#!/usr/bin/env bash
# Hardware, model, port and dependency checks. Read-only.
set -u
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
WHISPER_CPP_DIR="${WHISPER_CPP_DIR:-../vendor/whisper.cpp}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:1.7b}"
SUMMARY_MODEL="${SUMMARY_MODEL:-gemma4:e2b}"
ok() { printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad() { printf "  \033[31m✗\033[0m %s\n" "$1"; }

echo "Hardware"
MEM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
echo "  $(uname -m), ${MEM_GB} GB RAM, macOS $(sw_vers -productVersion), free: $(df -h ~ | awk 'NR==2{print $4}')"
if [ "$MEM_GB" -le 8 ]; then echo "  profile: 8 GB -> whisper base.en + qwen3:1.7b"; else echo "  profile: 16 GB+ -> may try qwen3:4b after replay works"; fi

echo "Tools"
for t in uv node npm ollama ffmpeg; do command -v $t >/dev/null && ok "$t" || bad "$t missing"; done
[ -x "$WHISPER_CPP_DIR/build/bin/whisper-server" ] && ok "whisper-server built" || bad "whisper-server not built in $WHISPER_CPP_DIR"
[ -f "$WHISPER_CPP_DIR/models/ggml-base.en.bin" ] && ok "ggml-base.en model" || bad "ggml-base.en model missing"

echo "Services (loopback only)"
curl -s -m 2 127.0.0.1:8080/ >/dev/null && ok "whisper.cpp on 127.0.0.1:8080" || bad "whisper.cpp not running"
if curl -s -m 2 127.0.0.1:11434/api/tags | grep -q "\"$OLLAMA_MODEL\""; then ok "Ollama has $OLLAMA_MODEL"; else bad "Ollama not running or $OLLAMA_MODEL not pulled"; fi
if curl -s -m 2 127.0.0.1:11434/api/tags | grep -q "\"$SUMMARY_MODEL\""; then ok "Ollama has $SUMMARY_MODEL (after-call summary)"; else echo "  - $SUMMARY_MODEL not pulled: summaries fall back to $OLLAMA_MODEL (ollama pull $SUMMARY_MODEL)"; fi
curl -s -m 2 127.0.0.1:8765/api/status >/dev/null && ok "CallPilot host on 127.0.0.1:8765" || echo "  - CallPilot host not running"

echo "Demo assets"
for f in demo/remote_scam.wav demo/remote_legit.wav demo/client_agreement.md; do [ -f "$f" ] && ok "$f" || bad "$f missing"; done
[ -f frontend/dist/host/index.html ] && ok "frontend built" || bad "frontend not built (cd frontend && npm run build)"

echo "Listener exposure"
lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk '$9 ~ /:(8765|8080|11434)$/ {print "  " $1 " " $9}'
echo "Manual: select headphones + close-talk mic in Chrome; open http://127.0.0.1:8765"
