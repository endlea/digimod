"""The standalone host's extras around a running engine (host/engine_host.py):

    Settings  data/settings/<device>.json: Main Volume and the MIDI inputs chosen, kept between runs
    Midi      CoreMIDI sources -> the machine's MIDI IN (host/midi_in.c)
    Drive     the +Drive (dt2's dt_drive_* RPC, as its plug-in's +DRIVE tab, vst/DriveModel.hpp):
              list, make folders, delete, and upload any audio file -- macOS's afconvert turns
              AIFF/MP3/M4A/FLAC/... into WAV, dt_wav2dt into the firmware's sample format
"""
import ctypes
import json
import os
import subprocess
import tempfile
import threading

FACTORY = "/factory"                         # read-only on the machine


class Settings:
    def __init__(self, path):
        self.path = path
        try:
            with open(path) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def get(self, k, default=None):
        return self.data.get(k, default)

    def set(self, k, v):
        self.data[k] = v
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path + ".tmp", "w") as f:
            json.dump(self.data, f, indent=1)
        os.replace(self.path + ".tmp", self.path)


class Midi:
    def __init__(self, L, rt, settings, log=print):
        self.L, self.rt, self.settings, self.log = L, rt, settings, log
        L.fc_midi_name.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t]
        L.fc_midi_select.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_char_p, ctypes.c_size_t]
        L.fc_midi_connected.restype = ctypes.c_uint64
        L.fc_midi_stats.restype = ctypes.c_uint32
        L.fc_midi_stats.argtypes = [ctypes.c_char_p]
        self.lock = threading.Lock()
        # chosen inputs by name (sources renumber when devices come and go); default: all of them
        self.apply(self.settings.get("midi_inputs"))

    def names(self):
        b = ctypes.create_string_buffer(128)
        out = []
        for i in range(self.L.fc_midi_count()):
            self.L.fc_midi_name(i, b, len(b))
            out.append(b.value.decode(errors="replace"))
        return out

    def apply(self, chosen=None):
        """Connect the sources named in `chosen` (None: all)."""
        with self.lock:
            names = self.names()
            mask = 0
            for i, n in enumerate(names[:64]):
                if chosen is None or n in chosen:
                    mask |= 1 << i
            err = ctypes.create_string_buffer(256)
            if self.L.fc_midi_select(self.rt, mask, err, len(err)):
                self.log(f"MIDI: {err.value.decode()}")
            on = [n for i, n in enumerate(names) if mask >> i & 1]
            self.log(f"MIDI in: {', '.join(on) if on else 'none'}")

    def state(self):
        names = self.names()
        conn = self.L.fc_midi_connected()
        last = ctypes.create_string_buffer(3)
        events = self.L.fc_midi_stats(last)
        return {"inputs": [{"name": n, "on": bool(conn >> i & 1)} for i, n in enumerate(names)],
                "events": events, "last": list(last.raw)}

    def set(self, name, on):
        names = self.names()
        conn = self.L.fc_midi_connected()
        chosen = {n for i, n in enumerate(names) if conn >> i & 1}
        (chosen.add if on else chosen.discard)(name)
        self.settings.set("midi_inputs", sorted(chosen))
        self.apply(chosen)


class DriveEntry(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char * 64), ("type", ctypes.c_char), ("protected_", ctypes.c_uint8),
                ("size", ctypes.c_uint32), ("hash", ctypes.c_uint32)]


