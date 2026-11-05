#!/usr/bin/env python3
"""The Digitakt II's real SHARC DSP for one firmware's DSP section (section 7), native.

    emulator/build_dsp.py <section_7_blob.bin>      (~10 min the first time, ~4 min for another section)

0. digi2's SHARC+ interpreter with a 512 MB DDR (the Digitakt's sample memory starts at 0x84000000), shared by
   all sections -> data/dsp/libdndsp_interp.dylib;
1. the DSP program booted by it -> data/dsp/<sha>/dsp_<sha>.dnds (<sha>: first 16 hex of the section's SHA-256);
2. translated to C (dn_dsp_gensrc) with the FreeRTOS idle loop of OS 1.16's DSP program skipped (DN_GEN_IDLE:
   loop 0xb863cf, xTasksWaiting 0x2ca3e4, ready list 0x2ca2e8; another DSP program just runs its idle time);
3. compiled in parallel and linked -> data/dsp/<sha>/libdndsp_native.dylib. emulator/run.py uses it when present,
   else the interpreter (slower than real time).
"""
import concurrent.futures
import ctypes
import os
import shutil
import subprocess
import sys
import time

import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))                      # emulator/
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "third_party", "digi2", "core", "dsp")
DSP = os.path.join(ROOT, "data", "dsp")
IDLE = "0xb863cf,0x2ca3e4,0x2ca2e8"
KEY = "fwchaos-dt2nat16"                     # exactly 16 chars (dn_dsp.c compares 16 bytes)
DDR = "-DDN_DDR_SIZE=0x20000000u"           # 512 MB: 0x80000000..0xA0000000
BASE = ["-O2", "-ffp-contract=off", "-fno-fast-math", "-fno-stack-protector", "-fomit-frame-pointer",
        "-fno-math-errno", "-w", "-fPIC", DDR]
FLAGS = BASE + ["-DDN_FIXED_IDLE", "-DDN_EXT_INFLATE"]
INTERP = ["dn_interp.c", "dn_exec_simd.c", "dn_exec_nosimd.c", "dn_jit.c", "dn_jit_simd.c", "dn_dspback.c"]
DIGI2 = os.path.join(ROOT, "third_party", "digi2")


def sec7_dir(sec7):
    """data/dsp/<sha>: where a DSP section's boot snapshot and native library go."""
    return os.path.join(DSP, hashlib.sha256(sec7).hexdigest()[:16])


def build_interp():
    lib = os.path.join(DSP, "libdndsp_interp.dylib")
    obj = os.path.join(DSP, "obj_interp")
    os.makedirs(obj, exist_ok=True)
    inc = ["-I", SRC, "-I", os.path.join(SRC, "rt"), "-I", os.path.join(DIGI2, "core", "include"),
           "-I", os.path.join(DIGI2, "dtcore", "include")]
    t = time.time()
    with concurrent.futures.ThreadPoolExecutor(len(INTERP)) as ex:
        runs = ex.map(lambda f: subprocess.run(["cc", *BASE, *inc, "-c", "-o", os.path.join(obj, f[:-2] + ".o"),
                                               os.path.join(SRC, f)], capture_output=True, text=True), INTERP)
        for f, r in zip(INTERP, runs):
            if r.returncode:
                sys.exit(f"{f}: {r.stderr[-2000:]}")
    subprocess.run(["cc", "-dynamiclib", "-o", lib, *[os.path.join(obj, f[:-2] + ".o") for f in INTERP], "-lz"], check=True)
    print(f"interpreter (512 MB DDR) in {time.time() - t:.0f}s: {lib}")
    return lib


# emulator/dsp_seeds/<DSP section folder>.txt: hex addresses of code reached only through pointers, one per line
# (DN_GEN_RANGES in dn_gensrc.inc); without them a jump there stops the native DSP ("rt_run inatteso: 3")


def main():
    if len(sys.argv) < 2 or not os.path.isfile(sys.argv[1]):
        sys.exit(__doc__)
    sec7 = open(sys.argv[1], "rb").read()
    OUT = sec7_dir(sec7)
    os.environ["DN_GEN_IDLE"] = IDLE
    seeds = os.path.join(HERE, "dsp_seeds", os.path.basename(OUT) + ".txt")
    if os.path.exists(seeds):                # DSP code a mod added that only pointers reach (its labels)
        os.environ["DN_GEN_RANGES"] = ",".join(f"{a}-{a}" for a in (x.strip() for x in open(seeds))
                                               if a and not a.startswith("#"))
    sys.path.insert(0, os.path.join(HERE, "fw"))
    import sharc
    os.makedirs(OUT, exist_ok=True)
    sharc.LIB = os.path.join(DSP, "libdndsp_interp.dylib")
    if not os.path.exists(sharc.LIB):
        build_interp()
    state = sharc.boot(sec7, cache_dir=OUT)
    L = sharc.lib()
    src = os.path.join(OUT, "native_src")
    shutil.rmtree(src, ignore_errors=True)
    os.makedirs(src)
    names = []
    SINK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t)

    def sink(_c, name, data, n):
        with open(os.path.join(src, name.decode()), "wb") as f:
            f.write(ctypes.string_at(data, n))
        names.append(name.decode())
        return 0
    cb = SINK(sink)
    L.dn_dsp_gensrc.argtypes = [ctypes.c_char_p, ctypes.c_size_t, SINK, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    err = ctypes.create_string_buffer(512)
    if L.dn_dsp_gensrc(state, len(state), cb, None, err, len(err)):
        sys.exit(f"translation failed: {err.value.decode(errors='replace')}")
    print(f"translated to {len(names)} C files")
    for f in ("rt/rt.h", "rt/periph.h", "dn_dsp.h", "dn_dsp.c", "dn_rtmain.c"):
        shutil.copy(os.path.join(SRC, f), src)
    with open(os.path.join(src, "dn_key.h"), "w") as f:
        f.write(f'#define DN_DSP_KEY "{KEY}"\n')
    obj = os.path.join(OUT, "obj_native")
    shutil.rmtree(obj, ignore_errors=True)
    os.makedirs(obj)
    units = ["dn_rtmain.c", "dn_dsp.c"] + [n for n in names if n != "dsp_main.c"]
    t = time.time()
    with concurrent.futures.ThreadPoolExecutor(max(1, (os.cpu_count() or 2) - 1)) as ex:
        runs = ex.map(lambda u: subprocess.run(["cc", *FLAGS, "-I", src, "-c", "-o", os.path.join(obj, u[:-2] + ".o"),
                                               os.path.join(src, u)], capture_output=True, text=True), units)
        for u, r in zip(units, runs):
            if r.returncode:
                sys.exit(f"{u}: {r.stderr[-2000:]}")
    print(f"compiled {len(units)} files in {time.time() - t:.0f}s")
    lib = os.path.join(OUT, "libdndsp_native.dylib")
    subprocess.run(["cc", "-dynamiclib", "-o", lib, *[os.path.join(obj, u[:-2] + ".o") for u in units]], check=True)
    print("native DSP:", lib)


if __name__ == "__main__":
    main()
