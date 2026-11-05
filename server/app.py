#!/usr/bin/env python3
"""digimod: pick a base firmware, tick mods, build a verified .syx, try it in the emulator.

    make run   (or: make up, in Docker)
    .venv/bin/python server/app.py [--host 127.0.0.1] [--port 8700] [--open]

API (JSON):
    GET  /api/firmwares                 base firmwares in firmware/ (device, version, checksums, sections, emulator)
    POST /api/firmwares?name=F.syx      add an official OS .syx (body: the file); refused unless the tool validates it
    GET  /api/mods                      the mod storage (mods/*/mod.json)
    GET  /api/builds                    builds, newest first; GET /api/builds/<id> one of them
    POST /api/builds                    {"firmware": id, "mods": [ids], "options": {mod id: {option: value}}}
                                        -> the build (runs in the background)
    GET  /download/<build id>           the built .syx (only when every check passed)
    GET  /api/emulator                  status, log and the panel's URL
    POST /api/emulator/start            {"kind": "base" | "build", "id": ...}
    POST /api/emulator/stop
"""
import argparse
import json
import mimetypes
import os
import re
import sys
import tempfile
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emulator  # noqa: E402
import firmware  # noqa: E402
import mods  # noqa: E402

WEB = os.path.join(firmware.ROOT, "web")
BUILDS = firmware.Builds()
EMU = emulator.Emulator()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _json(self):
        b = self._body()
        return json.loads(b) if b else {}

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        p = url.path
        try:
            if p == "/api/firmwares":
                return self._send(200, firmware.bases())
            if p == "/api/mods":
                return self._send(200, [{k: v for k, v in m.items() if k != "dir"} for m in mods.load()])
            if p == "/api/builds":
                return self._send(200, BUILDS.list())
            if p.startswith("/api/builds/"):
                return self._send(200, BUILDS.get(p.rsplit("/", 1)[1]))
            if p == "/api/emulator":
                return self._send(200, EMU.status())
            if p.startswith("/download/"):
                path, b = BUILDS.path(p.rsplit("/", 1)[1])
                return self._send(200, open(path, "rb").read(), "application/octet-stream",
                                  {"Content-Disposition": f'attachment; filename="{b["file"]}"'})
            f = os.path.normpath(os.path.join(WEB, (p.lstrip("/") or "index.html")))
            if f.startswith(WEB + os.sep) and os.path.isfile(f):
                return self._send(200, open(f, "rb").read(), mimetypes.guess_type(f)[0] or "application/octet-stream")
            self._send(404, {"error": "not found"})
        except KeyError as e:
            self._send(404, {"error": str(e).strip("'\"")})
        except Exception as e:                       # noqa: BLE001
            self._send(500, {"error": str(e)})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        p = url.path
        try:
            if p == "/api/builds":
                req = self._json()
                chosen = mods.by_ids(req.get("mods", []))
                _, info = firmware.base_by_id(req["firmware"])
                wrong = [m["id"] for m in chosen if not mods.fits(m, info.get("main_os_sha256"))]
                if wrong:
                    raise ValueError(f"not made for this firmware: {', '.join(wrong)}")
                opts = req.get("options", {})
                for m in chosen:                     # refused here, before a build starts
                    mods.option_values(m, opts.get(m["id"]))
                return self._send(200, BUILDS.start(req["firmware"], chosen, opts))
            if p == "/api/firmwares":
                name = os.path.basename(urllib.parse.parse_qs(url.query).get("name", ["firmware.syx"])[0])
                if not re.fullmatch(r"[\w.\- ]+\.syx", name, re.I):
                    raise ValueError("an OS .syx file, please")
                data = self._body()
                with tempfile.NamedTemporaryFile(suffix=".syx", delete=False) as t:
                    t.write(data)
                info = firmware.inspect(t.name)
                os.unlink(t.name)
                if not info["checksums_ok"]:
                    raise ValueError(f"not a valid Elektron OS .syx: {info.get('error', 'checksums do not match')}")
                dst = os.path.join(firmware.FIRMWARE, name)
                if os.path.exists(dst):
                    raise ValueError(f"{name} is already there")
                open(dst, "wb").write(data)
                os.chmod(dst, 0o444)
                return self._send(200, {"ok": True, "file": name})
            if p == "/api/emulator/start":
                req = self._json()
                return self._send(200, EMU.start(req["kind"], req["id"]))
            if p == "/api/emulator/stop":
                EMU.stop()
                return self._send(200, EMU.status())
            self._send(404, {"error": "not found"})
        except (KeyError, ValueError) as e:
            self._send(400, {"error": str(e).strip("'\"")})
        except Exception as e:                       # noqa: BLE001
            self._send(500, {"error": str(e)})


def main():
    ap = argparse.ArgumentParser(description="digimod: custom Elektron firmwares from mods, verified, with an emulator")
    ap.add_argument("--host", default="127.0.0.1", help="address to listen on (0.0.0.0 inside Docker)")
    ap.add_argument("--port", type=int, default=8700)
    ap.add_argument("--open", action="store_true", help="open the page in the browser")
    a = ap.parse_args()
    if not os.path.exists(firmware.TOOL):
        sys.exit(f"no firmware tool at {firmware.TOOL}: run `make setup` first")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"digimod: {url}", flush=True)
    if a.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        EMU.stop()


if __name__ == "__main__":
    main()
