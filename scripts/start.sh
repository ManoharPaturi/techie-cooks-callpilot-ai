#!/usr/bin/env bash
# Starts whisper.cpp + Ollama (if not already running) and the private host app. Loopback only.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
WHISPER_CPP_DIR="${WHISPER_CPP_DIR:-../vendor/whisper.cpp}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:1.7b}"
HOST_BIND="${HOST_BIND:-127.0.0.1}"
mkdir -p .run

case "$HOST_BIND" in 127.0.0.1|localhost) ;; *) echo "Refusing: HOST_BIND=$HOST_BIND is not loopback"; exit 1;; esac

if ! curl -s -m 1 127.0.0.1:8080/ >/dev/null; then
  echo "Starting whisper.cpp (127.0.0.1:8080)…"
  "$WHISPER_CPP_DIR/build/bin/whisper-server" --host 127.0.0.1 --port 8080 \
    -m "$WHISPER_CPP_DIR/models/ggml-base.en.bin" -t 4 > .run/whisper.log 2>&1 &
  echo $! > .run/whisper.pid
fi

if ! curl -s -m 1 127.0.0.1:11434/api/version >/dev/null; then
  echo "Starting Ollama (127.0.0.1:11434)…"
  OLLAMA_HOST=127.0.0.1:11434 ollama serve > .run/ollama.log 2>&1 &
  echo $! > .run/ollama.pid
  sleep 2
fi
ollama list | grep -q "^$OLLAMA_MODEL" || { echo "Model $OLLAMA_MODEL missing: run 'ollama pull $OLLAMA_MODEL'"; exit 1; }

[ -f frontend/dist/host/index.html ] || (cd frontend && npm install && npm run build)

for _ in $(seq 1 30); do curl -s -m 1 127.0.0.1:8080/ >/dev/null && break; sleep 0.5; done
echo
echo "Host dashboard (private, this Mac only): http://127.0.0.1:${HOST_PORT:-8765}"
exec uv run python -m backend.main
