#!/usr/bin/env python3
"""Decode the UART8 front-panel stream into screen images.

Display packets are 10 bytes: [0x10 + page] [x] [8 bytes], page 0-7, x a
multiple of 8 below 128. Each data byte is one 8-pixel column of the page
(LSB = top row), i.e. the usual SSD1306-style 128x64 page layout.
Only changed tiles are sent; 0xB8 marks the end of a frame. Other bytes
(60 02 70 00 version query, 73/60/B7 setup) are reported as non-display bytes.

    .venv/bin/python devices/digitone2/pyemu/screen.py logs/digitone2/pyemu/main/uart8.bin
        -> logs/main/screen.png (last frame), logs/main/frames/NNNN.png (every frame)
"""
import os
import struct
import sys
import zlib

W, H = 128, 64


def command_length(b):
    """Length of an OS -> panel command starting with byte b (from the senders in
    MAIN_OS: 0x40011faf6, 0x40131f04, 0x4011fb28 and neighbours)."""
    if 0x10 <= b <= 0x17:
        return 10                 # display tile: [0x10+page][x][8 bytes]
    if 0x40 <= b <= 0x7F:
        return 2                  # 0x40|cmd, arg (60 01 key scan, 60 02 + 70 00 version query, 73 00 ...)
    return {0xB5: 5, 0xB7: 2, 0xB8: 1, 0xB9: 3, 0xBB: 4, 0xBC: 2}.get(b, 1)


def commands(stream):
    """Split the OS -> panel stream into (offset, command bytes)."""
    i = 0
    while i < len(stream):
        n = command_length(stream[i])
        yield i, stream[i:i + n]
        i += n


def parse(stream):
    """Yield ("tile", page, x, data) and ("other", offset, byte) for other commands."""
    for off, cmd in commands(stream):
        if 0x10 <= cmd[0] <= 0x17 and len(cmd) == 10:
            yield ("tile", cmd[0] - 0x10, cmd[1], cmd[2:10])
        else:
            yield ("other", off, cmd[0])


FRAME_END = 0xB8


def frames(stream, with_offsets=False):
    """Framebuffer snapshots at every frame end (0xB8) that followed tile updates,
    plus the final state if it was not closed by a marker. With with_offsets,
    also returns the stream offset just after each snapshot's 0xB8."""
    fb = [[0] * W for _ in range(H)]
    out, offs, other, dirty = [], [], [], False
    for item in parse(stream):
        if item[0] == "other":
            other.append(item[1:])
            if item[2] == FRAME_END and dirty:
                out.append([row[:] for row in fb])
                offs.append(item[1] + 1)
                dirty = False
            continue
        _, page, x, data = item
        for col, byte in enumerate(data):
            for bit in range(8):
                fb[page * 8 + bit][x + col] = byte >> bit & 1
        dirty = True
    if dirty or not out:
        out.append([row[:] for row in fb])
        offs.append(len(stream))
    return (out, other, offs) if with_offsets else (out, other)


def save(fbs, out_dir):
    """screen.png = last frame, frames/NNNN.png = all distinct consecutive frames."""
    frames_dir = os.path.join(out_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    for f in os.listdir(frames_dir):
        if f.endswith(".png"):
            os.remove(os.path.join(frames_dir, f))
    n, prev = 0, None
    for fb in fbs:
        if fb != prev:
            png(fb, os.path.join(frames_dir, f"{n:04d}.png"))
            n += 1
            prev = fb
    png(fbs[-1], os.path.join(out_dir, "screen.png"))
    return n


def png_bytes(fb, scale=4):
    """PNG image of a framebuffer (list of rows of 0/1), scaled up."""
    w, h = len(fb[0]), len(fb)
    rows = []
    for y in range(h * scale):
        line = bytearray([0])
        for x in range(w * scale):
            on = fb[y // scale][x // scale]
            line += b"\xE0\xF0\xFF" if on else b"\x10\x14\x18"
        rows.append(bytes(line))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = struct.pack(">IIBBBBB", w * scale, h * scale, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b""))


def png(fb, path, scale=4):
    with open(path, "wb") as f:
        f.write(png_bytes(fb, scale))


def apply_tile(fb, cmd):
    """Draw one 10-byte tile command into a framebuffer."""
    page, x = cmd[0] - 0x10, cmd[1]
    for col, byte in enumerate(cmd[2:10]):
        for bit in range(8):
            fb[page * 8 + bit][x + col] = byte >> bit & 1


def ascii_art(fb):
    # two pixel rows per character line
    chars = {(0, 0): " ", (1, 0): "▀", (0, 1): "▄", (1, 1): "█"}
    return "\n".join("".join(chars[fb[y][x], fb[y + 1][x]] for x in range(W)) for y in range(0, H, 2))


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "logs/main/uart8.bin"
    stream = open(src, "rb").read()
    fbs, other = frames(stream)
    out_dir = os.path.dirname(src)
    n = save(fbs, out_dir)
    print(f"{len(stream)} bytes, {n} distinct frame(s) -> {out_dir}/frames/, last in {out_dir}/screen.png")
    odd = [(o, b) for o, b in other if b != FRAME_END]
    if odd:
        print("non-display bytes: " + " ".join(f"{b:02x}@{o}" for o, b in odd[:32])
              + (" ..." if len(odd) > 32 else ""))
    print(ascii_art(fbs[-1]))


if __name__ == "__main__":
    main()
