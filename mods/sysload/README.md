# System Load (Digitakt II OS 1.16), v0.3

SETTINGS > SYSTEM gets a fifth item, **SYSTEM LOAD** (after STORAGE, with its own icon). It opens a page drawn
like STORAGE, updated about 30 times a second while it is open:

- two columns with STORAGE's animated cylinder: **DSP** load now with its headroom, and **VOICES** playing out of
  16 with the ones left;
- left: DSP now, average and peak (falls back slowly), voices, and the DSP cycles the last audio frame took out
  of 666K (32 samples at 48 kHz on the 1 GHz SHARC).

v0.3: VOICES counts the voices that sound, not the ones the DSP still runs. A PLAITS voice never tells the DSP it
is free, and a sample voice plays to the end of its sample long after the amp envelope has closed, so v0.2 counted
voices that were long silent (with Polyphony on 8 voices, all 8). With the Polyphony mod (0.1.8 and later) a voice
counts while its note is held and for a second after; without it, as before.

v0.2: no CPU column (v0.1 sampled the ColdFire at the OS tick, about 60 Hz: on hardware it jumped around and
meant nothing); a speedometer icon.

Nothing runs while the page is closed: the hooks only check whether the view is ours.

## How

- **DSP:** the SHARC reports its own load. Every DSPI2 answer starts with the cycles its last frame took (answer
  words 0-1; the OS's `FUN_400cd6ce` turns them into headroom per mille and uses it only to pace background jobs).
  The mod reads the same words.
- **VOICES:** the channels the Polyphony mod marks as sounding (`0x47fdfff8`: "VOIC", then a bit per channel; it
  sees every DSPI2 frame's note-on and note-off bits): the DSP runs the voice (its play position, answer word
  1358 + channel, is not idle) and its note is held or was released less than a second ago. Without Polyphony:
  every channel whose play position is not idle.
- **The page:** the item is STORAGE's own list entry code with our invoker, icon and label (hook at the end of the
  SYSTEM list builder `0x4010a180`); it opens a `FreeSpaceMenuView` (STORAGE's view) whose draw, destructors and
  tick (vtable `0x402256d8` +0x10, +0, +4, +0x2c) the mod takes over for that one view. The cylinder is STORAGE's
  frame set (`0x44f35270`), picked by the load.
- Code and state: 1.5 KB at `0x47ec2000` (expansion RAM).

## Checked (emulator)

- The item, its icon and the page; STORAGE still opens and draws as stock.
- Live DSP load: idle 32%, 6-7 voices 38-39%, back to 33-34% as they end; about 34 redraws a second.

## Limits

- v0.1 on hardware: the page and the DSP load worked; the CPU column was noise (removed).
- The DSP figure is the SHARC's own count; in the emulator it is the interpreter's instruction count.
