# Musical WAVE / Déjà Vu (Digitakt II OS 1.16), v0.1

New LFO WAVE values on the MOD pages (LFO 1–3), after the stock 0–6:

| WAVE | Name | What it does |
|---|---|---|
| 7 | DV-F | Déjà Vu, fixed: a random step pattern that repeats |
| 8 | DV-T | Déjà Vu, trigger: a new random loop on each trig |
| 9–15 | PMAJ, PMIN, HIRA, WTON, DORI, LYDI, OCTA | scale quantizers for SAMPLE TUNE: the LFO moves the pitch in notes of the scale (major / minor pentatonic, hirajoshi, whole tone, dorian, lydian, octaves) |

The scale waves quantize only SAMPLE TUNE; on any other destination they behave as DV-F. The other LFO controls keep
their meaning.

Origin: Kári Garðarsson's Déjà Vu V5.1 (the hardware-tested 1.16 overlay, `dv-v51-overlay.bin`) with Musical WAVE V4
(`musical_deja_vu_v1.s` from line 362) as the Digitakt II All-in-One v0.3.0 community beta composes them
(https://github.com/karigardarsson/digitakt-ii-research). The Déjà Vu concept follows Mutable Instruments Marbles; DV-F
and DV-T used Bezronczek's Syntakt Firmware Workbench as a behavioral reference (no code from either). The All-in-One
edition is AGPL-3.0-or-later; keep its corresponding source with any build you give to others.

## How it was made for 1.16

- Déjà Vu V5.1 for 1.16 as published: 2030 bytes in the flash cave (`0x403117c4…`) and its 10 patch sites; its
  WAVE maximum raised from 8 to 15 and the WAVE formatter replaced by Musical WAVE's, as the All-in-One does.
- Musical WAVE V4: its 1.17 address constants ported to 1.16 (`port_s17.py`: the same code found in 1.16 with the
  address words masked), assembled at `0x47ec0000` (`musical.bin`, 1964 bytes); hooked into the V5.1 trampolines
  (`0x40311bb0`, `0x40311c00`, `0x40311c30`) and the stock post-apply call `0x4013961c` (1.17 `0x401398f8`).
  Made by `make_musical.py`.
- With the Plaits mod (`with.plaits` in mod.json): Plaits' core/params keeps a copy of the parameter table in RAM;
  the same three WAVE rows there (`0x47e8dab0`, `0x47e8dd08`, `0x47e8df60`) get the same maximum and formatter.

## Checked (emulator)

- Alone, with Plaits, and with Plaits, Polyphony and One-Shot Zoom: turning WAVE on the MOD page goes RAMP → DV-F →
  PMAJ … HIRA … DORI … OCTA and stops at OCTA.
- Not checked here: the sound of DV-F / DV-T and the scale quantizing (the All-in-One's own tests cover them on
  1.17), and save/reload of projects that use them.

## Limits

- Not on hardware in this form (V5.1 alone is hardware-tested on 1.16).
- Takes the flash cave `0x403117c4…0x40312000`; any other mod there conflicts (Polyphony v0.2 moved out of it).
- A project using WAVE 7–15 opened on a stock OS: the stock LFO does not know these values.
