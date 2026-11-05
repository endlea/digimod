"""The emulator: one Digitakt II at a time (emulator/run.py as a child process) on a base firmware or a build.

Each firmware gets its own machine and first-session cache (data/emu/<sha>/: a saved machine only fits the MAIN OS
that made it). A build runs with the address profile of the OS it was made from (DT_BASE_MAIN_SHA256). The real
DSP of a firmware's section 7 is compiled the first time it is needed (emulator/build_dsp.py, ~10 min the first
time, then ~4 min per new DSP section); the panel is the emulator's own page, shown in an iframe.
"""
import hashlib
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

import firmware

ROOT = firmware.ROOT
DATA = firmware.DATA
PORT = 8771
PY = os.path.join(ROOT, ".venv", "bin", "python")
if not os.path.exists(PY):
    PY = sys.executable
ENGINE = os.path.join(DATA, "build", "libfcdt.dylib")


def unavailable():
    """Why the emulator cannot run here, or None. It needs macOS (CoreAudio, CoreMIDI) and `make setup`."""
    if sys.platform != "darwin":
        return "the emulator runs on macOS only (start digimod with `make run` instead of Docker)"
    if not os.path.exists(ENGINE):
        return "the emulator is not built yet: run `make setup`"
    return None


class Emulator:
    """One emulator at a time. Every start or stop begins a new generation: a start still preparing from an older
    generation (a second click while the first one compiles or converts) gives up and never launches a process."""

    def __init__(self):
        self.lock = threading.Lock()
        self.dsp_lock = threading.Lock()         # one DSP compile at a time
        self.gen = 0
        self.proc = None
        self.state = {"status": "stopped"}
        self.log = []

    def _log(self, line):
        self.log = (self.log + [line.rstrip()])[-200:]

    def status(self):
        why = unavailable()
        if why:
            return {"status": "unavailable", "error": why, "log": []}
        s = dict(self.state)
        if self.proc and self.proc.poll() is not None and s["status"] in ("starting", "running"):
            s["status"] = "stopped"
            s["error"] = f"the emulator exited ({self.proc.returncode})"
            self.state = s
        s["log"] = self.log[-30:]
        return s

    def start(self, kind, ident):
        """kind "base" (a firmware in firmware/) or "build" (a finished build)."""
        why = unavailable()
        if why:
            raise ValueError(why)
        if kind == "base":
            path, info = firmware.base_by_id(ident)
            base_main = None
            label = f"{info['device']} OS {info['version']} (original)"
        else:
            path, b = firmware.Builds().path(ident)
            info = firmware.prepare(path)            # the built file itself, unpacked again
            base_main = b["base"]["main_os_sha256"]
            label = f"{b['base']['device']} OS {b['base']['version']} + " + (
                ", ".join(m["name"] for m in b["mods"]) or "no mods") + f" (build {b['id']})"
        if info.get("main_os_sha256") not in firmware.emulated_main_os() and base_main not in firmware.emulated_main_os():
            raise ValueError("the emulator has no address profile for this firmware (Digitakt II OS 1.16 only for now)")
        with self.lock:
            if (self.state.get("kind"), self.state.get("id")) == (kind, ident) and \
                    self.state["status"] in ("preparing", "building the DSP", "starting"):
                return self.status()                 # already on its way
            self._stop()
            self.gen += 1
            self.log = []
            self.state = {"status": "preparing", "label": label, "kind": kind, "id": ident, "since": time.time()}
            gen = self.gen
        threading.Thread(target=self._run, args=(gen, info, base_main), daemon=True).start()
        return self.status()

    def _set(self, gen, **kw):
        with self.lock:
            if gen == self.gen:
                self.state.update(kw)
            return gen == self.gen

    def _run(self, gen, info, base_main):
        try:
            syx, sec7 = firmware.engine_syx(info)
            native = os.path.join(DATA, "dsp", hashlib.sha256(open(sec7, "rb").read()).hexdigest()[:16],
                                  "libdndsp_native.dylib")
            with self.dsp_lock:
                if not os.path.exists(native):
                    if not self._set(gen, status="building the DSP"):
                        return
                    self._log("compiling this firmware's DSP (about 10 minutes the first time)")
                    r = subprocess.Popen([PY, "-u", os.path.join(ROOT, "emulator", "build_dsp.py"), sec7],
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    for line in r.stdout:
                        self._log(line)
                    if r.wait():
                        raise RuntimeError("the DSP did not build")
            where = os.path.join(DATA, "emu", info["sha256"][:16])
            os.makedirs(where, exist_ok=True)
            env = dict(os.environ)
            if base_main:
                env["DT_BASE_MAIN_SHA256"] = base_main
            with self.lock:
                if gen != self.gen:                  # stopped or replaced while preparing
                    return
                self.state["status"] = "starting"
                self._log("starting (the first start of a firmware boots it and loads the starter sounds: ~30 s)")
                proc = self.proc = subprocess.Popen(
                    [PY, "-u", os.path.join(ROOT, "emulator", "run.py"), "--syx", syx, "--sec7", sec7,
                     "--machine", os.path.join(where, "machine.dtsz"), "--cache", os.path.join(where, "cache"),
                     "--serve", str(PORT)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True)
            threading.Thread(target=self._pump, args=(proc,), daemon=True).start()
            for _ in range(600):
                if gen != self.gen:
                    return
                if proc.poll() is not None:
                    raise RuntimeError("the emulator exited while starting")
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{PORT}/state", timeout=1).read()
                    self._set(gen, status="running", url=f"http://127.0.0.1:{PORT}/")
                    return
                except OSError:
                    time.sleep(0.5)
            raise RuntimeError("the emulator did not come up")
        except Exception as e:                           # noqa: BLE001  (shown in the page)
            self._set(gen, status="failed", error=str(e))

    def _pump(self, proc):
        for line in proc.stdout:
            self._log(line)

    def _stop(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            os.killpg(p.pid, signal.SIGTERM)         # the emulator saves the machine and exits
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
        self.state = {"status": "stopped"}

    def stop(self):
        with self.lock:
            self.gen += 1
            self._stop()
