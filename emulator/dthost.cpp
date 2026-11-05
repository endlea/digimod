// dthost: the Digitakt II engine (dt2's dtcore, OS 1.16) started the way its plug-in does
// (third_party/dt2 vst/DigitaktPlugin.cpp: startEngine, coldBoot): a saved machine if there is one,
// else the first session from the firmware alone -- cold boot, FORMAT +DRIVE, and the 16 starter
// sounds of StarterKit.hpp loaded onto the tracks (the factory samples are not on a fresh +Drive,
// so the factory project would be silent) -- cached, then the real-time engine.
// Built by emulator/build.sh into data/build/libfcdt.dylib together with
// emulator/host/audio_out.c; driven by emulator/run.py.
#include "dtcore.h"
#include "StarterKit.hpp"
extern "C" {
#include "sha256.h"                 // dtcore's SHA-256 (core/src), as the plug-in uses it
}

#include <cstdio>
#include <dlfcn.h>
#include <cstdlib>
#include <string>
#include <vector>

typedef void (*fcdt_log_fn)(const char* line);

static bool readFile(const std::string& p, std::vector<uint8_t>& out)
{
    FILE* f = std::fopen(p.c_str(), "rb");
    if (!f) return false;
    std::fseek(f, 0, SEEK_END);
    long n = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    out.resize(n > 0 ? size_t(n) : 0);
    bool ok = n > 0 && std::fread(out.data(), 1, out.size(), f) == out.size();
    std::fclose(f);
    return ok;
}

static bool writeFile(const std::string& p, const uint8_t* d, size_t n)
{
    std::string tmp = p + ".tmp";
    FILE* f = std::fopen(tmp.c_str(), "wb");
    if (!f) return false;
    bool ok = std::fwrite(d, 1, n, f) == n;
    ok = std::fclose(f) == 0 && ok;
    return ok && std::rename(tmp.c_str(), p.c_str()) == 0;
}

static void logf(fcdt_log_fn log, const std::string& s)
{
    if (log) log(s.c_str());
}

// first session from the firmware: cold boot + FORMAT +DRIVE + starter sounds (as DigitaktPlugin::coldBoot)
static int firstSession(const uint8_t* syx, size_t len, std::vector<uint8_t>& out, fcdt_log_fn log,
                        char* err, size_t errlen)
{
    dt_firmware fw;
    uint8_t* buf = nullptr;
    size_t blen = 0;
    if (dt_fw_extract(syx, len, &fw, err, errlen)) return -1;
    logf(log, "first start: booting the firmware (cold boot + FORMAT +DRIVE, about a minute)");
    int r = dt_coldboot_session_ex(&fw, DT_COLDBOOT_RUNG, DT_COLDBOOT_GUI, 1, DT_COLDBOOT_PLUSDRIVE, &buf, &blen,
                                   nullptr, err, errlen);
    dt_fw_free(&fw);
    if (r) return r;
    logf(log, "first start: loading the 16 starter sounds onto the tracks");
    std::vector<uint8_t> boot(buf, buf + blen);
    std::free(buf);
    std::string kerr;
    if (dtkit::apply(syx, len, boot, out, nullptr, kerr)) {
        std::snprintf(err, errlen, "starter sounds: %s", kerr.c_str());
        return -1;
    }
    return 0;
}

