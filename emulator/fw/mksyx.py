#!/usr/bin/env python3
"""Rebuild a firmware .syx from extracted sections, in the form digi2 reads (dtcore/src/fw.c):

    emulator/fw/mksyx.py <folder with the sections> <out.syx>

Container "ELE3" (as mkflash.py lays it out at flash 0x80000) with sections 2, 3, 4, 5, 7, 8;
3 and 7 are stored as an NRV2B literal stream (digi2 calls the codec "elz") behind an 8-byte
header [stream length][byte sum of the stream], the form digi2 recognises as packed. The
container is then cut into SysEx messages: F0, 9 header bytes, 116 bytes of 8-in-7 data
(101 bytes each), 1 checksum byte, F7. digi2 checks the MAIN OS SHA-256 against its address
profile, so the decompressed section 3 must be the exact OS 1.11 image.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mkflash import nrv2b_decompress, nrv2b_store  # noqa: E402

SECTIONS = {                         # type: (file, load address)
    2: ("section_2_bootstrap.bin", 0x800003FC),
    3: ("section_3_MAIN_OS.bin", 0x40000400),
    4: ("section_4_updater.raw", 0x80000400),
    5: ("section_5_meta.raw", 0),
    7: ("section_7_blob.bin", 0),
    8: ("section_8_unknown.bin", 0),
}
PACKED = {3, 7}
PER_MSG = 101                        # data bytes in one message (14 groups of 7, then 3)


def container(fwdir):
    entries, blobs = [], []
    off = 0x1000
    for typ, (fname, load) in SECTIONS.items():
        path = os.path.join(fwdir, fname)
        if not os.path.exists(path):
            continue
        data = open(path, "rb").read()
        if typ in PACKED:
            stream = nrv2b_store(data)
            assert nrv2b_decompress(stream) == data
            data = struct.pack(">II", len(stream), sum(stream) & 0xFFFFFFFF) + stream
        entries.append((typ, off, len(data), load))
        blobs.append((off, data))
        off = (off + len(data) + 0xFFF) & ~0xFFF
    c = bytearray(off)
    c[0:8] = struct.pack(">4sI", b"ELE3", 0x34)
    c[0x1C:0x20] = struct.pack(">I", len(entries))
    for i, e in enumerate(entries):
        c[0x20 + 16 * i:0x30 + 16 * i] = struct.pack(">4I", *e)
    for o, d in blobs:
        c[o:o + len(d)] = d
    return bytes(c)


def encode7(chunk):
    """101 bytes -> 116: groups of [msb byte][7 bytes & 0x7F], msb of byte n at bit 6-n."""
    out = bytearray()
    for g in range(0, len(chunk), 7):
        grp = chunk[g:g + 7]
        ms = 0
        for n, b in enumerate(grp):
            ms |= (b >> 7) << (6 - n)
        out.append(ms)
        out += bytes(b & 0x7F for b in grp)
    return out


def syx(data):
    out = bytearray()
    for i in range(0, len(data), PER_MSG):
        chunk = data[i:i + PER_MSG].ljust(PER_MSG, b"\0")
        payload = encode7(chunk)
        assert len(payload) == 116
        seq = i // PER_MSG
        head = bytes([0x00, 0x20, 0x3C, 0x00, 0x00, (seq >> 14) & 0x7F, (seq >> 7) & 0x7F, seq & 0x7F, 0x00])
        body = head + payload
        out += b"\xF0" + body + bytes([sum(body) & 0x7F]) + b"\xF7"
    return bytes(out)


def main():
    fwdir, out = sys.argv[1], sys.argv[2]
    data = syx(container(fwdir))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "wb") as f:
        f.write(data)
    print(f"{out}: {len(data)} bytes")


if __name__ == "__main__":
    main()
