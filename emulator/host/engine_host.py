"""Shared host for the devices that run on digi2/dt2's C engines (dt_rt API): a live panel in a
browser window with sound on the system output, or an offline render to WAV. Each device has a
thin launcher (emulator/run.py) that describes itself
with a Device and calls main(device).

Panel page (host/panel.html) and HTTP API on 127.0.0.1:PORT:
    GET  /                      the panel: photo, live screen, LEDs, keys, encoders, Main Volume
    GET  /panel/photo.png, /panel/layout.json      the device's panel photo and layout
    GET  /panel/frame?since=V   8192 screen bytes (0/1 per pixel) + the LED bytes; 204 if unchanged; X-Frame
    GET  /screen.png?scale=N    the screen as PNG
    GET  /screen.json           {"frame", "time", "width", "height", "rows": hex per row, "leds"}
    GET  /state                 {"device", "frame", "time", "keys_down", "leds", "underruns", "status", "native_audio"}
    GET  /audio?pos=N           (only with --browser-audio) 16-bit LE stereo 48 kHz since sample N; X-Audio-Pos
    POST /input                 {"key": N, "down": bool} (N = matrix row*8 + bit) | {"encoder": N, "delta": D} (N = 0..8)
    POST /panel_input           {"kind", "code", "arg"} in the engine's own panel codes (0 down, 1 up, 3 encoder turn)
    POST /master                {"master": 0..0.5} (Main Volume)
    POST /save                  save the machine now (devices that keep one)
"""
import argparse
import array
import ctypes
import json
import os
import signal
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import host_tools
import screen

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # repository root
HERE = os.path.dirname(os.path.abspath(__file__))
RATE = 48000
BLOCK = 512

# dt_rt_panel kinds (core/src/*panel.inc.c in digi2 and dt2)
KEY_DOWN, KEY_UP, RELEASE_ALL, ENCODER = 0, 1, 2, 3

# digi2/dt2 give their emulation (and DSP) threads a macOS time-constraint (real-time) policy. On a
# busy M1 Pro (an open DAW) the kernel throttles them: Digitone II went 0.55x real time with it, 0.92x
# without. Set before the engine library starts its threads.
os.environ.setdefault("DN_DSP_CONSTRAINT", "0")
os.environ.setdefault("DN_CF_CONSTRAINT", "0")
os.environ.setdefault("DT_CF_CONSTRAINT", "0")


@dataclass
class Device:
    name: str                       # "Digitakt II"
    key: str                        # "digitakt2": logs/<key>/engine, build/<key>/
    lib: str                        # host library (dt_rt_* + the opener + fc_audio_start)
    syx: str
    panel_png: str
    panel_json: str
    open: callable                  # (L, syx bytes, rate, max_frames, log callback, err buffer) -> dt_rt* or 0
    n_leds: int = 50
    leds: callable = None           # (L, rt, buf) -> version; None: the LEDs of dt_rt_screen
    special_keys: dict = field(default_factory=dict)   # our key number -> engine code (the rest: n + 1)
    save: callable = None           # (L, rt) -> 0 when the machine was saved; None: nothing kept
    stereo: bool = True             # engine output 1 = right channel (digi2); False: it is track 1 (dt2), mix to both
    drive: bool = False             # the machine has dt2's +Drive RPC (dt_drive_*): the side panel's +DRIVE tab
    port: int = 8765


LIVE_FRAMES = 1024                  # engine max_frames when CoreAudio drives it: latency margin for any device buffer


