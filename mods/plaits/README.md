# Mutable Instruments Plaits (Digitakt II OS 1.16), v0.2

A PLAITS machine (FUNC + SRC, at the end of the list): Mutable Instruments Plaits running on the Digitakt II's DSP,
25 models (VA … SQUARE, four DX7 banks). SRC page: TUNE, MODEL, HARM, TIMB, MORPH, MIX, drawn with Kári's UI 2.1
(the model's short name with a 25-position rail; boxes with the value and a six-step bar; MIX as OUT…AUX). With the
DX7 models HARM picks one of 32 patches: the box shows its number, the label under it the patch name. One voice per
track; with the Polyphony mod a PLAITS track plays VOICES like ONESHOT. The original Elektron intro is kept.

Origin: digikit by Em D (https://github.com/ElekLabs/digikit), its modules core/expansion, core/machines,
core/dsp-inline, core/params, machines/plaits and machines/plaits-48k as pinned by the Digitakt II All-in-One v0.3.0
community beta (Kári Garðarsson, https://github.com/karigardarsson/digitakt-ii-research; optimized Plaits integration
Tyler Neely / spacejam, pin `e48d0a2a83f055c549391173247cea02c571927f`). Original Plaits algorithms and tables: Emilie
Gillet / Mutable Instruments, MIT. **License: AGPL-3.0-or-later** (digikit) with its NOTICE; keep the source with any
build you give to others.

## How it was made for 1.16

- digikit's port for dt2-1.16 resolved again (`port_resolve`, with a Ghidra dump of 1.16 in digikit's ColdfireEMAC
  language); one function rule written on 1.17 (`cf.machines.clone.5`) found by its 1.17 address with the address
  words masked. The DSP program of 1.16 and 1.17 is the same, so the DSP objects built for 1.17 apply unchanged.
- patchkit applied `machines/plaits-48k` to stock 1.16 (193 writes); `pk2digimod.py` turned the image into this mod:
  162 patches, 28 expansion segments (23 KB) and the DSP's 428 KB tail. patchkit's own reset loader is left out:
  digimod puts one in for every mod with segments.
- UI 2.1 with the DX7 upper-label fix (v0.2): `controls_ui.s`, `model_short.s`, `controls_ui.c` from the All-in-One's
  graphics overlay, built as the release builds them (built for 1.17 here it is byte-identical to its
  `ui21-dx7-fix.bin`, `7cb3c9ed…`), linked for 1.16 (fill `0x40112ece`, text `0x40113ae8`, pixel `0x4011221c`; the
  stock widget `0x40069b8e` is where it was) at `0x47ea0000` (`ui.bin`, 1084 bytes). It takes slot 45 of
  SourcePageView's vtable (1.16 `0x401ed93c`; 1.17 `0x401edd4c`) and passes every parameter but PLAITS' five to
  the stock widget. Made by `make_plaits_ui.py` after `pk2digimod.py`.
- Intro: the patchkit image changes nothing there (the All-in-One's splash came from its own base image), so the
  stock Elektron intro stays. The All-in-One's credits screen is not added; attribution is this README.

## Checked (emulator, the real DSP program on the interpreter)

- The DSP section equals the All-in-One's for 1.17 byte for byte (`6254560c…`).
- Boot; PLAITS in the machine list; its SRC page; MIDI notes C and G sound (RMS 12367 and 11144, silence before).
- UI 2.1: VA, WVTBL, DUST, HIHAT, DX7-3, SQUAR on the MODEL box as it turns; DX7-3: HARM 64 → 74, the label under
  it ENTRIX → Etherial5a; ONESHOT's SRC page (the same vtable) draws as stock.
- With Polyphony (VOICES 4 on the PLAITS track): a MIDI chord C E G sounds as three voices at the same level
  (262 / 330 / 392 Hz: 4636 / 4079 / 3962), the same with all four mods built together (Plaits, Polyphony, Musical
  WAVE, One-Shot Zoom).

## Limits

- Not on hardware. The All-in-One notes a recurring trigger click on hardware and DSP load limits (start with one
  PLAITS track; no 16-track guarantee).
- DSP load: every Polyphony voice is one more PLAITS on the DSP; start with few voices.
