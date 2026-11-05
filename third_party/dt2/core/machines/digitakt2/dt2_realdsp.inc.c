/* digidive: the Digitakt II's real SHARC DSP instead of dt2's stand-in synth (dt2_audio.inc.c).
 *
 * dt2 never ran the DSP: it took the note and velocity out of each DSPI2 frame and played the
 * track's sample itself (no filters, envelopes, FX, START/LEN; zeros as the DSP's answer, so no
 * play position for the waveform view). Here the DSP program of section 7 runs in digi2's SHARC+
 * engine (interpreter or native), installed by the host before the engine starts
 * (dt2_set_real_dsp, digitakt2/engine/dthost.cpp):
 *   - the firmware's own DSPI2 send routine runs (no HLE): at each SERQ of channel 28 the frame
 *     goes to the DSP, which runs until it wants the next one;
 *   - SPI is full duplex, so what the ColdFire receives during transfer N is what the DSP queued
 *     after frame N-1 (as digitone2's DN_DSP_LAG);
 *   - the DSP's stereo output goes to the sink in 512-sample blocks: left as the mix, right in
 *     track 1's slot (the host plays outputs 0/1 as left/right, as for the Digitone II).
 * Still missing: the sample memory (pages the firmware writes to the FIFO at 0x8C000002), so for
 * now the DSP plays the tracks with empty sample memory. */

/* dt2_dsp_api: include/dtcore.h */
#include <pthread.h>

/* the DSP's own thread: frame N runs while the ColdFire goes on; its results are taken at transfer
   N+1 (rd_flush). DT2_DSP_SYNC=1 runs it in line. */
static pthread_t rd_th;
static pthread_mutex_t rd_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t rd_cv = PTHREAD_COND_INITIALIZER;
static int rd_started, rd_busy, rd_sync = -1;
static uint8_t rd_frame[0x2000];
static size_t rd_fn;
static uint32_t rd_rn;
static int rd_rc, rd_got;
static void rd_emit(dt_engine *e, const int32_t *pcm, int got);
static void rd_swap(uint8_t *out, const uint8_t *raw, uint32_t n);
static dt2_dsp_api g_rd;                         /* one Digitakt per process, like the plug-in */
static uint8_t rd_answer[0x2000], rd_raw[0x2000];
static int rd_have_answer;
static int32_t rd_pcm[2 * 4096];
static float rd_mix[512], rd_trk[16][512];
static size_t rd_fill;
static int64_t rd_frames, rd_errors;
static int rd_replayed;
static void rd_replay(dt_engine *e);                 /* dt2_audio.inc.c: the sample memory restored from the state */

/* one ColdFire transfer to the link-port bridge (FUN_400cd638): `cmd`, then the 4 KB `page` as the
   bridge packs it -- command words (cmd 0xFFFFFFFF: slot records) byte order kept, sample data with
   each 16-bit sample byte-swapped (the ColdFire sends b1 b0 b3 b2, the link port packs LSB first) */
static void rd_flush(dt_engine *e);
static void rd_lp_send(dt_engine *e, uint32_t cmd, const uint8_t *page)
{
    static uint32_t w[1025];
    int k, record = cmd == 0xFFFFFFFFu;
    if (!g_rd.dsp || !g_rd.lp_queue)
        return;
    rd_flush(e);                                     /* the DSP is idle while its memory is written */
    w[0] = cmd;
    for (k = 0; k < 1024; k++) {
        const uint8_t *p = page + 4 * k;
        w[1 + k] = record ? ((uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3])
                          : ((uint32_t)p[1] | (uint32_t)p[0] << 8 | (uint32_t)p[3] << 16 | (uint32_t)p[2] << 24);
    }
    /* slot record: slot, left, right, sample rate (the file's header +8), bytes per channel. Samples put on
       the +Drive by dt2 before the fix in dt_wav2dt carry their byte count as the rate (mono: = bytes per
       channel, stereo: twice it) and would play length/0.6 s times too fast: they are 48 kHz */
    if (record && w[5] && (w[4] == w[5] || w[4] == 2 * w[5]))
        w[4] = 48000;
    g_rd.lp_queue(g_rd.dsp, w, 1025);
}

DT_API void dt2_set_real_dsp(const dt2_dsp_api *api)
{
    if (api)
        g_rd = *api;
    else
        memset(&g_rd, 0, sizeof g_rd);
    rd_have_answer = 0;
    rd_fill = 0;
    rd_replayed = 0;
    rd_busy = 0;
}

static void rd_swap(uint8_t *out, const uint8_t *raw, uint32_t n)
{
    uint32_t k;
    for (k = 0; k + 1 < n; k += 2) {
        out[k] = raw[k + 1];
        out[k + 1] = raw[k];
    }
}

