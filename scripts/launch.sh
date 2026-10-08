#!/usr/bin/env bash
# One-click launcher: picks Live (iPhone hotspot) or Replay mode, starts everything, opens the dashboard.
# Close this window or press Ctrl+C to stop CallPilot.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p .run
URL="http://127.0.0.1:8765"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  CallPilot AI — starting (all AI runs on this Mac)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Live mode only when the Mac is actually on the iPhone hotspot; otherwise a stale GUEST_BIND would crash startup.
HOTSPOT_IP=""
for ifc in en0 en1 en2; do
  ip=$(ipconfig getifaddr "$ifc" 2>/dev/null || true)
  case "$ip" in 172.20.10.*) HOTSPOT_IP=$ip; break;; esac
done
touch .env
if [ -n "$HOTSPOT_IP" ]; then
  echo "▸ iPhone hotspot detected ($HOTSPOT_IP) → Live phone calls enabled"
  ./scripts/setup_hotspot.sh >/dev/null || { echo "  hotspot setup failed — continuing in Replay mode"; HOTSPOT_IP=""; }
fi
if [ -z "$HOTSPOT_IP" ]; then
  echo "▸ Not on the iPhone hotspot → Replay mode (connect to the hotspot and relaunch for live calls)"
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
  if ! kill -0 "$APP_PID" 2>/dev/null; then echo; echo "✗ Startup failed. Last log lines:"; tail -15 .run/app.log; read -r -p "Press Enter to close…" _; exit 1; fi
  sleep 1
done

STATUS=$(curl -s -m 2 "$URL/api/status")
echo "$STATUS" | grep -q '"llm":{[^}]*"ok":true' && echo "  ✓ Qwen ready" || echo "  ! Qwen not ready yet (it may still be loading)"
echo "$STATUS" | grep -q '"asr":{[^}]*"ok":true' && echo "  ✓ Whisper ready" || echo "  ! Whisper not ready yet"

if [ -d "/Applications/Google Chrome.app" ]; then open -a "Google Chrome" "$URL"; else open "$URL"; fi
echo
echo "✓ CallPilot is running → $URL"
echo "  Tips: wear headphones · quit other heavy apps (8 GB Mac) · close this window to stop"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Live log:"
tail -n 0 -f .run/app.log &
TAIL_PID=$!
wait "$APP_PID"
kill "$TAIL_PID" 2>/dev/null
echo "CallPilot exited."
