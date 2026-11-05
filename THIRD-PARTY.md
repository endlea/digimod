# Third-party code and credits

digimod's own code is MIT (see [LICENSE](LICENSE)). The parts below come from other people's work and keep their
licenses. If you hand a built firmware to someone else, the AGPL parts mean you also owe them the corresponding
source: point them at this repository and the upstream projects listed here.

## Mods

| Mod | Based on | License |
|---|---|---|
| [Plaits](mods/plaits/README.md) | [digikit](https://github.com/ElekLabs/digikit) by Em D (machines/plaits, core/expansion, core/params, core/dsp-inline), as pinned by Kári Garðarsson's [Digitakt II All-in-One](https://github.com/karigardarsson/digitakt-ii-research) v0.3.0, with the optimized Plaits integration by Tyler Neely ([spacejam](https://github.com/spacejam)) and Kári's UI 2.1 graphics. Plaits itself: Emilie Gillet / [Mutable Instruments](https://github.com/pichenettes/eurorack), MIT. | AGPL-3.0-or-later with the digikit NOTICE |
| [Musical WAVE / Déjà Vu](mods/musical-wave/README.md) | Kári Garðarsson's Déjà Vu V5.1 and Musical WAVE V4, from the [Digitakt II All-in-One](https://github.com/karigardarsson/digitakt-ii-research). The Déjà Vu idea follows Mutable Instruments Marbles; DV-F / DV-T used Bezronczek's [Syntakt Firmware Workbench](https://github.com/Bezronczek/syntakt-firmware-workbench) as a behavioural reference. | AGPL-3.0-or-later (All-in-One edition) |
| [One-Shot Zoom](mods/oneshot-zoom/README.md) | Kári Garðarsson's ONESHOT Zoom V11, from the [Digitakt II All-in-One](https://github.com/karigardarsson/digitakt-ii-research). | AGPL-3.0-or-later (All-in-One edition) |
| [Polyphony](mods/polyphony/README.md) | written for digimod | MIT |
| [System Load](mods/sysload/README.md) | written for digimod | MIT |

The segment loader every build with expansion-RAM mods gets (`server/core/loader.s`) follows the scheme of
digikit's core/expansion, so it is AGPL-3.0-or-later too.

digikit's terms ask that its attribution stays visible: "digikit by Em D - https://github.com/ElekLabs/digikit".
It is in the page footer and in [LICENSES/digikit-NOTICE](LICENSES/digikit-NOTICE); the full AGPL text is in
[LICENSES/AGPL-3.0.txt](LICENSES/AGPL-3.0.txt).

## Tools and the emulator

| Path | Origin | License |
|---|---|---|
| `third_party/elektron-firmware-tool/` | [elektron-firmware-tool](https://github.com/mischa85/elektron-firmware-tool) by Marcel Bierling (mischa85), commit `a5bce9a` | MIT ([LICENSES/elektron-firmware-tool-MIT.txt](LICENSES/elektron-firmware-tool-MIT.txt)) |
| `third_party/dt2/`, `third_party/digi2/` | the Digitakt II and Digitone II engines of [elektron-emu](https://github.com/xerydian/elektron-emu) by xerydian, commit `03961c6`, with our changes (marked `digidive:` in the code: the real SHARC DSP, +Drive fixes, the sample header, live resync, address profiles for modified firmware) | no license published upstream; included with credit |
| `third_party/dt2/panel/digitakt2_panel.png` | Elektron's panel photo, as shipped by elektron-emu | Elektron's |
| `third_party/*/patches/` | patches against [Unicorn Engine](https://github.com/unicorn-engine/unicorn) 2.1.4, which `make setup` downloads and builds | GPL-2.0 (Unicorn) |

Elektron, Digitakt and Digitone are trademarks of Elektron Music Machines. digimod is not affiliated with or
endorsed by Elektron, and contains no Elektron firmware.
