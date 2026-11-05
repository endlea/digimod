# One-Shot Zoom (Digitakt II OS 1.16), v0.1

Zoom into the waveform on the ONESHOT SRC page (a track with a loaded sample):

- **press encoder C**: 1× → 2× → 3.75× → 1×;
- **turn encoder C** while zoomed: pan along the sample.

It changes the picture only, not playback or the sample. Where the detail it needs is not there, the page draws and
behaves as stock. The zoom is not saved with the project.

Origin: Kári Garðarsson's ONESHOT Zoom V11 (`musical_deja_vu_v1.s` lines 1–361) from the Digitakt II All-in-One
v0.3.0 community beta (https://github.com/karigardarsson/digitakt-ii-research). The All-in-One edition is
AGPL-3.0-or-later; keep its corresponding source with any build you give to others.

## How it was made for 1.16

- The 1.17 address constants ported to 1.16 (`port_s17.py`): the drawing functions moved (`0x4010ee64` →
  `0x4010eb84`, `0x4010ecd8` → `0x4010e9f8`, `0x4011b126` → `0x4011ae46`, `0x401a04e4` → `0x401a0208`, the data word
  `0x402ab508` → `0x402aafc8`); the key and encoder handlers did not.
- V11 keeps its state (480 bytes) in Déjà Vu's allocation; here it is a zeroed block at the end of its own segment,
  so it needs no Déjà Vu and runs the same with Musical WAVE. Code and state at `0x47ec1000` (`zoom.bin`, 1672 bytes).
- Five hooks, V11's sites: `0x400181fe` (keys), `0x400184ea` (encoders), `0x4001706a` (the waveform draw),
  `0x4010eba4` (the waveform data), `0x4010e9f0` (the waveform view's init); 1.17 `0x4010ee84` and `0x4010ecd0` for
  the last two. Made by `make_zoom.py`.

## Checked (emulator)

- Alone and with Plaits, Polyphony and Musical WAVE: press C → 2×, turn C → panned, press C → 3.75× panned, press C →
  1× (screenshots of the SRC page with the starter kick).
- In the emulator's panel encoder C is push 42 / turn 2 (the OS numbers its keys one higher).

## Limits

- Not on hardware in this form (V11 on 1.17 has hardware reports).
- ONESHOT only; not a waveform view for PLAITS.
