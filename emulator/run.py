#!/usr/bin/env python3
"""The Digitakt II emulator on one firmware: dt2's C engine (real time) with the real SHARC DSP.

    .venv/bin/python emulator/run.py --syx F.syx --sec7 S7.bin --machine M.dtsz --cache DIR --serve 8770
    .venv/bin/python emulator/run.py ... --render 8 --input 1:key:19:down --input 1.3:key:19:up

--syx is the firmware in the engines' form (emulator/fw/mksyx.py from its sections; digimod's server makes it from
an official or a built .syx), --sec7 its DSP section. A modified MAIN OS runs with the address profile of the OS
it was made from: DT_BASE_MAIN_SHA256=<that OS's MAIN OS SHA-256>. The first start of a firmware boots it,
formats the +Drive and loads dt2's 16 starter sounds (about 30 s, cached in --cache); the machine as you leave
it is kept in --machine (saved every 2 minutes and on exit). Panel, HTTP API and the other options:
emulator/host/engine_host.py. Needs data/build/libfcdt.dylib (emulator/build.sh) and the DSP
(emulator/build_dsp.py, else the interpreter: slower than real time).
"""
import ctypes
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))                      # emulator/
ROOT = os.path.dirname(HERE)                                           # repository root
sys.path.insert(0, os.path.join(HERE, "host"))
sys.path.insert(0, os.path.join(HERE, "fw"))
import engine_host  # noqa: E402

DATA = os.path.join(ROOT, "data")
PANEL = os.path.join(ROOT, "third_party", "dt2", "panel")
# the real SHARC DSP (filters, envelopes, FX, play position): digi2's SHARC+ engine running the firmware's
# DSP program (section 7); the interpreter library loads the native one of that section when it was built
DSP_INTERP = os.path.join(DATA, "dsp", "libdndsp_interp.dylib")   # 512 MB DDR (emulator/build_dsp.py)
STAND_IN = "--stand-in" in sys.argv                 # dt2's own sample player instead of the DSP


def _arg(name):
    """--name VALUE from the command line (read before engine_host parses it: the paths are needed at import)."""
    if name in sys.argv[:-1]:
        return sys.argv[sys.argv.index(name) + 1]
    return None


SYX, SEC7, MACHINE, CACHE = _arg("--syx"), _arg("--sec7"), _arg("--machine"), _arg("--cache")


def _real_dsp(L, log):
    import sharc
    sharc.LIB = DSP_INTERP
    sec7 = open(SEC7, "rb").read()
    where = os.path.join(DATA, "dsp", hashlib.sha256(sec7).hexdigest()[:16])      # build_dsp.sec7_dir
    os.makedirs(where, exist_ok=True)
    dnds = sharc.boot(sec7, cache_dir=where, log=lambda m: None)
    native = os.path.join(where, "libdndsp_native.dylib")
    native = native if os.path.exists(native) and os.environ.get("FC_DSP") != "interp" else ""
    L.fcdt_use_real_dsp.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t,
                                    ctypes.c_char_p, ctypes.c_size_t]
    err = ctypes.create_string_buffer(512)
    if L.fcdt_use_real_dsp(DSP_INTERP.encode(), native.encode(), dnds, len(dnds), err, len(err)):
        raise RuntimeError(f"real DSP: {err.value.decode(errors='replace')}")
    log(f"digitakt2: real DSP ({'native' if native else 'interpreter, slower than real time'})".encode())


def _open(L, syx, rate, frames, log, err):
    L.fcdt_open.restype = ctypes.c_void_p
    L.fcdt_open.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_double,
                            ctypes.c_uint32, type(log), ctypes.c_char_p, ctypes.c_size_t]
    os.makedirs(CACHE, exist_ok=True)
    if "--fresh" in sys.argv and os.path.exists(MACHINE):
        os.replace(MACHINE, MACHINE + ".old")
    if not STAND_IN:
        _real_dsp(L, log)
    return L.fcdt_open(syx, len(syx), MACHINE.encode(), CACHE.encode(), rate, frames,
                       log, err, len(err))


def _save(L, rt):
    L.fcdt_save.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    return L.fcdt_save(rt, MACHINE.encode())


DEVICE = engine_host.Device(
    name="Digitakt II", key="digitakt2",
    lib=os.path.join(DATA, "build", "libfcdt.dylib"), syx=SYX or "",
    panel_png=os.path.join(PANEL, "digitakt2_panel.png"), panel_json=os.path.join(PANEL, "digitakt2_panel.json"),
    open=_open, n_leds=50,
    special_keys={49: 49, 48: 50},              # dt2 wire_for(): codes 49/50 on matrix row 6, bits 1/0
    save=_save, drive=True, stereo=not STAND_IN,             # dt2's stand-in: output 1 is track 1, not the right channel
    port=8766)


def _extra(p):
    p.add_argument("--stand-in", action="store_true",
                   help="dt2's own sample player instead of the real DSP (no filters, envelopes, FX)")
    p.add_argument("--fresh", action="store_true",
                   help="forget the saved machine (kept as .old) and start from the first session")
    p.add_argument("--syx", metavar="PATH", required=True, help="the firmware in the engines' form (emulator/fw/mksyx.py)")
    p.add_argument("--sec7", metavar="PATH", required=True, help="its DSP section (section_7_blob.bin)")
    p.add_argument("--machine", metavar="PATH", required=True, help="the saved machine to use and keep")
    p.add_argument("--cache", metavar="DIR", required=True, help="first-session cache")


if __name__ == "__main__":
    sys.exit(engine_host.main(DEVICE, extra_args=_extra))
