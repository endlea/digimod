"""SHARC+ DSP of the synth (DSPI2 CS1), run by the interpreter from digi2
(third_party/digi2/core/dsp, built into data/dsp/ by emulator/build_dsp.py).

    state = boot(section7_bytes)        # ADI boot stream -> DNDS snapshot (cached in build/dsp/)
    d = Dsp(state)
    d.frame(words_be)                   # one DSPI2 frame from the ColdFire (16-bit words, BE)
    pcm = d.pull()                      # [(l, r), ...] int32 samples produced by that frame
    d.reply(n)                          # n bytes for the ColdFire's RX buffer (already swapped)
"""
import ctypes
import hashlib
import os
import sys
import threading
import time


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # repository root
DSP = os.path.join(ROOT, "data", "dsp")
LIB = os.path.join(DSP, "libdndsp_interp.dylib")      # emulator/build_dsp.py
NATIVE = ""                                           # set per DSP section (data/dsp/<sha>/libdndsp_native.dylib)
BOOT_CYCLES = 2_000_000_000            # digi2 core/src/dn_prepare.c DSP_BOOT_CYCLES

_lib = None


def lib():
    global _lib
    if _lib is None:
        if not os.path.exists(LIB):
            raise RuntimeError(f"{LIB} missing: run emulator/build_dsp.py")
        L = ctypes.CDLL(LIB)
        vp, sz, u8p = ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint8)
        PROG = ctypes.CFUNCTYPE(None, vp, ctypes.c_int, ctypes.c_char_p)
        L.dn_dsp_boot.argtypes = [ctypes.c_char_p, sz, ctypes.c_int64, vp, PROG, vp,
                                  ctypes.POINTER(u8p), ctypes.POINTER(sz), ctypes.c_char_p, sz]
        L.dn_dsp_new.argtypes = [ctypes.c_char_p, sz, ctypes.c_char_p, sz]
        L.dn_dsp_new.restype = vp
        L.dn_dsp_free.argtypes = [vp]
        L.dn_dsp_frame.argtypes = [vp, ctypes.c_char_p, sz]
        L.dn_dsp_pull.argtypes = [vp, ctypes.POINTER(ctypes.c_int32), ctypes.c_int]
        L.dn_dsp_reply.argtypes = [vp, ctypes.c_char_p, ctypes.c_uint32]
        L.dn_dsp_reply.restype = ctypes.c_uint32
        L.dn_dsp_error.argtypes = [vp]
        L.dn_dsp_error.restype = ctypes.c_char_p
        L.dn_dsp_cycles.argtypes = [vp]
        L.dn_dsp_cycles.restype = ctypes.c_int64
        L.dn_dsp_native_load.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
        L.dn_dsp_backend_name.restype = ctypes.c_char_p
        L._PROG = PROG
        _lib = L
    return _lib


def boot(sec7, cache_dir=DSP, log=print):
    """DNDS state after booting the DSP program; cached by the section's SHA-256."""
    key = hashlib.sha256(sec7).hexdigest()[:16]
    path = os.path.join(cache_dir, f"dsp_{key}.dnds")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    L = lib()
    last = [-1]

    def progress(_ctx, pct, what):
        if pct // 10 != last[0]:
            last[0] = pct // 10
            log(f"DSP boot {pct}% {what.decode(errors='replace') if what else ''}")
    cb = L._PROG(progress)
    out = ctypes.POINTER(ctypes.c_uint8)()
    n = ctypes.c_size_t()
    err = ctypes.create_string_buffer(512)
    t = time.time()
    r = L.dn_dsp_boot(sec7, len(sec7), BOOT_CYCLES, None, cb, None, ctypes.byref(out), ctypes.byref(n),
                      err, len(err))
    if r:
        raise RuntimeError(f"DSP boot failed ({r}): {err.value.decode(errors='replace')}")
    state = ctypes.string_at(out, n.value)
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "wb") as f:
        f.write(state)
    log(f"DSP boot done in {time.time() - t:.1f}s, state {len(state)} bytes -> {path}")
    return state


class Dsp:
    def __init__(self, state, native=None):
        L = self.L = lib()
        err = ctypes.create_string_buffer(512)
        if native is None:
            native = os.environ.get("FC_DSP", "native") != "interp"
        if native and os.path.exists(NATIVE) and L.dn_dsp_native_load(NATIVE.encode(), err, len(err)):
            raise RuntimeError(f"native DSP: {err.value.decode(errors='replace')}")
        self.backend = L.dn_dsp_backend_name().decode()
        self.h = L.dn_dsp_new(state, len(state), err, len(err))
        if not self.h:
            raise RuntimeError(f"DSP state rejected: {err.value.decode(errors='replace')}")
        self.pcm = (ctypes.c_int32 * 8192)()
        self.frames = 0

    def frame(self, data):
        self.frames += 1
        r = self.L.dn_dsp_frame(self.h, bytes(data), len(data))
        if r < 0:
            raise RuntimeError(f"DSP frame {self.frames}: {r} {self.L.dn_dsp_error(self.h).decode(errors='replace')}")
        return r

    def pull(self):
        got = self.L.dn_dsp_pull(self.h, self.pcm, 4096)
        return memoryview(self.pcm).cast("B")[:got * 8], got

    def reply(self, n):
        raw = ctypes.create_string_buffer(n + 2)
        self.L.dn_dsp_reply(self.h, raw, n)
        b = bytearray(raw.raw[:n])
        b[0:n & ~1:2], b[1:n & ~1:2] = b[1:n & ~1:2], b[0:n & ~1:2]   # 16-bit words swapped for the ColdFire
        return bytes(b)

    def cycles(self):
        return self.L.dn_dsp_cycles(self.h)


