"""Firmware files through elektron-firmware-tool: inspect, extract, build with mods, verify.

Base firmwares are the official OS .syx files in firmware/. Everything made from them lives in data/:
    data/fw/<sha>/        one .syx (base or build): info.json, sections/ (extracted), engine.syx (the emulator's form)
    data/builds/<id>/     a build: the new .syx, build.json (mods, sections changed, every check and its result)
<sha> is the first 16 hex digits of the file's SHA-256.

A build is handed out only when every check passed: the tool's checksums of the new file, and each of its sections,
extracted again, byte-identical to what we meant to put there (the patched ones and the untouched ones).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
FIRMWARE = os.path.join(ROOT, "firmware")
TOOL = os.environ.get("DIGIMOD_TOOL") or os.path.join(DATA, "build", "elektron-firmware-tool")
PROFILES = os.path.join(ROOT, "third_party", "dt2", "core", "machines", "digitakt2", "profiles.inc.c")
MKSYX = os.path.join(ROOT, "emulator", "fw", "mksyx.py")

_lock = threading.Lock()                   # one extraction of a file at a time
_engine_lock = threading.Lock()            # one engine.syx made at a time


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def tool(*args):
    r = subprocess.run([TOOL, *args], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def inspect(path):
    """The tool's summary of an OS .syx -> {device, version, sections: [{id, name, size}], checksums_ok}."""
    rc, out = tool("-i", path)
    info = {"device": None, "version": None, "sections": [], "checksums_ok": False, "tool_output": out}
    if rc:
        info["error"] = out.strip().splitlines()[-1] if out.strip() else f"exit {rc}"
        return info
    for line in out.splitlines():
        m = re.match(r"device\s*:\s*(.+?)\s*\(0x[0-9a-f]+\)", line)
        if m:
            info["device"] = m.group(1)
        m = re.match(r"version\s*:\s*(\S+)", line)
        if m:
            info["version"] = m.group(1)
        m = re.match(r"\s*id (\d+)\s+(.+?)\s+(\d+) B", line)
        if m:
            info["sections"].append({"id": int(m.group(1)), "name": m.group(2).strip(), "size": int(m.group(3))})
        if re.match(r"checksums\s*:\s*ok", line):
            info["checksums_ok"] = True
    return info


def section_files(folder):
    """{section id: file path} in a folder the tool extracted into."""
    out = {}
    for f in os.listdir(folder):
        m = re.match(r"section_(\d+)_", f)
        if m:
            out[int(m.group(1))] = os.path.join(folder, f)
    return out


def emulated_main_os():
    """MAIN OS SHA-256s the emulator has an address profile for (dt2's profiles)."""
    return set(re.findall(r'\{"([0-9a-f]{64})"', open(PROFILES).read()))


def prepare(path):
    """Inspect and extract a .syx once (cached by its hash) -> its info, with paths to the extracted sections."""
    data = open(path, "rb").read()
    full = sha256(data)
    where = os.path.join(DATA, "fw", full[:16])
    meta = os.path.join(where, "info.json")
    with _lock:
        if os.path.exists(meta):
            info = json.load(open(meta))
            info["dir"] = where              # data/ may be mounted elsewhere (Docker) than where it was cached
            return info
        os.makedirs(where, exist_ok=True)
        info = inspect(path)
        info.update({"sha256": full, "file": os.path.basename(path), "size": len(data), "dir": where})
        if info["checksums_ok"]:
            sec = os.path.join(where, "sections")
            shutil.rmtree(sec, ignore_errors=True)
            rc, out = tool("-i", path, "-o", sec)
            if rc:
                info["error"] = f"extract: {out.strip()[-300:]}"
            else:
                files = section_files(sec)
                info["section_sha256"] = {str(k): sha256(open(v, "rb").read()) for k, v in files.items()}
                info["main_os_sha256"] = info["section_sha256"].get("3")
        json.dump(info, open(meta + ".tmp", "w"), indent=1)
        os.replace(meta + ".tmp", meta)
        return info


def engine_syx(info):
    """The firmware in the form the emulator's engine reads (emulator/fw/mksyx.py) -> (syx path, DSP section path)."""
    sec = os.path.join(info["dir"], "sections")
    out = os.path.join(info["dir"], "engine.syx")
    files = section_files(sec)
    with _engine_lock:
        if not os.path.exists(out):
            names = {2: "section_2_bootstrap.bin", 3: "section_3_MAIN_OS.bin", 4: "section_4_updater.raw",
                     5: "section_5_meta.raw", 7: "section_7_blob.bin", 8: "section_8_unknown.bin"}
            tmp = tempfile.mkdtemp(prefix="engine_sections.", dir=info["dir"])
            try:
                for k, name in names.items():
                    if k in files:
                        shutil.copy(files[k], os.path.join(tmp, name))
                r = subprocess.run([sys.executable, MKSYX, tmp, out + ".tmp"], capture_output=True, text=True)
                if r.returncode:
                    raise RuntimeError(f"mksyx: {(r.stdout + r.stderr).strip()[-300:]}")
                os.replace(out + ".tmp", out)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    return out, files[7]


