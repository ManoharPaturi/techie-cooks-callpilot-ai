#!/usr/bin/env bash
# Stops only the processes start.sh launched (never unrelated Ollama sessions).
cd "$(dirname "$0")/.."
pkill -f "python -m backend.main" 2>/dev/null && echo "stopped host app"
for svc in whisper ollama; do
  if [ -f .run/$svc.pid ]; then kill "$(cat .run/$svc.pid)" 2>/dev/null && echo "stopped $svc"; rm -f .run/$svc.pid; fi
done
