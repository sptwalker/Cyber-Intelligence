#!/bin/sh
set -eu

PROFILE_DIR="${CHROME_PROFILE_DIR:-/profile/chromium}"
mkdir -p "$PROFILE_DIR"

Xvfb "${DISPLAY:-:99}" -screen 0 1440x900x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
sleep 1
x11vnc -display "${DISPLAY:-:99}" -rfbport 5900 -localhost -forever -shared -nopw >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc/ 127.0.0.1:6080 127.0.0.1:5900 >/tmp/novnc.log 2>&1 &

chromium \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --disable-background-networking \
  --disable-default-apps \
  --no-first-run \
  --no-default-browser-check \
  --window-size=1440,900 \
  --user-data-dir="$PROFILE_DIR" \
  --disable-extensions-except=/opt/opencli-extension \
  --load-extension=/opt/opencli-extension \
  about:blank >/tmp/chromium.log 2>&1 &

exec /opt/venv/bin/python -m yuqing.collector_service