def bases():
    """The base firmwares in firmware/ with what the tool says about them."""
    out = []
    for f in sorted(os.listdir(FIRMWARE)):
        if f.lower().endswith(".syx"):
            info = prepare(os.path.join(FIRMWARE, f))
            info = {k: v for k, v in info.items() if k not in ("tool_output", "dir")}
            info["id"] = info["sha256"][:16]
            info["emulator"] = info.get("main_os_sha256") in emulated_main_os()
            out.append(info)
    return out


def base_by_id(fid):
    for f in sorted(os.listdir(FIRMWARE)):
        if f.lower().endswith(".syx"):
            info = prepare(os.path.join(FIRMWARE, f))
            if info["sha256"].startswith(fid):
                return os.path.join(FIRMWARE, f), info
    raise KeyError(f"no base firmware {fid}")


def apply_mods(base_info, mods, workdir, options=None):
    """Copy the base's sections into workdir and apply the mods' patches -> ({section id: path}, [changed ids]).
    A patch is (section, offset, the bytes it expects there, the bytes it writes): overwrite only, the section
    keeps its size, and the expected bytes must be there (else the mod is for another firmware). Then the mods'
    settings: each write puts one byte computed from the options, and only inside the mod's own patches."""
    import mods as modstore
    files = section_files(os.path.join(base_info["dir"], "sections"))
    work = {}
    for k, p in files.items():
        work[k] = os.path.join(workdir, os.path.basename(p))
        shutil.copy(p, work[k])
    changed, taken = set(), {}
    for mod in mods:
        for patch in mod.get("patches", []):
            sid, off = int(patch["section"]), int(str(patch["offset"]), 0)
            old, new = bytes.fromhex(patch["from"]), bytes.fromhex(patch["to"])
            if sid in (2, 4):
                raise ValueError(f"{mod['id']}: section {sid} (bootloader / updater) must never be changed")
            if len(old) != len(new) or not new:
                raise ValueError(f"{mod['id']}: a patch must overwrite as many bytes as it expects")
            for b in range(off, off + len(new)):
                if (sid, b) in taken:
                    raise ValueError(f"{mod['id']} and {taken[(sid, b)]} change the same byte "
                                     f"(section {sid}, {b:#x})")
                taken[(sid, b)] = mod["id"]
            data = bytearray(open(work[sid], "rb").read())
            if off + len(old) > len(data) or data[off:off + len(old)] != old:
                raise ValueError(f"{mod['id']}: section {sid} at {off:#x} is not what the mod expects "
                                 f"(another firmware version?)")
            data[off:off + len(new)] = new
            open(work[sid], "wb").write(data)
            changed.add(sid)
        values = modstore.option_values(mod, (options or {}).get(mod["id"]))
        for w in mod.get("writes", []):
            sid, off = int(w["section"]), int(str(w["offset"]), 0)
            if not any(int(p["section"]) == sid and int(str(p["offset"]), 0) <= off <
                       int(str(p["offset"]), 0) + len(bytes.fromhex(p["to"])) for p in mod["patches"]):
                raise ValueError(f"{mod['id']}: a setting outside the mod's own patches (section {sid}, {off:#x})")
            v = modstore.evaluate(w["u8"], values)
            if not 0 <= v <= 255:
                raise ValueError(f"{mod['id']}: setting {w['u8']} = {v} is not a byte")
            data = bytearray(open(work[sid], "rb").read())
            data[off] = v
            open(work[sid], "wb").write(data)
        mod["values"] = values
    tails = {}
    for mod in mods:                      # data a mod appends to a section (the DSP's own code)
        for t in mod.get("tails", []):
            sid, at = int(t["section"]), int(str(t["at"]), 0)
            if sid in (2, 4):
                raise ValueError(f"{mod['id']}: section {sid} (bootloader / updater) must never be changed")
            if sid in tails:
                raise ValueError(f"{mod['id']} and {tails[sid]} both append to section {sid}")
            tails[sid] = mod["id"]
            data = bytearray(open(work[sid], "rb").read())
            if len(data) != at:
                raise ValueError(f"{mod['id']}: section {sid} is {len(data)} bytes, the mod appends at {at}")
            data += open(modstore.file(mod, t["file"]), "rb").read()
            open(work[sid], "wb").write(data)
            changed.add(sid)
    segs = segments(mods)
    with_partners(mods, segs)
    if segs:
        add_segments(work[3], segs, taken)
        changed.add(3)
    return work, sorted(changed)