extern "C" {

// the real SHARC DSP (third_party/dt2 dt2_realdsp.inc.c): digi2's SHARC+ engine from `lib`
// (build/digitone2/dsp/libdndsp_interp.dylib: the interpreter, plus `native` if given) running the
// Digitakt's DSP program from its boot snapshot `dnds`. Takes effect for engines made afterwards.
static dt2_dsp_api g_api;
static const uint8_t* (*g_dsp_memory)(const void*, int, size_t*);
int fcdt_use_real_dsp(const char* lib, const char* native, const uint8_t* dnds, size_t dnds_len, char* err,
                      size_t errlen)
{
    void* h = dlopen(lib, RTLD_NOW | RTLD_LOCAL);
    if (!h) {
        std::snprintf(err, errlen, "%s", dlerror());
        return -1;
    }
    typedef void* (*new_fn)(const uint8_t*, size_t, char*, size_t);
    typedef int (*native_fn)(const char*, char*, size_t);
    new_fn mk = (new_fn)dlsym(h, "dn_dsp_new");
    native_fn nl = (native_fn)dlsym(h, "dn_dsp_native_load");
    g_api.frame = (int (*)(void*, const uint8_t*, size_t))dlsym(h, "dn_dsp_frame");
    g_api.pull = (int (*)(void*, int32_t*, int))dlsym(h, "dn_dsp_pull");
    g_api.reply = (uint32_t(*)(void*, uint8_t*, uint32_t))dlsym(h, "dn_dsp_reply");
    g_api.error = (const char* (*)(const void*))dlsym(h, "dn_dsp_error");
    g_api.lp_queue = (int (*)(void*, const uint32_t*, int))dlsym(h, "dn_dsp_lp_queue");
    g_dsp_memory = (const uint8_t* (*)(const void*, int, size_t*))dlsym(h, "dn_dsp_memory");
    if (!mk || !g_api.frame || !g_api.pull || !g_api.reply) {
        std::snprintf(err, errlen, "%s: not digi2's DSP library", lib);
        return -1;
    }
    if (native && *native && nl && nl(native, err, errlen))
        return -1;
    g_api.dsp = mk(dnds, dnds_len, err, errlen);
    if (!g_api.dsp) return -1;
    dt2_set_real_dsp(&g_api);
    return 0;
}

// debugging: the real DSP's memory (0 = L1, 1 = L2, 2 = DDR), NULL without it
const uint8_t* fcdt_dsp_memory(int which, size_t* len)
{
    return g_api.dsp && g_dsp_memory ? g_dsp_memory(g_api.dsp, which, len) : nullptr;
}

// -> the real-time engine; state_path: the saved machine (used if present), cache_dir: first-session cache
dt_rt* fcdt_open(const uint8_t* syx, size_t len, const char* state_path, const char* cache_dir, double rate,
                 uint32_t max_frames, fcdt_log_fn log, char* err, size_t errlen)
{
    std::vector<uint8_t> state;
    if (state_path && *state_path && readFile(state_path, state)) {
        logf(log, std::string("machine: ") + state_path);
        dt_rt* rt = dt_rt_new_mem(syx, len, state.data(), state.size(), rate, max_frames, err, errlen);
        if (rt) return rt;
        logf(log, std::string("saved machine not usable (") + err + "), starting from the first session");
        std::rename(state_path, (std::string(state_path) + ".bad").c_str());
    }
    dt_sha256 h;
    uint8_t md[32];
    dt_sha256_init(&h);
    dt_sha256_update(&h, syx, len);
    dt_sha256_final(&h, md);
    char name[96];
    std::snprintf(name, sizeof name, "/first-session-%02x%02x%02x%02x%02x%02x%02x%02x-kit%d.dtsz", md[0], md[1],
                  md[2], md[3], md[4], md[5], md[6], md[7], dtkit::kVersion);
    const std::string cache = std::string(cache_dir) + name;
    state.clear();
    if (std::getenv("FCDT_KIT_REAL_DSP") || !readFile(cache, state)) {
        if (!std::getenv("FCDT_KIT_REAL_DSP"))  // debugging: load the starter kit with the real DSP attached
            dt2_set_real_dsp(nullptr);          // the first session is made as dt2 makes it (stand-in synth)
        int r = firstSession(syx, len, state, log, err, errlen);
        if (g_api.dsp) dt2_set_real_dsp(&g_api);
        if (r) return nullptr;
        if (!std::getenv("FCDT_KIT_REAL_DSP") && !writeFile(cache, state.data(), state.size()))
            logf(log, "warning: cannot write " + cache);
    }
    if (std::getenv("FCDT_REAPPLY_KIT")) {      // debugging: load the starter kit onto the tracks again (real DSP)
        std::vector<uint8_t> again;
        std::string kerr;
        logf(log, "reloading the starter sounds onto the tracks");
        if (dtkit::apply(syx, len, state, again, nullptr, kerr)) {
            std::snprintf(err, errlen, "starter sounds: %s", kerr.c_str());
            return nullptr;
        }
        state.swap(again);
    }
    return dt_rt_new_mem(syx, len, state.data(), state.size(), rate, max_frames, err, errlen);
}

// the machine as it is now (projects, samples on the +Drive, settings) -> path; 0 = saved
int fcdt_save(dt_rt* rt, const char* path)
{
    uint8_t* buf = nullptr;
    size_t len = 0;
    if (dt_rt_save(rt, &buf, &len) || !buf) return -1;
    bool ok = writeFile(path, buf, len);
    std::free(buf);
    return ok ? 0 : -1;
}

}
