#!/usr/bin/env bash
# One-click launcher: sets up anything missing (first run only), picks Live (iPhone hotspot) or Replay mode,
# starts everything and opens the dashboard. Close this window or press Ctrl+C to stop CallPilot.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p .run
URL="http://127.0.0.1:8765"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  CallPilot AI — starting (all AI runs on this Mac)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

pause_exit() { [ -t 0 ] && read -r -p "Press Enter to close…" _; exit 1; }
open_url() {
  if [ "$(uname -s)" = Darwin ]; then
    if [ -d "/Applications/Google Chrome.app" ]; then open -a "Google Chrome" "$1"; else open "$1"; fi
  elif command -v xdg-open >/dev/null; then xdg-open "$1" >/dev/null 2>&1 &
  else echo "  Open $1 in Chrome"; fi
}

echo "▸ Checking setup (the first run installs models and builds speech-to-text: 10–20 minutes)…"
./scripts/setup.sh || { echo; echo "✗ Setup did not finish. Fix the message above and launch again."; pause_exit; }
echo

# Live mode only when the Mac is actually on the iPhone hotspot; otherwise a stale GUEST_BIND would crash startup.
HOTSPOT_IP=""
if [ "$(uname -s)" = Darwin ]; then
  for ifc in en0 en1 en2; do
    ip=$(ipconfig getifaddr "$ifc" 2>/dev/null || true)
    case "$ip" in 172.20.10.*) HOTSPOT_IP=$ip; break;; esac
  done
fi
touch .env
if [ -n "$HOTSPOT_IP" ]; then
  echo "▸ iPhone hotspot detected ($HOTSPOT_IP) → Live phone calls enabled"
  ./scripts/setup_hotspot.sh >/dev/null || { echo "  hotspot setup failed — continuing in Replay mode"; HOTSPOT_IP=""; }
fi
if [ -z "$HOTSPOT_IP" ]; then
  echo "▸ Not on an iPhone hotspot → Replay mode (demo calls). For live phone calls, join the iPhone hotspot and relaunch."
  python3 - <<'PY'
import pathlib
p = pathlib.Path(".env")
lines = [l for l in p.read_text().splitlines() if not l.startswith("GUEST_ENABLED=")]
p.write_text("\n".join(lines + ["GUEST_ENABLED=false"]) + "\n")
PY
fi

echo "▸ Stopping any previous run…"
./scripts/stop.sh >/dev/null 2>&1; sleep 1

echo "▸ Starting Whisper, Qwen and the dashboard…"
./scripts/start.sh > .run/app.log 2>&1 &
APP_PID=$!
TAIL_PID=""
cleanup() { echo; echo "▸ Stopping CallPilot…"; ./scripts/stop.sh >/dev/null 2>&1; [ -n "$TAIL_PID" ] && kill "$TAIL_PID" 2>/dev/null; echo "  stopped. You can close this window."; exit 0; }
trap cleanup INT TERM HUP

for i in $(seq 1 90); do
  if curl -s -m 1 "$URL/api/status" | grep -q '"ok":true'; then break; fi
  if ! kill -0 "$APP_PID" 2>/dev/null; then echo; echo "✗ Startup failed. Last log lines:"; tail -15 .run/app.log; pause_exit; fi
  sleep 1
done

STATUS=$(curl -s -m 2 "$URL/api/status")
echo "$STATUS" | grep -q '"llm":{[^}]*"ok":true' && echo "  ✓ Local AI ready" || echo "  ! Local AI not ready yet (it may still be loading)"
echo "$STATUS" | grep -q '"asr":{[^}]*"ok":true' && echo "  ✓ Whisper ready" || echo "  ! Whisper not ready yet"

open_url "$URL"
echo
echo "✓ CallPilot is running → $URL"
echo "  Tips: wear headphones · quit other heavy apps on 8 GB machines · close this window or press Ctrl+C to stop"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Live log:"
tail -n 0 -f .run/app.log &
TAIL_PID=$!
wait "$APP_PID"
kill "$TAIL_PID" 2>/dev/null
echo "CallPilot exited."
