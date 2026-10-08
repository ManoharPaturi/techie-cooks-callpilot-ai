#!/usr/bin/env bash
# One-time setup, safe to re-run: installs tools, the local models, whisper.cpp and app dependencies.
# Every step is skipped when it is already done, so a second run takes a few seconds.
#
#   ./scripts/setup.sh                   # everything
#   CALLPILOT_SKIP_MODELS=1 ./scripts/setup.sh   # skip the ~6 GB model downloads (used by CI)
#   CALLPILOT_YES=1 ./scripts/setup.sh           # don't ask before installing missing tools
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

WHISPER_COMMIT="60c0be6ac8fa71b1a2ae2dd938a31a34a508e774"   # the whisper.cpp version CallPilot was tested with
WHISPER_MODEL="base.en"
LIVE_MODEL="${OLLAMA_MODEL:-qwen3:1.7b}"
SUMMARY_MODEL="${SUMMARY_MODEL:-gemma4:e2b}"
OS="$(uname -s)"

step() { printf "\n\033[1m▸ %s\033[0m\n" "$1"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; }
die()  { printf "  \033[31m✗\033[0m %s\n" "$1"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
confirm() {
  [ "${CALLPILOT_YES:-}" = "1" ] && return 0
  [ -t 0 ] || return 0
  read -r -p "  $1 [Y/n] " a; [ -z "$a" ] || [ "$a" = "y" ] || [ "$a" = "Y" ]
}

# ---------------------------------------------------------------- tools
step "Checking tools"
case "$OS" in
  Darwin)
    [ "$(uname -m)" = "arm64" ] || warn "Tested on Apple Silicon; Intel Macs work but are slow."
    if ! have brew; then
      die "Homebrew is needed. Install it from https://brew.sh (one command, asks for your password), then run this again."
    fi
    missing=()
    for t in uv node cmake git; do have "$t" || missing+=("$t"); done
    if [ "${CALLPILOT_SKIP_MODELS:-}" != "1" ] && ! have ollama; then missing+=("ollama"); fi
    if [ ${#missing[@]} -gt 0 ]; then
      confirm "Install with Homebrew: ${missing[*]}?" || die "Please install: ${missing[*]}"
      brew install "${missing[@]}"
    fi
    ;;
  Linux)
    missing=()
    for t in git cmake curl; do have "$t" || missing+=("$t"); done
    have c++ || missing+=("build-essential")
    if [ ${#missing[@]} -gt 0 ]; then
      have apt-get || die "Please install: ${missing[*]}"
      confirm "Install with apt: ${missing[*]}?" || die "Please install: ${missing[*]}"
      sudo apt-get update -qq && sudo apt-get install -y -qq "${missing[@]}"
    fi
    if ! have uv; then
      confirm "Install uv (Python package manager) from astral.sh?" || die "Please install uv: https://docs.astral.sh/uv/"
      curl -LsSf https://astral.sh/uv/install.sh | sh
      export PATH="$HOME/.local/bin:$PATH"
    fi
    if ! have node; then die "Please install Node.js 20+ (https://nodejs.org or your package manager), then run this again."; fi
    if [ "${CALLPILOT_SKIP_MODELS:-}" != "1" ] && ! have ollama; then
      confirm "Install Ollama from ollama.com (asks for sudo)?" || die "Please install Ollama: https://ollama.com/download"
      curl -fsSL https://ollama.com/install.sh | sh
    fi
    ;;
  *)
    die "Unsupported system: $OS. CallPilot runs on macOS (tested) and Linux. On Windows, use WSL2 (Ubuntu)."
    ;;
esac
for t in uv node git cmake; do have "$t" && ok "$t" || die "$t is still missing"; done
node -e 'process.exit(parseInt(process.versions.node) >= 20 ? 0 : 1)' || die "Node.js 20+ is needed (found $(node -v))."

# ---------------------------------------------------------------- whisper.cpp
step "Speech to text (whisper.cpp)"
if [ -n "${WHISPER_CPP_DIR:-}" ]; then W="$WHISPER_CPP_DIR"
elif [ -x "../vendor/whisper.cpp/build/bin/whisper-server" ]; then W="$(cd ../vendor/whisper.cpp && pwd)"
else W="$ROOT/vendor/whisper.cpp"; fi
if [ ! -d "$W/.git" ] && [ ! -x "$W/build/bin/whisper-server" ]; then
  echo "  downloading whisper.cpp (pinned version)…"
  mkdir -p "$W" && git -C "$W" init -q
  git -C "$W" fetch -q --depth 1 https://github.com/ggml-org/whisper.cpp "$WHISPER_COMMIT"
  git -C "$W" checkout -q FETCH_HEAD
fi
if [ ! -x "$W/build/bin/whisper-server" ]; then
  echo "  building whisper-server (2–4 minutes the first time)…"
  cmake -S "$W" -B "$W/build" -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF > "$ROOT/.setup-whisper.log" 2>&1 \
    && cmake --build "$W/build" -j --config Release --target whisper-server >> "$ROOT/.setup-whisper.log" 2>&1 \
    || die "whisper.cpp build failed, see .setup-whisper.log"
fi
ok "whisper-server built ($W)"
if [ ! -f "$W/models/ggml-$WHISPER_MODEL.bin" ]; then
  echo "  downloading the Whisper $WHISPER_MODEL model (~140 MB)…"
  sh "$W/models/download-ggml-model.sh" "$WHISPER_MODEL" >/dev/null 2>&1 || die "Whisper model download failed"
fi
ok "Whisper $WHISPER_MODEL model"

# ---------------------------------------------------------------- models
step "Local AI models (Ollama)"
if [ "${CALLPILOT_SKIP_MODELS:-}" = "1" ]; then
  warn "skipped (CALLPILOT_SKIP_MODELS=1)"
else
  STARTED_OLLAMA=""
  if ! curl -s -m 2 127.0.0.1:11434/api/version >/dev/null; then
    OLLAMA_HOST=127.0.0.1:11434 ollama serve > /dev/null 2>&1 &
    STARTED_OLLAMA=$!
    for _ in $(seq 1 20); do curl -s -m 1 127.0.0.1:11434/api/version >/dev/null && break; sleep 0.5; done
  fi
  for m in "$LIVE_MODEL" "$SUMMARY_MODEL"; do
    if ollama list | awk 'NR>1{print $1}' | grep -qx "$m"; then ok "$m"
    else
      echo "  downloading $m (this is the big one; retries on network errors)…"
      for attempt in 1 2 3 4 5; do ollama pull "$m" && break; warn "download interrupted, retrying ($attempt/5)"; sleep 3; done
      ollama list | awk 'NR>1{print $1}' | grep -qx "$m" && ok "$m" || die "could not download $m"
    fi
  done
  [ -n "$STARTED_OLLAMA" ] && kill "$STARTED_OLLAMA" 2>/dev/null || true
fi

# ---------------------------------------------------------------- app
step "App dependencies"
uv sync -q && ok "Python packages (uv)"
if [ ! -d frontend/node_modules ]; then (cd frontend && npm ci --silent --no-audit --no-fund); fi
ok "frontend packages (npm)"
if [ ! -f frontend/dist/host/index.html ] || [ -n "$(find frontend/src frontend/host frontend/guest -newer frontend/dist/host/index.html -type f 2>/dev/null | head -1)" ]; then
  (cd frontend && npm run build --silent > /dev/null) || die "frontend build failed (cd frontend && npm run build)"
fi
ok "frontend built"

printf "\n\033[32m✓ Setup complete.\033[0m Start CallPilot with ./start (or double-click “Start CallPilot.command” on a Mac).\n"