class Engine:
    def __init__(self, dev, rate=RATE, max_frames=BLOCK, log=print):
        self.dev = dev
        self.rate, self.max_frames = rate, max_frames
        L = self.L = ctypes.CDLL(dev.lib)
        self.log = log
        LOG = ctypes.CFUNCTYPE(None, ctypes.c_char_p)
        self._log_cb = LOG(lambda s: log(f"{dev.key}: " + s.decode(errors="replace")))
        L.dt_rt_render.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
                                   ctypes.c_int, ctypes.c_int]
        L.dt_rt_panel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        L.dt_rt_screen.restype = ctypes.c_uint64
        L.dt_rt_screen.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        L.dt_rt_underruns.restype = ctypes.c_int64
        L.dt_rt_underruns.argtypes = [ctypes.c_void_p]
        L.dt_rt_status.restype = ctypes.c_char_p
        L.dt_rt_status.argtypes = [ctypes.c_void_p]
        L.dt_rt_set_master.argtypes = [ctypes.c_void_p, ctypes.c_double]
        L.fc_audio_start.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_uint32, ctypes.c_int,
                                     ctypes.c_char_p, ctypes.c_size_t]
        L.fc_audio_stats.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        syx = open(dev.syx, "rb").read()
        err = ctypes.create_string_buffer(1024)
        t = time.time()
        self.rt = dev.open(L, syx, float(rate), max_frames, self._log_cb, err)
        if not self.rt:
            raise RuntimeError(f"{dev.key}: {err.value.decode(errors='replace')}")
        log(f"{dev.key}: engine ready in {time.time() - t:.1f}s ({rate:g} Hz)")
        self.l = (ctypes.c_float * BLOCK)()
        self.r = (ctypes.c_float * BLOCK)()
        self.outs = (ctypes.POINTER(ctypes.c_float) * 2)(ctypes.cast(self.l, ctypes.POINTER(ctypes.c_float)),
                                                       ctypes.cast(self.r, ctypes.POINTER(ctypes.c_float)))
        self.lock = threading.Lock()

    def render(self, n, offline):
        """n <= BLOCK stereo frames as int16 LE bytes."""
        with self.lock:
            self.L.dt_rt_render(self.rt, n, self.outs, 2 if self.dev.stereo else 1, 1 if offline else 0)
        if not self.dev.stereo:
            ctypes.memmove(self.r, self.l, 4 * n)       # dt2: output 1 would be track 1 alone
        out = array.array("h", bytes(4 * n))
        for i in range(n):
            out[2 * i] = max(-32768, min(32767, int(self.l[i] * 32767)))
            out[2 * i + 1] = max(-32768, min(32767, int(self.r[i] * 32767)))
        return out.tobytes()

    def resyncs(self):
        """Times the live output jumped to a lagging engine (dt2's rt.c; 0 where the engine lacks it)."""
        f = getattr(self.L, "dt_rt_resyncs", None)
        if f is None:
            return 0
        f.restype, f.argtypes = ctypes.c_int64, [ctypes.c_void_p]
        return f(self.rt)

    def key_code(self, n):
        if n in self.dev.special_keys:
            return self.dev.special_keys[n]
        if 0 <= n < 48:
            return n + 1
        raise ValueError(f"no such key {n}")

    def key(self, n, down):
        self.L.dt_rt_panel(self.rt, KEY_DOWN if down else KEY_UP, self.key_code(n), 0)

    def encoder(self, n, delta):
        self.L.dt_rt_panel(self.rt, ENCODER, n + 1, delta)

    def raw_frame(self):
        """-> (version, 128*64 pixels + the LED bytes) as the plug-ins read them."""
        px = ctypes.create_string_buffer(128 * 64)
        leds = ctypes.create_string_buffer(max(50, self.dev.n_leds))
        if self.dev.leds:
            sv = self.L.dt_rt_screen(self.rt, px, None)
            sv ^= self.dev.leds(self.L, self.rt, leds) << 40
        else:
            sv = self.L.dt_rt_screen(self.rt, px, leds)
        return sv, px.raw + leds.raw[:self.dev.n_leds]

    def screen(self):
        ver, raw = self.raw_frame()
        return ver, [[1 if raw[y * 128 + x] else 0 for x in range(128)] for y in range(64)], list(raw[8192:])

    def save(self):
        if not self.dev.save:
            return False
        ok = self.dev.save(self.L, self.rt) == 0
        self.log(f"{self.dev.key}: machine {'saved' if ok else 'NOT saved'}")
        return ok


