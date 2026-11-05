# Polyphony (Digitakt II OS 1.16), v0.1.8

Polyphony as a per-track setting, the way Digitone II does it. A track with VOICES above 1 plays chords: it borrows
the DSP channels of tracks that are not playing anything, starting from track 16 and going down.

## Using it

Press **FUNC + SAMPLES** twice. The first press opens the stock SAMPLES menu (+DRIVE / PROJECT RAM), the second one
the VOICES page, a third closes it, the same way FUNC + FX steps through its pages.

| On the VOICES page | |
|---|---|
| ← / →, encoder A, LEVEL/DATA | pick the track (1–16, wraps; opens on the active track) |
| ↑ / ↓, encoder B | VOICES of that track (1–8) |
| NO, a page key, FUNC + SAMPLES | close |

The page shows TRACK and VOICES, then either `PLAYS ON n` (how many channels the track got; fewer than VOICES means
there were not enough free tracks) or `LENT TO n` (this track's channel is in use by track n). Below that, the 16
channels: the selected track inverted, the channels it plays on framed, busy tracks underlined.

- **Saved with the project** (SAVE PROJECT AS) and restored when it loads; CREATE NEW resets every track to 1. The
  value goes into five bytes of each track's MIDI settings that the OS writes as zeros and never reads, so the same
  project opens on the stock OS as usual, mono.
- **Chords in the sequencer**: live recording (REC + PLAY, notes from MIDI or from the trig keys in KEYBOARD mode)
  stores up to 4 notes per trig in the trig's Note 1–4, the fields MIDI tracks use for chords. Notes played within
  ~0.1 s of each other land on the first note's step. On playback each note goes to its own voice; parameter locks
  apply to the whole chord. On the stock OS such a trig plays its first note.

## Which channels a track borrows

A track with VOICES = N plays on its own channel plus N−1 others, taken from track 16 downward. A track lends its
channel when it is not polyphonic itself, not already lent, and:

- it has no trigs in the pattern while the sequencer runs (when stopped every track is free, so live playing takes
  voices from 16 down even with a full pattern);
- or it is muted (or not soloed): it lends its channel regardless of trigs;
- or it is a MIDI track: its notes go out over MIDI, it never uses its DSP channel;
- and it has not played its own notes from the keys or MIDI in the last few seconds.

When a lending track plays a note of its own it takes its channel back right away and the polyphonic track moves to
another free one. Lowering VOICES returns the lowest channels first, so the pool stays at the top (16, 15, 14…).

Voices are picked as on Digitone: the same note reuses its voice, otherwise the free voice released longest ago,
otherwise the oldest one is stolen. Sequencer trigs rotate over the voices, so overlapping long trigs ring out.

## Where it stays mono

MIDI tracks (they have their own chords), and tracks on REPITCH, GRID or SLICE, where voices do not work correctly
yet. Their channels can still be lent. The VOICES value is remembered and comes back when the track goes back to
ONESHOT, STRETCH, WERP or PLAITS (from the Plaits mod).

## How it works

- **Sound.** Before every DSPI2 frame the mod copies everything the frame carries for the polyphonic track into its
  voice channels: arrays 42, 58, 74, 90, 926, 942, 958, 974, 990 (machine type, filter type and the rest) and the
  48-word parameter block (frame words 109 + 48 × channel). Each voice keeps its own note, velocity and note on/off
  bits, and the parameters the OS has locked on its channel: the OS marks a trig's locks per channel (a 96-bit mask
  at `0x47db461c` + 12 × channel, set by `FUN_400d9204`), and the mod reads that mask. WERP voices keep their own
  sample position.
- **What the OS sees.** UI note messages are rewritten to the owner track, so only its LED flashes and live recording
  writes to it; the record path is told the track is not an audio track, so chord notes go into Note 2–4. The
  waveform playhead follows the track's newest voice. A lending track's own sequencer notes are dropped while its
  channel is lent.
- **Where.** Code and data are one segment in expansion RAM (`0x47f00000`); voice state is allocated from the OS
  heap on first use. Until then every hook passes straight through to the stock code.

