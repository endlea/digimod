"""Smoke test: for each base firmware, build it alone, with each mod made for it, and with all of them at once.

    make test            (in Docker)
    .venv/bin/python tests/smoke.py

Builds go to a temporary folder, so data/builds stays as it is. Needs at least one official OS .syx in firmware/.
A build with no mods must come out byte-identical to the original; every other build must pass all its checks.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
import firmware  # noqa: E402
import mods  # noqa: E402


def wait(builds, b):
    while builds.get(b["id"])["status"] in ("queued", "building"):
        time.sleep(0.2)
    return builds.get(b["id"])


def main():
    bases = [f for f in firmware.bases() if f["checksums_ok"]]
    if not bases:
        sys.exit("no valid OS .syx in firmware/: add one first")
    storage = [m for m in mods.load() if not m["problems"]]
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        builds = firmware.Builds(tmp)
        for f in bases:
            fit = [m for m in storage if mods.fits(m, f.get("main_os_sha256"))]
            jobs = [[]] + [[m] for m in fit] + ([fit] if len(fit) > 1 else [])
            for chosen in jobs:
                b = wait(builds, builds.start(f["id"], chosen))
                label = f"{f['device']} OS {f['version']} + " + (", ".join(m["id"] for m in chosen) or "no mods")
                if b["status"] == "ok":
                    print(f"ok    {label}", flush=True)
                    continue
                failed += 1
                why = b.get("error") or ", ".join(c["name"] for c in b["checks"] if not c["ok"])
                print(f"FAIL  {label}: {why}", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
