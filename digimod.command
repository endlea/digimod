#!/bin/bash
# Double-click on macOS: the native digimod (with the emulator) in the browser. Close the window to stop.
cd "$(dirname "$0")" || exit 1
make run || read -r -p "press Return to close"