# Expansion memory: code and data mods keep in the free top of RAM (the scheme of digikit's core/expansion). A mod
# lists "segments" ({dest, file or data (hex), bss}); digimod appends them as one chain to the MAIN OS image and puts
# one loader in (core/loader.s), which copies them there at reset.
MAIN_BASE = 0x40000400
MAIN_END = 0x4031FF60                              # the stock MAIN OS image's end, OS 1.16 and 1.17
EXPANSION = (0x47E8B000, 0x47FE0000)               # unused by the OS (digikit's port analysis)
LOADER_AT = 0x40310420                             # alignment padding before the table at 0x40311000
RESET_CALLS = (0x4000053E, bytes.fromhex("4ebaff1c4ebaff6e"))   # jsr copy ; jsr clear
LOADER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core", "loader.bin")


def segments(mods):
    """[(mod id, dest, payload, bss)] of the mods, checked: 4-byte multiples, inside the expansion region, apart."""
    import mods as modstore
    out = []
    for mod in mods:
        for s in mod.get("segments", []):
            dest, bss = int(str(s["dest"]), 0), int(str(s.get("bss", 0)), 0)
            if "file" in s:
                data = open(modstore.file(mod, s["file"]), "rb").read()
            else:
                data = bytes.fromhex(s["data"])
            if dest % 4 or len(data) % 4 or bss % 4:
                raise ValueError(f"{mod['id']}: a segment at {dest:#x} is not in 4-byte units")
            end = dest + max(len(data), bss)
            if not (EXPANSION[0] <= dest and end <= EXPANSION[1]):
                raise ValueError(f"{mod['id']}: segment {dest:#x}..{end:#x} is outside the expansion memory")
            for oid, odest, odata, obss in out:
                if dest < odest + max(len(odata), obss) and odest < end:
                    raise ValueError(f"{mod['id']} and {oid} both use memory at {max(dest, odest):#x}")
            out.append((mod["id"], dest, data, bss))
    return out


def with_partners(mods, segs):
    """A mod's "with": {other mod id: [{"dest", "from", "to"}]} -- bytes it changes in the other mod's segments when
    both are built (e.g. a stock table the other one copied into RAM). Checked like a patch."""
    ids = {m["id"] for m in mods}
    for mod in mods:
        for other, changes in mod.get("with", {}).items():
            if other not in ids:
                continue
            for c in changes:
                at, old, new = int(str(c["dest"]), 0), bytes.fromhex(c["from"]), bytes.fromhex(c["to"])
                for i, (oid, dest, payload, bss) in enumerate(segs):
                    if oid == other and dest <= at and at + len(old) <= dest + len(payload):
                        o = at - dest
                        if payload[o:o + len(old)] != old:
                            raise ValueError(f"{mod['id']}: {other}'s memory at {at:#x} is not what it expects")
                        segs[i] = (oid, dest, payload[:o] + new + payload[o + len(new):], bss)
                        break
                else:
                    raise ValueError(f"{mod['id']}: no segment of {other} at {at:#x}")


def add_segments(main_path, segs, taken):
    """Append the segment chain to the MAIN OS image, write the loader and point the reset calls at it."""
    data = bytearray(open(main_path, "rb").read())
    if len(data) != MAIN_END - MAIN_BASE:
        raise ValueError("the MAIN OS image is not of the stock size: cannot append segments")
    loader = open(LOADER, "rb").read()
    hook = b"\x4e\xb9" + LOADER_AT.to_bytes(4, "big") + b"\x4e\x71"
    for at, old in ((LOADER_AT, bytes(len(loader))), RESET_CALLS):
        off = at - MAIN_BASE
        for b in range(off, off + len(old)):
            if (3, b) in taken:
                raise ValueError(f"{taken[(3, b)]} changes the bytes digimod's segment loader needs ({at:#x})")
        if data[off:off + len(old)] != old:
            raise ValueError(f"MAIN OS at {at:#x} is not as expected for the segment loader")
    data[LOADER_AT - MAIN_BASE:LOADER_AT - MAIN_BASE + len(loader)] = loader
    data[RESET_CALLS[0] - MAIN_BASE:RESET_CALLS[0] - MAIN_BASE + 8] = hook
    for _, dest, payload, bss in sorted(segs, key=lambda s: s[1]):
        data += len(payload).to_bytes(4, "big") + dest.to_bytes(4, "big") + bss.to_bytes(4, "big") + payload
    data += b"\xff\xff\xff\xff"
    open(main_path, "wb").write(data)


