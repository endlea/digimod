#!/usr/bin/env python3
"""Assemble a SPI NOR flash image from the extracted sections.

Layout as the bootstrap expects it (reverse engineered, see README):
    0x000000  bootstrap (serial-boot image: 4-byte length + code)
    0x080000  header: "ELE3", 0x34, ..., +0x1C = number of directory entries
    0x080020  directory, 16 bytes per entry: type, offset (from 0x80000), length, load address
    ...       section payloads, 4 KB aligned

The bootstrap looks up entry type 4 (updater/loader), copies it to its load
address and jumps to the address stored in its first long word. The updater
then reads entry type 3 (MAIN_OS) to 0x40200000, NRV2B-decompresses it (after
an 8-byte header) to its load address and jumps to its first long word.

    .venv/bin/python devices/digitone2/pyemu/mkflash.py   -> build/digitone2/flash.bin
"""
import os
import struct
import sys

DEV = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # devices/digitone2/
ROOT = os.path.dirname(os.path.dirname(DEV))                                                # repository root
BUILD = os.path.join(ROOT, "build", "digitone2")
FW = os.path.join(DEV, "firmware", "os_260930")

FLASH_SIZE = 16 << 20
HDR = 0x80000

# type: (file, load address). Types follow the section numbers of the extracted files.
COMPRESSED = {3, 7}               # sections the firmware NRV2B-decompresses after reading
SECTIONS = {
    3: ("section_3_MAIN_OS.bin", 0x40000400),
    4: ("section_4_updater.raw", 0x80000400),
    5: ("section_5_meta.raw", 0),
    7: ("section_7_blob.bin", 0),
    8: ("section_8_unknown.bin", 0),
}


class _BitWriter:
    """NRV2B stream writer: 8-bit flag bytes interleaved with data, MSB first,
    reserved in the output exactly where the decoder will fetch them."""

    def __init__(self):
        self.out = bytearray()
        self.flag_pos = None
        self.nbits = 0

    def bit(self, b):
        if self.nbits == 0:
            self.flag_pos = len(self.out)
            self.out.append(0)
            self.nbits = 8
        self.nbits -= 1
        if b:
            self.out[self.flag_pos] |= 1 << self.nbits

    def byte(self, b):
        self.out.append(b)

    def gamma(self, v):
        bits = bin(v)[3:]               # drop the leading 1
        for i, c in enumerate(bits):
            self.bit(c == "1")
            self.bit(i == len(bits) - 1)


def nrv2b_store(data):
    """Valid NRV2B stream holding `data` as literals only, plus the end marker."""
    w = _BitWriter()
    for b in data:
        w.bit(1)
        w.byte(b)
    w.bit(0)
    w.gamma(0x1000002)                 # (m_off - 3) * 256 + 0xFF == 0xFFFFFFFF -> end
    w.byte(0xFF)
    return bytes(w.out)


def nrv2b_decompress(src):
    """Reference decoder (mirrors the updater's routine at 0x80005728)."""
    out = bytearray()
    pos, bb, last = 0, 0, 1

    def getbit():
        nonlocal pos, bb
        bb = (bb * 2) & 0xFFFFFFFF
        if bb & 0xFF == 0:
            bb = src[pos] * 2 + 1
            pos += 1
        return (bb >> 8) & 1

    while True:
        while getbit():
            out.append(src[pos])
            pos += 1
        m_off = 1
        while True:
            m_off = m_off * 2 + getbit()
            if getbit():
                break
        if m_off == 2:
            m_off = last
        else:
            m_off = ((m_off - 3) * 256 + src[pos]) & 0xFFFFFFFF
            pos += 1
            if m_off == 0xFFFFFFFF:
                return bytes(out)
            m_off += 1
            last = m_off
        m_len = getbit() * 2 + getbit()
        if m_len == 0:
            m_len = 1
            while True:
                m_len = m_len * 2 + getbit()
                if getbit():
                    break
            m_len += 2
        m_len += m_off > 0xD00
        for _ in range(m_len + 1):
            out.append(out[-m_off])


def build():
    flash = bytearray(b"\xFF" * FLASH_SIZE)
    boot = open(os.path.join(FW, "section_2_bootstrap.bin"), "rb").read()
    flash[0:len(boot)] = boot

    entries = []
    off = 0x1000                      # payloads start 4 KB after the header
    for typ, (fname, load) in SECTIONS.items():
        data = open(os.path.join(FW, fname), "rb").read()
        if typ in COMPRESSED:
            # 8-byte header the updater skips: raw size + stream size (contents unchecked)
            stream = nrv2b_store(data)
            assert nrv2b_decompress(stream) == data
            data = struct.pack(">II", len(data), len(stream)) + stream
        flash[HDR + off:HDR + off + len(data)] = data
        entries.append((typ, off, len(data), load))
        off = (off + len(data) + 0xFFF) & ~0xFFF

    hdr = bytearray(b"\x00" * 0x20)
    hdr[0:8] = struct.pack(">4sI", b"ELE3", 0x34)
    hdr[0x1C:0x20] = struct.pack(">I", len(entries))
    flash[HDR:HDR + 0x20] = hdr
    for i, e in enumerate(entries):
        flash[HDR + 0x20 + 16 * i:HDR + 0x30 + 16 * i] = struct.pack(">4I", *e)
    return flash, entries


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BUILD, "flash.bin")
    flash, entries = build()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "wb").write(flash)
    print(f"{os.path.relpath(out, ROOT)}: bootstrap at 0x000000, header at 0x{HDR:06x}")
    for typ, off, length, load in entries:
        print(f"  type {typ}: flash 0x{HDR + off:06x} +0x{length:x} -> load 0x{load:08x}")


if __name__ == "__main__":
    main()