class Server:
    RING = 24000

    def __init__(self, eng, port, native_audio=True):
        self.eng = eng
        self.native_audio = native_audio
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.ring = bytearray()
        self.audio_end = 0
        self.keys_down = set()
        if native_audio:                    # CoreAudio pulls the engine itself (host/audio_out.c)
            err = ctypes.create_string_buffer(256)
            if eng.L.fc_audio_start(eng.rt, eng.rate, eng.max_frames, int(eng.dev.stereo), err, len(err)):
                raise RuntimeError(err.value.decode())
        else:
            threading.Thread(target=self._audio, daemon=True).start()
        server = self
        dev = eng.dev
        self.settings = host_tools.Settings(os.path.join(ROOT, "data", "settings", dev.key + ".json"))
        self.master = float(self.settings.get("master", 0.25))
        eng.L.dt_rt_set_master(eng.rt, self.master)
        self.midi = host_tools.Midi(eng.L, eng.rt, self.settings, eng.log)
        self.drive = host_tools.Drive(eng.L, eng.rt, self.health, eng.log) if dev.drive else None

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype, extra=()):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Expose-Headers", "X-Audio-Pos, X-Frame")
                for k, v in extra:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self):
                return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_GET(self):
                url = urlparse(self.path)
                q = parse_qs(url.query)
                if url.path in ("/", "/panel"):
                    with open(os.path.join(HERE, "panel.html"), "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                if url.path in ("/panel/photo.png", "/panel/layout.json"):
                    path = dev.panel_png if url.path.endswith(".png") else dev.panel_json
                    with open(path, "rb") as f:
                        return self._send(200, f.read(), "image/png" if path.endswith(".png") else "application/json")
                if url.path == "/panel/frame":
                    ver, data = server.eng.raw_frame()
                    if str(ver) == q.get("since", [""])[0]:
                        return self._send(204, b"", "application/octet-stream")
                    return self._send(200, data, "application/octet-stream", [("X-Frame", str(ver))])
                if url.path == "/audio":
                    data, end = server.audio_since(int(q.get("pos", ["-1"])[0]))
                    return self._send(200, data, "application/octet-stream", [("X-Audio-Pos", str(end))])
                ver, fb, leds = server.eng.screen()
                if url.path == "/screen.png":
                    scale = max(1, min(int(q.get("scale", ["4"])[0]), 8))
                    self._send(200, screen.png_bytes(fb, scale), "image/png")
                elif url.path == "/screen.json":
                    rows = ["".join(f"{int(''.join(map(str, r[x:x + 8])), 2):02x}" for x in range(0, 128, 8))
                            for r in fb]
                    body = {"frame": ver, "time": server.now(), "width": 128, "height": 64, "rows": rows,
                            "leds": leds}
                    self._send(200, json.dumps(body).encode(), "application/json")
                elif url.path == "/state":
                    mx, calls = ctypes.c_uint32(), ctypes.c_uint32()
                    server.eng.L.fc_audio_stats(ctypes.byref(mx), ctypes.byref(calls))
                    body = {"device": dev.name, "drive": bool(server.drive), "master": server.master,
                            "frame": ver, "time": server.now(), "keys_down": sorted(server.keys_down),
                            "rate": server.eng.rate, "audio_callbacks": calls.value, "audio_max_buffer": mx.value,
                            "audio_pos": server.audio_end, "leds": leds, "native_audio": server.native_audio,
                            "underruns": server.eng.L.dt_rt_underruns(server.eng.rt), "resyncs": server.eng.resyncs(),
                            "status": (server.eng.L.dt_rt_status(server.eng.rt) or b"").decode(errors="replace")}
                    self._send(200, json.dumps(body).encode(), "application/json")
                elif url.path == "/midi":
                    self._send(200, json.dumps(server.midi.state()).encode(), "application/json")
                elif url.path == "/drive" and server.drive:
                    path = q.get("path", ["/"])[0]
                    try:
                        body = {"path": path, "entries": server.drive.ls(path)}
                    except RuntimeError as e:
                        body = {"path": path, "entries": [], "error": str(e)}
                    self._send(200, json.dumps(body).encode(), "application/json")
                else:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self):
                path = urlparse(self.path).path
                try:
                    if path == "/master":
                        server.master = max(0.0, min(0.5, float(self._json()["master"])))
                        server.eng.L.dt_rt_set_master(server.eng.rt, server.master)
                        server.settings.set("master", server.master)
                    elif path == "/midi":
                        req = self._json()
                        server.midi.set(str(req["name"]), bool(req.get("on", True)))
                    elif path.startswith("/drive/") and server.drive:
                        q = parse_qs(urlparse(self.path).query)
                        try:
                            if path == "/drive/mkdir":
                                server.drive.mkdir(self._json()["path"])
                            elif path == "/drive/rm":
                                req = self._json()
                                server.drive.rm(req["path"], bool(req.get("dir")))
                            elif path == "/drive/upload":
                                data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                                target = server.drive.upload(q.get("dir", ["/incoming"])[0], q.get("name", ["sample"])[0], data)
                                return self._send(200, json.dumps({"ok": True, "path": target}).encode(), "application/json")
                            else:
                                return self._send(404, b"not found", "text/plain")
                        except RuntimeError as e:
                            return self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")
                    elif path == "/panel_input":
                        req = self._json()
                        server.eng.L.dt_rt_panel(server.eng.rt, int(req["kind"]), int(req["code"]), int(req.get("arg", 0)))
                    elif path == "/save":
                        if not server.eng.save():
                            return self._send(409, b"this device keeps no machine", "text/plain")
                    elif path == "/input":
                        req = self._json()
                        if "key" in req:
                            n, down = int(req["key"]), bool(req.get("down", True))
                            server.eng.key(n, down)
                            (server.keys_down.add if down else server.keys_down.discard)(n)
                        elif "encoder" in req:
                            server.eng.encoder(int(req["encoder"]), int(req.get("delta", 1)))
                        else:
                            raise ValueError("expected 'key' or 'encoder'")
                    else:
                        return self._send(404, b"not found", "text/plain")
                except (ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
                    return self._send(400, str(e).encode(), "text/plain")
                self._send(200, b'{"ok":true}', "application/json")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    def now(self):
        return time.time() - self.t0

    def health(self):
        """What the engine and the audio output are doing (logged when the firmware does not answer)."""
        mx, calls = ctypes.c_uint32(), ctypes.c_uint32()
        self.eng.L.fc_audio_stats(ctypes.byref(mx), ctypes.byref(calls))
        c0 = calls.value
        time.sleep(0.2)
        self.eng.L.fc_audio_stats(ctypes.byref(mx), ctypes.byref(calls))
        return {"status": (self.eng.L.dt_rt_status(self.eng.rt) or b"").decode(errors="replace"),
                "audio_callbacks_per_s": (calls.value - c0) * 5, "underruns": self.eng.L.dt_rt_underruns(self.eng.rt),
                "resyncs": self.eng.resyncs()}

    def _audio(self):
        """--browser-audio: pull the engine at wall-clock pace into a ring for /audio."""
        made = 0
        start = time.time()
        while True:
            due = int((time.time() - start) * RATE) - made
            if due < BLOCK // 2:
                time.sleep(0.003)
                continue
            n = min(due, BLOCK)
            pcm = self.eng.render(n, offline=True)    # waits for the engine: stretches rather than drops
            made += n
            with self.lock:
                self.ring += pcm
                del self.ring[:max(0, len(self.ring) - self.RING * 4)]
                self.audio_end += n

    def audio_since(self, pos):
        with self.lock:
            have = len(self.ring) // 4
            start = self.audio_end - have
            if pos < start:
                pos = max(start, self.audio_end - 4800)
            return bytes(self.ring[(pos - start) * 4:]), self.audio_end


def open_window(url):
    """A chrome-less app window if Chrome is installed, else the default browser."""
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.exists(chrome):
        subprocess.Popen([chrome, f"--app={url}", "--window-size=1000,860", "--new-window",
                          "--autoplay-policy=no-user-gesture-required"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def parse_input(spec):
    t, kind, n, v = spec.split(":")
    if kind == "key":
        return float(t), ("key", int(n), v == "down")
    return float(t), ("enc", int(n), int(v))


def render(eng, seconds, wav, inputs):
    """Offline: inputs at their times, audio as fast as the engine goes."""
    inputs = sorted(inputs)
    pcm = bytearray()
    total = int(seconds * RATE)
    t = time.time()
    made = 0
    while made < total:
        now = made / RATE
        while inputs and inputs[0][0] <= now:
            _, (kind, n, v) = inputs.pop(0)
            eng.key(n, v) if kind == "key" else eng.encoder(n, v)
            print(f"[{now:6.2f}s] input {kind} {n} {v}")
        n = min(BLOCK, total - made)
        pcm += eng.render(n, offline=True)
        made += n
    dt = time.time() - t
    os.makedirs(os.path.dirname(os.path.abspath(wav)), exist_ok=True)
    with open(wav, "wb") as f:
        f.write(struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ", 16, 1, 2, RATE,
                            RATE * 4, 4, 16, b"data", len(pcm)) + pcm)
    _, fb, _ = eng.screen()
    screen.png(fb, os.path.splitext(wav)[0] + ".png")
    print(f"rendered {seconds:.1f}s in {dt:.1f}s ({seconds / dt:.2f}x real time) -> {wav}")


def main(dev, argv=None, extra_args=None):
    p = argparse.ArgumentParser(description=f"{dev.name} on its C engine: live panel or offline render.")
    p.add_argument("--serve", type=int, nargs="?", const=dev.port, metavar="PORT",
                   help=f"live panel + sound on 127.0.0.1:PORT (default {dev.port})")
    p.add_argument("--open", action="store_true", help="open the panel in a browser window (with --serve)")
    p.add_argument("--render", type=float, metavar="SECONDS", help="render offline to --wav and stop")
    p.add_argument("--wav", default=os.path.join(ROOT, "data", "renders", "render.wav"))
    p.add_argument("--input", action="append", default=[], metavar="T:key:N:down|up or T:enc:N:DELTA",
                   help="--render: input at T seconds (our key numbers, encoders 0-8)")
    p.add_argument("--browser-audio", action="store_true",
                   help="sound through the page (/audio) instead of the system output")
    if extra_args:
        extra_args(p)
    a = p.parse_args(argv)
    live = a.serve and not a.browser_audio and not a.render
    if live:                                # engine at the output device's rate: CoreAudio converts nothing
        L = ctypes.CDLL(dev.lib)
        L.fc_audio_device_rate.restype = ctypes.c_double
        rate = L.fc_audio_device_rate() or RATE
        eng = Engine(dev, rate=rate, max_frames=LIVE_FRAMES)
    else:
        eng = Engine(dev)
    if a.render:
        render(eng, a.render, a.wav, [parse_input(s) for s in a.input])
    if a.serve:
        srv = Server(eng, a.serve, native_audio=not a.browser_audio)
        url = f"http://127.0.0.1:{a.serve}/"
        print(f"{dev.name} panel: {url} (close this window or Ctrl-C to stop)")
        threading.Thread(target=srv.httpd.serve_forever, daemon=True).start()
        if a.open:
            open_window(url)
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):    # SIGHUP: the Terminal window closed
            signal.signal(sig, lambda *_: stop.set())
        while not stop.wait(120):
            eng.save()                      # autosave every 2 minutes (devices that keep a machine)
        eng.save()
    return 0