static void rd_emit(dt_engine *e, const int32_t *pcm, int got)
{
    e_audio *a = &e->au;
    int i;
    for (i = 0; i < got; i++) {
        rd_mix[rd_fill] = (float)((double)pcm[2 * i] * (1.0 / 2147483648.0)) * a->master * 4.0f;
        rd_trk[0][rd_fill] = (float)((double)pcm[2 * i + 1] * (1.0 / 2147483648.0)) * a->master * 4.0f;
        if (++rd_fill == 512) {
            a->samples += 512;
            if (a->sink)
                a->sink(a->sink_ctx, rd_mix, &rd_trk[0][0], 512);
            rd_fill = 0;
        }
    }
}

static void rd_run(void)                         /* one frame in the DSP: audio and the next answer */
{
    rd_rc = g_rd.frame(g_rd.dsp, rd_frame, rd_fn);
    rd_got = rd_rc < 0 ? 0 : g_rd.pull(g_rd.dsp, rd_pcm, 4096);
    if (rd_rn && rd_rc >= 0) {
        memset(rd_raw, 0, rd_rn);
        g_rd.reply(g_rd.dsp, rd_raw, rd_rn);
    }
}

static void *rd_main(void *arg)
{
    (void)arg;
    pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0);
    pthread_mutex_lock(&rd_mu);
    for (;;) {
        while (rd_busy != 1)
            pthread_cond_wait(&rd_cv, &rd_mu);
        pthread_mutex_unlock(&rd_mu);
        rd_run();
        pthread_mutex_lock(&rd_mu);
        rd_busy = 2;                                 /* done, results not taken yet */
        pthread_cond_broadcast(&rd_cv);
    }
    return NULL;
}

/* wait for the DSP's frame in flight and take its results (audio out, answer for the next transfer) */
static void rd_flush(dt_engine *e)
{
    if (!rd_busy)
        return;
    if (!rd_sync) {
        pthread_mutex_lock(&rd_mu);
        while (rd_busy != 2)
            pthread_cond_wait(&rd_cv, &rd_mu);
        pthread_mutex_unlock(&rd_mu);
    }
    rd_busy = 0;
    rd_frames++;
    if (rd_rc < 0) {
        if (rd_errors++ < 4)
            fprintf(stderr, "dt2 real DSP: frame %lld: %d %s\n", (long long)rd_frames, rd_rc,
                    g_rd.error ? g_rd.error(g_rd.dsp) : "");
        return;
    }
    rd_emit(e, rd_pcm, rd_got);
    if (rd_rn)
        rd_swap(rd_answer, rd_raw, rd_rn);
    if (rd_rn && getenv("DT2_RD_DUMP")) {            /* debugging: every answer, [u32 n][n bytes] */
        static FILE *f;
        if (!f)
            f = fopen(getenv("DT2_RD_DUMP"), "wb");
        if (f) {
            fwrite(&rd_rn, 4, 1, f);
            fwrite(rd_answer, 1, rd_rn, f);
        }
    }
}

/* the DSPI2 transfer with the real DSP: frame `words` (n bytes) out, answer into RX */
static void rd_transfer(dt_engine *e, const uint8_t *words, size_t n, uint32_t rx_dst, uint32_t rn)
{
    if (rd_sync < 0)
        rd_sync = getenv("DT2_DSP_SYNC") && !strcmp(getenv("DT2_DSP_SYNC"), "1");
    rd_flush(e);                                     /* frame N-1: its answer goes out now */
    if (rn > sizeof rd_answer)
        rn = sizeof rd_answer;
    if (!rd_have_answer && rn) {                 /* queued by the DSP's boot */
        memset(rd_raw, 0, rn);
        g_rd.reply(g_rd.dsp, rd_raw, rn);
        rd_swap(rd_answer, rd_raw, rn);
        rd_have_answer = 1;
    }
    if (rn)
        e_write(e, rx_dst, rd_answer, rn);
    if (!rd_replayed) {                          /* the DSP starts empty: give it the sample memory */
        rd_replayed = 1;
        rd_replay(e);
    }
    memcpy(rd_frame, words, n < sizeof rd_frame ? n : sizeof rd_frame);
    rd_fn = n < sizeof rd_frame ? n : sizeof rd_frame;
    if (getenv("DT2_FRAME_DUMP")) {                  /* debugging: every frame, [u32 n][n bytes] */
        static FILE *f;
        if (!f)
            f = fopen(getenv("DT2_FRAME_DUMP"), "wb");
        if (f) {
            fwrite(&rd_fn, 4, 1, f);
            fwrite(rd_frame, 1, rd_fn, f);
        }
    }
    rd_rn = rn;
    if (rd_sync) {
        rd_run();
        rd_busy = 2;
        return;
    }
    if (!rd_started) {
        rd_started = 1;
        pthread_create(&rd_th, NULL, rd_main, NULL);
    }
    pthread_mutex_lock(&rd_mu);
    rd_busy = 1;
    pthread_cond_broadcast(&rd_cv);
    pthread_mutex_unlock(&rd_mu);
}
