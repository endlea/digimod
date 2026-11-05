#!/bin/bash
# Everything digimod runs, built into data/build/ (once; a few minutes the first time):
#   1. data/build/elektron-firmware-tool   unpack / repack / verify .syx (third_party/elektron-firmware-tool)
#   2. data/build/unicorn-static           Unicorn 2.1.4 with digi2's patches, static (fetched from GitHub)
#   3. data/build/libfcdt.dylib            the Digitakt II engine: dt2's core + emulator/dthost.cpp + the host's
#                                          audio and MIDI (emulator/host)
# The DSP of each firmware is built on demand: emulator/build_dsp.py <section_7_blob.bin>.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$ROOT/data/build
SRC=$ROOT/third_party/dt2
UNI=$OUT/unicorn-static
mkdir -p "$OUT/eft" "$OUT/bin"
# Unicorn's configure insists on pkg-config although it needs no external package: a stub
printf '#!/bin/sh\ncase "$1" in --version) echo 0.29.2 ;; *) exit 1 ;; esac\n' > "$OUT/bin/pkg-config"
chmod +x "$OUT/bin/pkg-config"
export PATH="$ROOT/.venv/bin:$OUT/bin:$PATH"  # cmake, ninja, pkg-config

EFT=$ROOT/third_party/elektron-firmware-tool
for c in "$EFT"/*.c; do cc -std=c11 -O2 -Wall -Wextra -c -o "$OUT/eft/$(basename "${c%.c}").o" "$c"; done
cc -std=c11 -O2 -o "$OUT/elektron-firmware-tool" "$OUT"/eft/*.o
echo "built $OUT/elektron-firmware-tool"

if [ ! -f "$UNI/lib/libunicorn.a" ]; then
  "$ROOT/third_party/digi2/core/scripts/build-unicorn.sh" "$UNI" "$OUT/unicorn-work"
fi

cmake -S "$SRC/core" -B "$OUT/dtcore" -G Ninja -DDTCORE_UNICORN_PREFIX="$UNI" -DCMAKE_BUILD_TYPE=Release
cmake --build "$OUT/dtcore" --target dtcore
H=$ROOT/emulator/host
c++ -std=c++17 -O2 -c -o "$OUT/dthost.o" "$ROOT/emulator/dthost.cpp" -I "$SRC/core/include" -I "$SRC/core/src" -I "$SRC"
cc -O2 -c -o "$OUT/audio_out.o" "$H/audio_out.c" -I "$SRC/core/include"
cc -O2 -c -o "$OUT/midi_in.o" "$H/midi_in.c" -I "$SRC/core/include"
c++ -dynamiclib -o "$OUT/libfcdt.dylib" "$OUT/dthost.o" "$OUT/audio_out.o" "$OUT/midi_in.o" \
  -Wl,-force_load,"$OUT/dtcore/libdtcore.a" "$UNI/lib/libunicorn.a" -lz -lpthread \
  -framework AudioToolbox -framework CoreAudio -framework CoreMIDI -framework CoreFoundation
echo "built $OUT/libfcdt.dylib"
