#!/usr/bin/env bash
# Reusable HEADED (non-headless) browser harness for MANUAL OAuth completion.
#
# Runs a real Chromium on a virtual display (Xvfb) exposed over the web via
# noVNC, so a human (via Cursor port-forward of :6080) can SEE and interact with
# the browser — e.g. type real OAuth provider credentials on a consent screen —
# while automation drives the deterministic steps over CDP (:9222).
#
# The browser runs ON THIS BOX, co-located with the stack, so localhost:8180
# (frontend), localhost:8100 (control OAuth callback) and outbound HTTPS to the
# OAuth provider all resolve directly — no fragile multi-port forwarding.
#
#   scripts/ralph/headed_browser.sh start   # start Xvfb+fluxbox+x11vnc+noVNC+chromium
#   scripts/ralph/headed_browser.sh status  # show what's listening
#   scripts/ralph/headed_browser.sh stop
#
# Ports: noVNC web UI = 6080 (open http://localhost:6080/vnc.html), CDP = 9222.
set -u
SCRATCH="${HEADED_SCRATCH:-/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/d74b4a3b-4d61-48b7-8ae0-10a05eefc79e/scratchpad/headed}"
DISPLAY_NUM="${HEADED_DISPLAY:-:99}"
NOVNC_PORT="${HEADED_NOVNC_PORT:-6080}"
CDP_PORT="${HEADED_CDP_PORT:-9222}"
GEO="${HEADED_GEO:-1600x1000x24}"
START_URL="${HEADED_URL:-http://127.0.0.1:8180/login}"
CHROME="${CHROME_PATH:-/usr/bin/chromium-browser}"
mkdir -p "$SCRATCH"

_pids() { pgrep -f "$1" 2>/dev/null; }

start() {
  export DISPLAY="$DISPLAY_NUM"
  # 1. Xvfb virtual display
  if ! _pids "Xvfb $DISPLAY_NUM" >/dev/null; then
    nohup Xvfb "$DISPLAY_NUM" -screen 0 "$GEO" -ac >"$SCRATCH/xvfb.log" 2>&1 &
    sleep 1.5
  fi
  # 2. window manager (helps popups/new-windows behave)
  if ! _pids "fluxbox" >/dev/null; then nohup fluxbox >"$SCRATCH/fluxbox.log" 2>&1 & sleep 0.8; fi
  # 3. x11vnc exporting the display
  if ! _pids "x11vnc.*$DISPLAY_NUM" >/dev/null; then
    nohup x11vnc -display "$DISPLAY_NUM" -forever -shared -nopw -rfbport 5900 -quiet >"$SCRATCH/x11vnc.log" 2>&1 &
    sleep 1
  fi
  # 4. noVNC (websockify serving the web client, bridging 6080->5900)
  if ! _pids "websockify.*$NOVNC_PORT" >/dev/null; then
    nohup websockify --web=/usr/share/novnc "$NOVNC_PORT" localhost:5900 >"$SCRATCH/novnc.log" 2>&1 &
    sleep 1
  fi
  # 5. headed chromium with remote debugging (CDP)
  if ! _pids "remote-debugging-port=$CDP_PORT" >/dev/null; then
    nohup "$CHROME" \
      --no-sandbox --disable-gpu --disable-dev-shm-usage \
      --no-first-run --no-default-browser-check --start-maximized \
      --remote-debugging-port="$CDP_PORT" \
      --user-data-dir="$SCRATCH/chrome-profile" \
      "$START_URL" >"$SCRATCH/chrome.log" 2>&1 &
    sleep 3
  fi
  status
}

status() {
  echo "DISPLAY=$DISPLAY_NUM noVNC=:$NOVNC_PORT CDP=:$CDP_PORT"
  for pat in "Xvfb $DISPLAY_NUM" "fluxbox" "x11vnc.*$DISPLAY_NUM" "websockify.*$NOVNC_PORT" "remote-debugging-port=$CDP_PORT"; do
    p=$(_pids "$pat"); echo "  [$([ -n "$p" ] && echo UP || echo DOWN)] $pat  ${p:-}"
  done
  echo "CDP json/version:"; curl -s -m 3 "http://127.0.0.1:$CDP_PORT/json/version" 2>/dev/null | head -c 300; echo
}

stop() {
  for pat in "remote-debugging-port=$CDP_PORT" "websockify.*$NOVNC_PORT" "x11vnc.*$DISPLAY_NUM" "fluxbox" "Xvfb $DISPLAY_NUM"; do
    pkill -f "$pat" 2>/dev/null && echo "killed $pat"
  done
}

case "${1:-start}" in
  start) start ;;
  status) status ;;
  stop) stop ;;
  *) echo "usage: $0 {start|status|stop}"; exit 2 ;;
esac