| Address (MAIN OS 1.16) | Hook |
|---|---|
| `0x4011fe12`, `0x4012020c` | track note on / note off |
| `0x4013a6b0` | timed sequencer event |
| `0x4002dd76` | DSPI2 frame send (the voice mirror) |
| `0x40034394` | "tracks played" message (LEDs) |
| `0x4011f29c` | UI message post |
| `0x4004905a`, `0x40030d5c` | "is this an audio track?" in live and step recording |
| `0x4002e1d0` | the mute check of sequencer notes in the DSPI2 interrupt |
| `0x400cd726`, `0x400cd746`, `0x400cd764` | the screen's track position getters |
| `0x400dbc66`, `0x400db734`, `0x400e5eee` | kit save, kit load, new project |
| `0x401ee684`, `0x401ee68c`, `0x4001f1a0`, `0x4001f288` | the VOICES page inside SampleFSSelectView |

## Status

- v0.1 was checked on hardware; v0.1.3 is part of the builds that have run on hardware since. v0.1.4 to v0.1.8 are
  checked in the emulator only.
- Checked in the emulator with the real DSP: chords recorded live and played back on borrowed channels, trig locks
  on 3-voice chords, save / load / new project, ONESHOT, STRETCH and WERP voices at the same level as mono.
- v0.1.4 in the emulator: a pattern with locked trigs on every step, VOICES 3 (the notes rotate over channels 4, 10
  and 6), FREQ turned while it plays: every voice follows the track (3328 → 2048 in the frame), and each keeps its
  trig's locked words. With 0.1.3 the voices on 10 and 6 stayed at 3328. A held MIDI chord follows as before.

## Limits

- REPITCH, GRID and SLICE stay mono for now.
- Up to 4 notes per recorded chord (Note 1–4); live playing can use up to 8 voices.
- With trigs on every track of a playing pattern there are no free channels, and the track plays one note.
- Chord notes 2–4 cannot be edited on the trig page (an audio track only shows NOTE there); record them live.

## History

- **0.1.8** — tells System Load which channels sound: the DSP runs the voice and its note is held, or was released
  less than a second ago. Kept for every channel, polyphonic or not, at `0x47fdfff8` ("VOIC", then a bit per
  channel).
- **0.1.7** — VOICES survive a power cycle without saving the project. The OS keeps the working project in a mirror
  it writes to the +Drive, and re-saves the active kit there only when something in the kit changes; a VOICES
  change touched nothing the OS watches. Half a second after a change the mod now asks the OS for a full mirror
  update (Project::updateMirror, `FUN_400435a0`), from the UI task's message loop.
- **0.1.6** — rewritten around what the OS itself knows:
  - trig locks come from the OS's per-channel lock mask; 0.1.4–0.1.5 guessed them from what changed when a note
    started and lost a lock the second time a trig landed on the same voice (locks played only on the first pass);
  - the parameter block starts at frame word 109, not 106: every version before copied it 3 words early, so the
    last 3 parameters of a voice stayed the lending track's and 3 parameters of the channel below were overwritten;
  - voices always get the track's machine type (in 0.1.5 a voice could keep the lending track's, so PLAITS notes
    played through another machine: harsh chords, Plaits still heard after switching the machine);
  - VOICES come back after a restart: a project loads all of its kits and only the active one is saved again as it
    changes, so VOICES are now taken from the active kit, not from the last one loaded.
- **0.1.5** — notes from the keys or MIDI on a lent channel always get the whole track. They kept what the
  channel's last sequencer trig had left there (its locked words or a chord's copy), so with live notes on top of a
  pattern with locks a voice now and then played the lending track's sample, or sounded harsh. A lock now counts
  only when the OS sets a word to something else than the track's own value.
- **0.1.4** — knobs turned while a pattern plays reach every voice. On a lent channel the OS rebuilds the words each
  frame from its own state of that channel, which never sees the track's live changes; 0.1.3 copied only each
  frame's changes into voices with locked trigs, and the OS put its own value back the next frame, so a filter
  turned during playback was heard only on the notes that landed on the track's own channel. Now such a voice gets
  all of the track's settings except the words its trig's locks set.
- **0.1.3** — trig locks work on chords of any size: voices copy a clean copy of the track's settings instead of
  its channel, which carries the locks of whatever note plays there. The VOICES page's encoders step like the OS's
  own lists (they were several times too fast on hardware).
- **0.1.1–0.1.2** — moved from free space in the OS image to expansion RAM, so it fits next to Plaits and Musical
  WAVE; PLAITS tracks are polyphonic.
- **0.1** — first version, checked on hardware.