class Builds:
    """Builds run one at a time in a thread; their state is data/builds/<id>/build.json."""

    def __init__(self, folder=None):
        self.dir = folder or os.path.join(DATA, "builds")
        os.makedirs(self.dir, exist_ok=True)
        self.lock = threading.Lock()

    def list(self):
        out = []
        for b in sorted(os.listdir(self.dir), reverse=True):
            p = os.path.join(self.dir, b, "build.json")
            if os.path.exists(p):
                out.append(json.load(open(p)))
        return out

    def get(self, bid):
        p = os.path.join(self.dir, os.path.basename(bid), "build.json")
        if not os.path.exists(p):
            raise KeyError(f"no build {bid}")
        return json.load(open(p))

    def _save(self, b):
        p = os.path.join(self.dir, b["id"], "build.json")
        json.dump(b, open(p + ".tmp", "w"), indent=1)
        os.replace(p + ".tmp", p)

    def start(self, base_id, mods, options=None):
        path, info = base_by_id(base_id)
        bid = stamp = time.strftime("%Y%m%d-%H%M%S")
        n = 1
        while os.path.exists(os.path.join(self.dir, bid)):   # two builds in the same second
            n += 1
            bid = f"{stamp}-{n}"
        os.makedirs(os.path.join(self.dir, bid))
        b = {"id": bid, "status": "queued", "created": time.time(), "base": {"id": info["sha256"][:16],
             "file": info["file"], "device": info["device"], "version": info["version"],
             "main_os_sha256": info.get("main_os_sha256")},
             "mods": [{"id": m["id"], "name": m.get("name", m["id"]), "version": m.get("version")} for m in mods],
             "checks": [], "log": []}
        self._save(b)
        threading.Thread(target=self._run, args=(b, path, info, mods, options or {}), daemon=True).start()
        return b

    def _run(self, b, path, info, mods, options):
        with self.lock:
            d = os.path.join(self.dir, b["id"])

            def check(name, ok, detail=""):
                b["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
                self._save(b)
                return ok
            try:
                b["status"] = "building"
                self._save(b)
                work = os.path.join(d, "work")
                os.makedirs(work)
                files, changed = apply_mods(info, mods, work, options)
                b["sections_changed"] = changed
                for m in b["mods"]:
                    m["options"] = next(x.get("values", {}) for x in mods if x["id"] == m["id"])
                ver = (info["version"] or "OS").replace(" ", "")
                name = f"{(info['device'] or 'Elektron').replace(' ', '_')}_OS{ver}-digimod-{b['id']}.syx"
                out = os.path.join(d, name)
                t = time.time()
                if changed:
                    args = ["-i", path]
                    for sid in changed:
                        args += ["-c", str(sid), files[sid]]
                    rc, log = tool(*args, "-o", out)
                else:                                # no change: the tool's lossless repack of the original
                    rc, log = tool("-i", path, "-r", "-o", out)
                b["log"].append(log)
                if not check("pack", rc == 0 and os.path.exists(out), f"{time.time() - t:.1f} s"):
                    raise RuntimeError("the tool could not pack the firmware")
                new = inspect(out)
                check("checksums and HMAC of the new file", new["checksums_ok"], new["tool_output"].strip()[-200:])
                check("same device and version", (new["device"], new["version"]) == (info["device"], info["version"]),
                      f"{new['device']} {new['version']}")
                verify = os.path.join(d, "verify")
                rc, log = tool("-i", out, "-o", verify)
                got = section_files(verify) if rc == 0 else {}
                check("sections extracted again", rc == 0 and set(got) == set(files), ", ".join(map(str, sorted(got))))
                for sid in sorted(files):
                    same = sid in got and open(got[sid], "rb").read() == open(files[sid], "rb").read()
                    what = "patched" if sid in changed else "untouched"
                    check(f"section {sid} byte-identical ({what})", same)
                if not changed:
                    check("byte-identical to the original", open(out, "rb").read() == open(path, "rb").read())
                ok = all(c["ok"] for c in b["checks"])
                if ok:
                    data = open(out, "rb").read()
                    b["file"] = name
                    b["size"] = len(data)
                    b["sha256"] = sha256(data)
                    b["main_os_sha256"] = sha256(open(files[3], "rb").read())
                else:
                    os.replace(out, out + ".failed")     # never handed out
                b["status"] = "ok" if ok else "failed"
                shutil.rmtree(work, ignore_errors=True)
                shutil.rmtree(verify, ignore_errors=True)
            except Exception as e:                     # noqa: BLE001  (reported in the build)
                b["status"] = "failed"
                b["error"] = str(e)
            b["finished"] = time.time()
            self._save(b)

    def path(self, bid):
        b = self.get(bid)
        if b["status"] != "ok":
            raise KeyError(f"build {bid} is not usable ({b['status']})")
        return os.path.join(self.dir, b["id"], b["file"]), b
