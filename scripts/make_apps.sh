#!/usr/bin/env bash
# Builds "Start CallPilot.app" and "Stop CallPilot.app" next to the repo folder. Real apps open Terminal directly,
# so they work even when .command files are associated with an editor (e.g. Antigravity / VS Code).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$(dirname "$REPO")}"
rm -rf "$DEST/Start CallPilot.app" "$DEST/Stop CallPilot.app"
osacompile -o "$DEST/Start CallPilot.app" -e "do shell script \"open -a Terminal \" & quoted form of \"$REPO/scripts/launch.sh\""
osacompile -o "$DEST/Stop CallPilot.app" -e "do shell script quoted form of \"$REPO/scripts/stop.sh\" & \" >/dev/null 2>&1; pkill -f 'tail -n 0 -f .run/app.log' || true\"
display notification \"Whisper, Qwen and the dashboard are stopped.\" with title \"CallPilot stopped\""
echo "Created in $DEST: Start CallPilot.app, Stop CallPilot.app"
