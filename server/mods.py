"""The mod storage: mods/<id>/mod.json (format in mods/README.md).

Mods still in development live in lab/mods/ (not in git) and are listed only with DIGIMOD_LAB=1.

A mod says which firmwares it is made for by their MAIN OS SHA-256 (the code it patches), and each patch says the
bytes it expects; firmware.apply_mods checks both again when building.
"""
import ast
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODS = os.path.join(ROOT, "mods")
LAB = os.path.join(ROOT, "lab", "mods")


def dirs():
    out = [MODS]
    if os.environ.get("DIGIMOD_LAB") == "1" and os.path.isdir(LAB):
        out.append(LAB)
    return out


def file(m, name):
    """Path of a file that belongs to mod m."""
    return os.path.join(m["dir"], name)


def load():
    """All mods, each with "problems" (empty when its mod.json is usable)."""
    out = []
    for where in [os.path.join(base, d) for base in dirs() for d in sorted(os.listdir(base))]:
        d = os.path.basename(where)
        p = os.path.join(where, "mod.json")
        if not os.path.isfile(p):
            continue
        try:
            m = json.load(open(p))
        except ValueError as e:
            out.append({"id": d, "name": d, "dir": where, "problems": [f"mod.json: {e}"], "patches": [],
                        "firmwares": []})
            continue
        m["dir"] = where
        m["lab"] = where.startswith(LAB + os.sep)
        m.setdefault("id", d)
        m.setdefault("name", d)
        m.setdefault("patches", [])
        m.setdefault("firmwares", [])
        m.setdefault("options", [])
        m.setdefault("writes", [])
        m["problems"] = check(m)
        out.append(m)
    return out


def check(m):
    problems = []
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", m["id"]):
        problems.append("id: lowercase letters, digits, - and _")
    if not m["firmwares"]:
        problems.append("firmwares: no firmware listed")
    for f in m["firmwares"]:
        if not re.fullmatch(r"[0-9a-f]{64}", f.get("main_os_sha256", "")):
            problems.append("firmwares: each needs main_os_sha256")
    for i, p in enumerate(m["patches"]):
        try:
            sid, off = int(p["section"]), int(str(p["offset"]), 0)
            old, new = bytes.fromhex(p["from"]), bytes.fromhex(p["to"])
        except (KeyError, ValueError):
            problems.append(f"patch {i}: needs section, offset, from (hex), to (hex)")
            continue
        if sid in (2, 4):
            problems.append(f"patch {i}: section {sid} (bootloader / updater) is never changed")
        if len(old) != len(new) or not new or off < 0:
            problems.append(f"patch {i}: overwrite only, from and to of the same length")
    for i, sg in enumerate(m.get("segments", [])):
        try:
            int(str(sg["dest"]), 0), int(str(sg.get("bss", 0)), 0)
        except (KeyError, ValueError):
            problems.append(f"segment {i}: needs dest and file or data (hex)")
            continue
        if "file" in sg:
            if not os.path.isfile(file(m, sg["file"])):
                problems.append(f"segment {i}: no file {sg['file']}")
        elif not re.fullmatch(r"([0-9a-fA-F]{2})+", sg.get("data", "")):
            problems.append(f"segment {i}: needs file or data (hex)")
    for i, t in enumerate(m.get("tails", [])):
        try:
            sid, at = int(t["section"]), int(str(t["at"]), 0)
        except (KeyError, ValueError):
            problems.append(f"tail {i}: needs section, at and file")
            continue
        if sid in (2, 4) or sid == 3:
            problems.append(f"tail {i}: section {sid} cannot grow by a tail (MAIN OS: segments)")
        if not os.path.isfile(file(m, t.get("file", ""))):
            problems.append(f"tail {i}: no file {t.get('file')}")
    for other, changes in m.get("with", {}).items():
        for i, c in enumerate(changes):
            try:
                int(str(c["dest"]), 0)
                if len(bytes.fromhex(c["from"])) != len(bytes.fromhex(c["to"])):
                    raise ValueError
            except (KeyError, ValueError):
                problems.append(f"with {other} {i}: needs dest, from and to (hex, the same length)")
    names = set()
    for o in m["options"]:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", o.get("id", "")) or o.get("type") != "int" \
                or not isinstance(o.get("min"), int) or not isinstance(o.get("max"), int) \
                or not o["min"] <= o.get("default", o["min"]) <= o["max"]:
            problems.append(f"option {o.get('id')}: needs id, type int, min <= default <= max")
        names.add(o.get("id"))
    for i, w in enumerate(m["writes"]):
        try:
            int(w["section"]), int(str(w["offset"]), 0)
            expr_names(w["u8"])
        except (KeyError, ValueError, SyntaxError) as e:
            problems.append(f"write {i}: needs section, offset and a u8 expression ({e})")
            continue
        if not expr_names(w["u8"]) <= names:
            problems.append(f"write {i}: unknown option in {w['u8']}")
    for c in m.get("require", []):
        try:
            if not expr_names(c) <= names:
                problems.append(f"require: unknown option in {c}")
        except (ValueError, SyntaxError) as e:
            problems.append(f"require {c}: {e}")
    return problems


# ---- option values -> bytes: a tiny, safe expression language (integers, + - * // %, comparisons, and/or/not, if)
_OK = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Name, ast.Load, ast.Constant,
       ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod, ast.USub, ast.Not, ast.And, ast.Or,
       ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


def _tree(expr):
    t = ast.parse(str(expr), mode="eval")
    for n in ast.walk(t):
        if not isinstance(n, _OK) or (isinstance(n, ast.Constant) and not isinstance(n.value, int)):
            raise ValueError(f"not allowed in an expression: {type(n).__name__}")
    return t


def expr_names(expr):
    return {n.id for n in ast.walk(_tree(expr)) if isinstance(n, ast.Name)}


def evaluate(expr, values):
    return int(eval(compile(_tree(expr), "<option>", "eval"), {"__builtins__": {}}, dict(values)))  # noqa: S307


def option_values(m, given):
    """The mod's option values: given ones (checked against min/max), defaults for the rest; and its requirements."""
    out = {}
    for o in m["options"]:
        v = given.get(o["id"], o.get("default", o["min"])) if given else o.get("default", o["min"])
        if not isinstance(v, int) or not o["min"] <= v <= o["max"]:
            raise ValueError(f"{m['id']}: {o.get('label', o['id'])} must be {o['min']}..{o['max']}")
        out[o["id"]] = v
    for c in m.get("require", []):
        if not evaluate(c, out):
            raise ValueError(f"{m['id']}: {m.get('require_text', {}).get(c, c)}")
    return out


def fits(m, main_os_sha256):
    return any(f.get("main_os_sha256") == main_os_sha256 for f in m["firmwares"])


def by_ids(ids):
    have = {m["id"]: m for m in load()}
    missing = [i for i in ids if i not in have]
    if missing:
        raise KeyError(f"no such mod: {', '.join(missing)}")
    bad = [i for i in ids if have[i]["problems"]]
    if bad:
        raise ValueError(f"mod not usable: {', '.join(bad)}")
    return [have[i] for i in ids]