if __name__ == "__main__":
    sec7 = open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(DEV, "firmware", "os_260930", "section_7_blob.bin"), "rb").read()
    st = boot(sec7)
    d = Dsp(st)
    print("state ok, cycles", d.cycles())


def _realtime_thread():
    """macOS: keep the calling thread on the performance cores (as digi2's DSP thread)."""
    if sys.platform == "darwin":
        try:
            libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            libc.pthread_set_qos_class_self_np(0x21, 0)          # QOS_CLASS_USER_INTERACTIVE
        except (OSError, AttributeError):
            pass


class Dspi2Link:
    """DSPI2 chip select 1 as MAIN_OS drives it after the DSP has booted (digi2
    core/src/dn_dspi.inc.c): eDMA channel 29 pushes one frame of 32-bit PUSHR words into
    DSPI2, channel 28 pops what the DSP shifts out meanwhile. SERQ of channel 28 starts the
    pair; the whole frame goes to the DSP at once, which runs until it wants the next frame.

    SPI is full duplex, so what the ColdFire receives during transfer N is what the DSP
    queued after frame N-1 (digi2 hands over the answer to frame N itself). That gives the
    DSP a whole audio block to run, in its own thread (ctypes releases the GIL), alongside
    the ColdFire: at transfer N the answer to N-1 goes into RX, frame N goes to the DSP.
    """
    RATE = 48000

    def __init__(self, edma, dsp, out, max_seconds=600, threaded=True):
        self.edma, self.dsp, self.out = edma, dsp, out
        self.frames = 0
        self.dsp_seconds = 0.0              # wall time spent inside the DSP
        self.wait_seconds = 0.0             # wall time the ColdFire waited for the DSP
        self.audio = bytearray()            # int32 LE stereo
        self.max_bytes = max_seconds * self.RATE * 8
        self.listeners = []                 # callables(pcm_bytes, n_frames) e.g. a live audio output
        self.busy = False                   # the DSP is working on a frame
        self.done = 0                       # frames the DSP has finished
        self.job = None
        self.result = None
        self.answer = b""                   # what the DSP will shift out during the next transfer
        self.threaded = threaded
        if threaded:
            self.cv = threading.Condition()
            threading.Thread(target=self._worker, daemon=True, name="dsp").start()
        edma.on_serq[28] = self.transfer
        edma.requests[15] = lambda: True    # DSPI1 tail of each frame (to nothing), keeps its IRQ flowing

    def attach(self, uc):
        pass

    def _tcd(self, ch, off, size):
        return self.edma._f(ch, off, size)

    def _run(self, words, rn):
        t = time.perf_counter()
        self.dsp.frame(words)
        pcm, got = self.dsp.pull()
        answer = self.dsp.reply(rn) if rn else b""
        return bytes(pcm), got, answer, time.perf_counter() - t

    def _worker(self):
        _realtime_thread()
        while True:
            with self.cv:
                while self.job is None:
                    self.cv.wait()
                words, rn = self.job
            try:
                res = self._run(words, rn)
            except Exception as e:             # reported by flush() in the emulation thread
                res = e
            with self.cv:
                self.job, self.result = None, res
                self.cv.notify_all()

    def flush(self):
        """Wait for the DSP's current frame and keep its results (audio, next answer)."""
        if not self.busy:
            return
        if self.threaded:
            t = time.perf_counter()
            with self.cv:
                while self.result is None:
                    self.cv.wait()
                res, self.result = self.result, None
            self.wait_seconds += time.perf_counter() - t
        else:
            res, self.result = self.result, None
        self.busy = False
        if isinstance(res, Exception):
            raise res
        pcm, got, self.answer, dt = res
        self.dsp_seconds += dt
        self.done += 1
        if self.done == 1:
            pcm = bytes(len(pcm))           # the first block after boot carries two stray full-scale samples
        if got:
            if len(self.audio) < self.max_bytes:
                self.audio += pcm
            for f in self.listeners:
                f(pcm, got)

    def transfer(self):
        e = self.edma
        self.flush()                        # the previous frame: its answer goes out now
        count = self._tcd(29, 0x14, 2) & 0x7FFF
        if not count:
            return
        raw = e.mem_read(self._tcd(29, 0, 4), count * 4)
        n = count
        for i in range(count):
            if raw[4 * i] & 0x08:           # EOQ
                n = i + 1
                break
        words = b"".join(raw[4 * i + 2:4 * i + 4] for i in range(n))
        self.frames += 1
        rn = (self._tcd(28, 0x14, 2) & 0x7FFF) * 2
        if self.frames == 1:
            self.out("DSP", f"first DSPI2 frame: {len(words)} bytes")
            self.answer = self.dsp.reply(rn) if rn else b""    # queued by the DSP's boot
        if rn:
            e.mem_write(self._tcd(28, 0x10, 4), self.answer[:rn].ljust(rn, b"\0"))
        self.busy = True
        if self.threaded:
            with self.cv:
                self.job, self.result = (words, rn), None
                self.cv.notify_all()
        else:
            self.result = self._run(words, rn)
        for ch in (29, 28):                 # major loops done: CITER = BITER, DONE, not ACTIVE
            e._set(ch, 0x14, 2, e._f(ch, 0x1C, 2))
            e._set(ch, 0x1E, 2, (e._f(ch, 0x1E, 2) | 0x80) & ~0x40)

    def wav(self, path):
        self.flush()
        n = len(self.audio) // 8 * 8
        if not n:
            return 0
        import struct
        hdr = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + n, b"WAVE", b"fmt ", 16, 1, 2,
                          self.RATE, self.RATE * 8, 8, 32, b"data", n)
        with open(path, "wb") as f:
            f.write(hdr + self.audio[:n])
        return n / (self.RATE * 8)
