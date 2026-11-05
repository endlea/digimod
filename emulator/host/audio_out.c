/* System audio output of the emulator (emulator/run.py): a CoreAudio
 * default output unit whose render callback pulls dt_rt_render directly, as a plug-in host does --
 * no Python, browser or HTTP on the audio path. Compiled into each device's host library against
 * that engine's dtcore.h (the dt_rt API is the same in digi2 and dt2). */
#include "dtcore.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <AudioToolbox/AudioToolbox.h>
#include <CoreAudio/CoreAudio.h>

static AudioComponentInstance g_unit;
static dt_rt *g_rt;
static uint32_t g_chunk = 512;                   /* frames per dt_rt_render call (<= the engine's max_frames) */
static int g_stereo = 1;                         /* engine output 1 = right channel (digi2); 0: mono mix to both */
static volatile uint32_t g_max_req, g_calls;     /* biggest buffer CoreAudio asked for, callbacks so far */
static float *g_tap;                             /* fc_audio_tap: a copy of what went to the device (L/R interleaved) */
static volatile size_t g_tap_n, g_tap_cap;

static OSStatus fc_render_cb(void *ref, AudioUnitRenderActionFlags *flags, const AudioTimeStamp *ts,
                             UInt32 bus, UInt32 frames, AudioBufferList *io)
{
    float *outs[2];
    UInt32 done = 0;
    (void)ref; (void)flags; (void)ts; (void)bus;
    if (frames > g_max_req)
        g_max_req = frames;
    g_calls++;
    while (done < frames) {
        UInt32 n = frames - done > g_chunk ? g_chunk : frames - done;
        outs[0] = (float *)io->mBuffers[0].mData + done;
        outs[1] = (float *)io->mBuffers[1].mData + done;
        if (g_stereo) {
            dt_rt_render(g_rt, n, outs, 2, 0);
        } else {                                 /* dt2: output 1 is track 1 alone, not the right channel */
            dt_rt_render(g_rt, n, outs, 1, 0);
            memcpy(outs[1], outs[0], n * sizeof(float));
        }
        done += n;
    }
    if (g_tap) {
        UInt32 i;
        for (i = 0; i < frames && g_tap_n + 2 <= g_tap_cap; i++) {
            g_tap[g_tap_n++] = ((float *)io->mBuffers[0].mData)[i];
            g_tap[g_tap_n++] = ((float *)io->mBuffers[1].mData)[i];
        }
    }
    return noErr;
}

/* debugging: keep a copy of the next `seconds` of output; fc_audio_tap_get returns the frames kept */
void fc_audio_tap(double seconds, double rate)
{
    size_t cap = (size_t)(seconds * rate) * 2;
    float *t = (float *)calloc(cap, sizeof(float));
    g_tap_n = 0;
    g_tap_cap = cap;
    g_tap = t;
}

size_t fc_audio_tap_get(float *out, size_t max_frames)
{
    size_t n = g_tap_n / 2 < max_frames ? g_tap_n / 2 : max_frames;
    if (g_tap)
        memcpy(out, g_tap, n * 2 * sizeof(float));
    return n;
}

/* the default output device's nominal sample rate (0 if unknown): the engine is made at this rate,
   so CoreAudio never converts (digi2/dt2 resample internally from 48 kHz, as for a DAW) */
double fc_audio_device_rate(void)
{
    AudioObjectPropertyAddress a = {kAudioHardwarePropertyDefaultOutputDevice, kAudioObjectPropertyScopeGlobal,
                                    kAudioObjectPropertyElementMain};
    AudioDeviceID dev = 0;
    Float64 rate = 0;
    UInt32 sz = sizeof dev;
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &sz, &dev) || !dev)
        return 0;
    a.mSelector = kAudioDevicePropertyNominalSampleRate;
    sz = sizeof rate;
    if (AudioObjectGetPropertyData(dev, &a, 0, NULL, &sz, &rate))
        return 0;
    return rate;
}

void fc_audio_stats(uint32_t *max_req, uint32_t *calls)
{
    *max_req = g_max_req;
    *calls = g_calls;
}

/* 0 = playing. rate: the engine's rate (dt_rt_new*), chunk: its max_frames, stereo: engine output 1 is
   the right channel (digi2's Digitone) rather than track 1 (dt2's Digitakt) */
int fc_audio_start(dt_rt *rt, double rate, uint32_t chunk, int stereo, char *err, size_t errlen)
{
    AudioComponentDescription d = {kAudioUnitType_Output, kAudioUnitSubType_DefaultOutput,
                                   kAudioUnitManufacturer_Apple, 0, 0};
    AudioComponent c = AudioComponentFindNext(NULL, &d);
    AudioStreamBasicDescription f = {0};
    AURenderCallbackStruct cb = {fc_render_cb, NULL};
    UInt32 maxf = 8192;                          /* any device buffer; split into `chunk`-sized engine calls */
    OSStatus s;
    if (g_unit)
        return 0;
    g_rt = rt;
    g_chunk = chunk ? chunk : 512;
    g_stereo = stereo;
    if (!c || AudioComponentInstanceNew(c, &g_unit)) {
        snprintf(err, errlen, "no default audio output");
        return -1;
    }
    f.mSampleRate = rate;
    f.mFormatID = kAudioFormatLinearPCM;
    f.mFormatFlags = kAudioFormatFlagsNativeFloatPacked | kAudioFormatFlagIsNonInterleaved;
    f.mChannelsPerFrame = 2;
    f.mBitsPerChannel = 32;
    f.mFramesPerPacket = 1;
    f.mBytesPerFrame = 4;
    f.mBytesPerPacket = 4;
    s = AudioUnitSetProperty(g_unit, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Input, 0, &f, sizeof f);
    if (!s) s = AudioUnitSetProperty(g_unit, kAudioUnitProperty_SetRenderCallback, kAudioUnitScope_Input, 0, &cb, sizeof cb);
    if (!s) s = AudioUnitSetProperty(g_unit, kAudioUnitProperty_MaximumFramesPerSlice, kAudioUnitScope_Global, 0, &maxf, sizeof maxf);
    if (!s) s = AudioUnitInitialize(g_unit);
    if (!s) s = AudioOutputUnitStart(g_unit);
    if (s) {
        snprintf(err, errlen, "CoreAudio error %d", (int)s);
        AudioComponentInstanceDispose(g_unit);
        g_unit = NULL;
        return -1;
    }
    return 0;
}

void fc_audio_stop(void)
{
    if (!g_unit)
        return;
    AudioOutputUnitStop(g_unit);
    AudioUnitUninitialize(g_unit);
    AudioComponentInstanceDispose(g_unit);
    g_unit = NULL;
}
