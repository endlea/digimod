# digimod

Custom firmware for the Elektron Digitakt II, built from mods. Pick an official OS, tick the mods you want, and
digimod packs a new `.syx`, then checks it before it lets you download it. On a Mac you can also boot the result in
a built-in Digitakt II emulator before it goes anywhere near your hardware.

> digimod does not include Elektron's firmware. Download the OS yourself from
> [elektron.se](https://www.elektron.se/support-downloads) (Digitakt II → Downloads) and add the `.syx` file.

## Quick start

With Docker (the build service, no emulator):

```bash
make up          # http://127.0.0.1:8700
```

On macOS, natively, with the emulator:

```bash
make setup       # once, a few minutes: Python env, the firmware tool, Unicorn, the emulator engine
make run         # http://127.0.0.1:8700, opens the browser
```

`make help` lists everything else. Both ways read official OS files from `firmware/` and keep their builds in
`data/`, so you can switch between them.

## What's in it

| Mod | What it does |
|---|---|
| [Polyphony](mods/polyphony/README.md) | VOICES per track (1–8) on a new page, FUNC + SAMPLES twice. A track borrows the channels of tracks that aren't playing, from track 16 down. Chords record into the sequencer and are saved with the project. |
| [Plaits](mods/plaits/README.md) | A PLAITS machine: Mutable Instruments Plaits on the DSP, 25 models and four DX7 banks, with Kári Garðarsson's UI. |
| [Musical WAVE / Déjà Vu](mods/musical-wave/README.md) | New LFO waves: Déjà Vu random loops and scale quantizers for SAMPLE TUNE. |
| [One-Shot Zoom](mods/oneshot-zoom/README.md) | Zoom and pan on the ONESHOT waveform with encoder C. |
| [System Load](mods/sysload/README.md) | SETTINGS → SYSTEM → SYSTEM LOAD: live DSP load and voices playing. |

All of them are for Digitakt II OS 1.16 and can be built together. Each README says what was checked in the
emulator and what on hardware.

## How it works

The page has four steps:

1. **Base firmware.** Every `.syx` in `firmware/` that the firmware tool validates (device, version, checksums).
2. **Mods.** The folders in `mods/`. A mod names the exact OS it was made for (by the SHA-256 of its main image),
   so only the ones that fit the chosen firmware can be ticked.
3. **Build.** digimod unpacks the OS, applies the mods, packs it again with
   [elektron-firmware-tool](https://github.com/mischa85/elektron-firmware-tool), and runs its checks. Only a build
   that passed all of them can be downloaded.
4. **Emulator** (macOS). Boots the original OS or a build with the real DSP program, and shows the panel on the
   page: screen, keys, encoders, sound, +Drive, MIDI.

A build is handed out only if:

- every patch finds exactly the bytes it expects to replace, and no two mods touch the same byte;
- the bootloader and the updater sections are never changed;
- the new file passes the tool's checks (SysEx checksums, container checksum, HMAC) with the same device and version;
- every section, extracted again from the new file, is byte-identical to what was meant to go in. A build with no
  mods comes out identical to the original, byte for byte.

These checks catch a broken file, not a broken mod. A mod with a bug can still hang the device, so try it in the
emulator first. Flashing modified firmware is at your own risk.

### Flashing and getting back

Send the `.syx` with Elektron Transfer, or copy it to the device and use SETTINGS → SYSTEM → OS UPGRADE.

If a build hangs the device, it can always be reflashed from the startup menu, which runs without the main OS: hold
**FUNC** while powering on and choose **OS UPGRADE**, then send a working `.syx`. Over USB, connect the Digitakt
straight to the computer: through a hub or a dock it may not show up in this mode. A MIDI cable into its MIDI IN
works too (about 12 minutes for an OS). Don't pick EMPTY RESET or FACTORY RESET there: they erase your data.

## Layout

```
digimod/
├── Makefile, docker-compose.yaml, docker/    how to run it
├── server/          the service, Python standard library only
│   ├── app.py       HTTP server and its API (listed at the top of the file)
│   ├── firmware.py  inspect, extract, build with mods, verify
│   ├── mods.py      the mod storage
│   ├── emulator.py  runs the emulator on a firmware
│   └── core/        the segment loader builds put into the OS
├── web/             the page
├── mods/            the mods, one folder each (format: mods/README.md)
├── emulator/        the Digitakt II emulator (macOS): build scripts, the native DSP build, the panel's host
├── tests/smoke.py   builds every mod onto every firmware in firmware/ (make test)
├── third_party/     the firmware tool and the emulator engines (THIRD-PARTY.md)
├── firmware/        official OS files (not in git)
└── data/            everything generated: tools, builds, extracted firmwares, emulator machines (not in git)
```

## Writing a mod

A mod is a folder with a `mod.json` and a README. The format, including code segments in free RAM and data
appended to the DSP's section, is in [mods/README.md](mods/README.md). Mods that are still in development go into
`lab/mods/` (not in git) and show up on the page with `DIGIMOD_LAB=1 make run`.

## What we're working on

**Advanced Stretching**: [Signalsmith Stretch](https://github.com/Signalsmith-Audio/signalsmith-stretch) inside the
stock STRETCH machine, as a MODE parameter with two new modes, SHORT (tighter, for drums) and HIGH (smoother, for
pads and vocals). It sounds right in the emulator, but on hardware the DSP takes 7–12× longer than the emulator
says: the frame's working set doesn't fit the DSP's data cache, and each miss to external memory costs about 90
cycles. Next is a cheaper real-time mode for SHORT and rendering HIGH ahead of time, in the background. Not in the
storage until it runs cleanly on hardware.

## Credits

Plaits, Musical WAVE / Déjà Vu and One-Shot Zoom are ports of other people's work: digikit by Em D -
https://github.com/ElekLabs/digikit, Kári Garðarsson's
[Digitakt II research and All-in-One](https://github.com/karigardarsson/digitakt-ii-research), Tyler Neely
([spacejam](https://github.com/spacejam)), and Emilie Gillet's
[Mutable Instruments](https://github.com/pichenettes/eurorack) Plaits. The firmware tool is by
[mischa85](https://github.com/mischa85/elektron-firmware-tool); the emulator is built on xerydian's
[elektron-emu](https://github.com/xerydian/elektron-emu). Details and licenses: [THIRD-PARTY.md](THIRD-PARTY.md).

## License

MIT for digimod's own code. The mods derived from digikit and the All-in-One are AGPL-3.0-or-later, and the
emulator engines and the firmware tool keep their own terms; see [THIRD-PARTY.md](THIRD-PARTY.md).

Elektron, Digitakt and Digitone are trademarks of Elektron Music Machines. digimod is not affiliated with Elektron.
