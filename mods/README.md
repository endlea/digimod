# mods

The mod storage: one folder per mod. The page lists them and builds the ones you tick into a firmware. Mods still
in development live in `lab/mods/` instead (not in git) and are listed only with `DIGIMOD_LAB=1`.

```
mods/<id>/
├── mod.json       what the page and the build read (below)
└── README.md      what it does, how to try it in the emulator, what was checked on hardware
```

## mod.json

```json
{
  "id": "poly-voices",
  "name": "Polyphony",
  "version": "1.0",
  "description": "One line for the page",
  "firmwares": [
    {"version": "1.16", "main_os_sha256": "57bb4dfa8df07d846adc72fdb4fb0d3cd3c5680c524bf498338460207e008e7d"}
  ],
  "patches": [
    {"section": 3, "offset": "0x2423C6", "from": "4f6e6573686f74", "to": "444947494d4f44"}
  ]
}
```

- `firmwares`: the MAIN OS (section 3) SHA-256 of each firmware the mod is made for; the page offers the mod only on
  those. The version is for people.
- `patches`: overwrite only — `from` and `to` are hex of the same length, `offset` is inside the extracted section
  (`elektron-firmware-tool -i OS.syx -o dir`; MAIN OS is loaded at `0x40000400`, so address = offset + 0x40000400).
- `options` / `require` / `writes` (optional): settings chosen on the page when building. Options are integers
  (`id`, `label`, `type: "int"`, `min`, `max`, `default`, `help`); `require` lists conditions on them (with
  `require_text` for the page); each write puts one byte, `u8` an expression of the options (integers, `+ - * // %`,
  comparisons, `and/or/not`, `x if c else y`), and only inside the mod's own patches (`to` bytes).

- `segments` (optional): code and data in the free top of RAM, `0x47e8b000…0x47fe0000` (`{"dest", "file" or "data"
  (hex), "bss"}`, 4-byte multiples). digimod appends the segments of every mod as one chain to the MAIN OS image (at
  its stock end, `0x4031ff60`) and puts one loader in (`server/core/loader.s`, at `0x40310420`; the reset calls at
  `0x4000053e` call it): at reset it copies each segment to its place and zeroes its bss. Two mods' segments must not
  overlap. The scheme is digikit's core/expansion (ElekLabs/digikit, AGPL-3.0).
- `tails` (optional): bytes a mod appends to a section other than MAIN OS (`{"section", "at": its stock size,
  "file"}`), e.g. the DSP's (section 7) own code; one mod per section.
- `with` (optional): changes a mod makes inside another mod's segments when both are built (`{"<other id>": [{"dest",
  "from", "to"}]}`, `dest` a RAM address inside one of that mod's segments, `from`/`to` hex of the same length). For
  data one mod moves to RAM that another one patches in place, e.g. musical-wave raising the LFO WAVE rows that the
  plaits mod's parameter table copies. Without the other mod they are ignored; a `from` that does not match fails
  the build.

What a build checks before handing out a file (server/firmware.py):
1. every patch finds exactly its `from` bytes (else the mod is for another firmware) and no two mods touch the
   same byte; sections 2 (bootloader) and 4 (updater) are never changed;
2. the tool packs only the changed sections and recomputes checksums and HMAC;
3. the new file passes the tool's checks, has the same device and version, and each of its sections, extracted
   again, is byte-identical to what we meant (patched or untouched); a build with no mods is the original file
   byte for byte.

The emulator runs a build with the address profile of its base OS: a mod must not move or overwrite the code the
profile points at (third_party/dt2/core/machines/digitakt2/profiles.inc.c).
