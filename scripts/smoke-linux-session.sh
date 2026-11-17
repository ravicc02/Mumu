#!/usr/bin/env bash
set -euo pipefail

export XDG_RUNTIME_DIR="$(mktemp -d)"
chmod 700 "$XDG_RUNTIME_DIR"
export WAYLAND_DISPLAY=mumu-test
printf '%s' 'isolated-fixture-password' | gnome-keyring-daemon --unlock --components=secrets
weston --backend=headless-backend.so --socket="$WAYLAND_DISPLAY" --idle-time=0 --width=1600 --height=1000 > "$RUNNER_TEMP/mumu-weston.log" 2>&1 &
compositor=$!
trap 'kill "$compositor" 2>/dev/null || true' EXIT
for attempt in $(seq 1 50); do
  if [ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]; then break; fi
  sleep 0.2
done
test -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY"
node scripts/smoke-linux.mjs