class Drive:
    def __init__(self, L, rt, health=None, log=print):
        self.L, self.rt = L, rt
        self.health = health or (lambda: {})  # () -> what the engine and the audio output are doing
        self.log = log
        P = ctypes.POINTER
        L.dt_drive_ls.argtypes = [ctypes.c_void_p, ctypes.c_char_p, P(P(DriveEntry)), P(ctypes.c_size_t),
                                  ctypes.c_char_p, ctypes.c_size_t]
        for f in (L.dt_drive_mkdir, L.dt_drive_rmdir, L.dt_drive_rm):
            f.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
        L.dt_drive_put.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t,
                                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        L.dt_wav2dt.argtypes = [ctypes.c_char_p, ctypes.c_size_t, P(P(ctypes.c_uint8)), P(ctypes.c_size_t),
                                ctypes.c_char_p, ctypes.c_size_t]
        self.libc = ctypes.CDLL(None)
        self.libc.free.argtypes = [ctypes.c_void_p]
        self.lock = threading.Lock()           # one +Drive operation at a time (they are RPCs to the firmware)

    @staticmethod
    def _check(r, err):
        if r:
            raise RuntimeError(err.value.decode(errors="replace") or f"error {r}")

    def _call(self, what, fn):
        """Run one +Drive RPC (fn() -> (result, err buffer)); on a timeout log the engine's state
        and try once more: the firmware only answers while the engine runs, which it does as long
        as the audio output pulls it."""
        for attempt in (1, 2):
            with self.lock:
                r, err = fn()
            if not r:
                return
            msg = err.value.decode(errors="replace")
            if "did not answer" not in msg or attempt == 2:
                raise RuntimeError(msg or f"{what}: error {r}")
            self.log(f"+Drive: {msg}; engine {self.health()}; retrying")

    def ls(self, path):
        out = ctypes.POINTER(DriveEntry)()
        n = ctypes.c_size_t()

        def call():
            err = ctypes.create_string_buffer(256)
            return self.L.dt_drive_ls(self.rt, path.encode(), ctypes.byref(out), ctypes.byref(n), err, len(err)), err
        self._call("ls", call)
        items = [{"name": out[i].name.decode(errors="replace"), "dir": out[i].type == b"D",
                  "protected": bool(out[i].protected_), "size": out[i].size} for i in range(n.value)]
        if out:
            self.libc.free(out)
        items.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        return items

    def mkdir(self, path):
        def call():
            err = ctypes.create_string_buffer(256)
            return self.L.dt_drive_mkdir(self.rt, path.encode(), err, len(err)), err
        self._call("mkdir", call)

    def rm(self, path, is_dir):
        if path == FACTORY or path.startswith(FACTORY + "/"):
            raise RuntimeError("/factory is read-only")
        f = self.L.dt_drive_rmdir if is_dir else self.L.dt_drive_rm

        def call():
            err = ctypes.create_string_buffer(256)
            return f(self.rt, path.encode(), err, len(err)), err
        self._call("rm", call)

    @staticmethod
    def to_wav(data, name):
        """Any audio macOS can read -> 16-bit WAV bytes (WAV files pass through)."""
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            return data
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "in" + (os.path.splitext(name)[1] or ".bin"))
            dst = os.path.join(d, "out.wav")
            with open(src, "wb") as f:
                f.write(data)
            r = subprocess.run(["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16", src, dst],
                               capture_output=True, text=True)
            if r.returncode:
                raise RuntimeError(f"{name}: not an audio file macOS can read ({r.stderr.strip()[:120]})")
            with open(dst, "rb") as f:
                return f.read()

    def upload(self, folder, name, data):
        """Put one audio file into `folder` (as the plug-in: base name up to 32 characters)."""
        if folder == FACTORY or folder.startswith(FACTORY + "/"):
            raise RuntimeError("/factory is read-only")
        wav = self.to_wav(data, name)
        blob = ctypes.POINTER(ctypes.c_uint8)()
        n = ctypes.c_size_t()
        err = ctypes.create_string_buffer(256)
        self._check(self.L.dt_wav2dt(wav, len(wav), ctypes.byref(blob), ctypes.byref(n), err, len(err)), err)
        try:
            base = os.path.splitext(os.path.basename(name))[0][:32]
            target = (folder.rstrip("/") or "") + "/" + base
            data = ctypes.string_at(blob, n.value)

            def call():
                e2 = ctypes.create_string_buffer(256)
                return self.L.dt_drive_put(self.rt, target.encode(), data, len(data), None, None, e2, len(e2)), e2
            self._call("put", call)
            return target
        finally:
            self.libc.free(blob)
