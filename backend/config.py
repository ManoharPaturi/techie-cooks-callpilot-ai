"""Runtime configuration. Values come from the environment (see .env.example)."""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    host_bind: str = os.environ.get("HOST_BIND", "127.0.0.1")
    host_port: int = int(os.environ.get("HOST_PORT", "8765"))
    whisper_url: str = os.environ.get("WHISPER_URL", "http://127.0.0.1:8080/inference")
    whisper_model_name: str = os.environ.get("WHISPER_MODEL_NAME", "ggml-base.en")
    whisper_prompt: str = os.environ.get("WHISPER_PROMPT", "")
    ollama_url: str = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
    ollama_model: str = os.environ.get("OLLAMA_MODEL", "qwen3:1.7b")  # scam checks (measured best: 20/20)
    # Live assistant answers/auto-replies. Same model as scam checks by default: on 8 GB two models can't stay loaded,
    # and Ollama evicting/reloading them mid-call made alerts 2-4x slower (measured).
    assistant_model: str = os.environ.get("ASSISTANT_MODEL", os.environ.get("OLLAMA_MODEL", "qwen3:1.7b"))
    # After-call summary + follow-up tasks. Defaults to the live model, so only ONE model is ever in memory (8 GB Macs).
    # Set SUMMARY_MODEL=gemma4:e2b to have Gemma 4 write it instead (it loads after the call, then unloads).
    summary_model: str = os.environ.get("SUMMARY_MODEL", os.environ.get("OLLAMA_MODEL", "qwen3:1.7b"))
    ollama_timeout_s: float = float(os.environ.get("OLLAMA_TIMEOUT_S", "45"))
    vad: str = os.environ.get("VAD", "auto")  # auto (Silero if available) | silero | energy
    llm_workers: int = int(os.environ.get("LLM_WORKERS", "1"))
    safety_num_predict: int = int(os.environ.get("SAFETY_NUM_PREDICT", "96"))
    persist_raw_audio: bool = _bool("PERSIST_RAW_AUDIO", False)
    persist_transcripts: bool = _bool("PERSIST_TRANSCRIPTS", False)
    dev_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            o.strip() for o in os.environ.get("DEV_ORIGINS", "").split(",") if o.strip()
        )
    )

    guest_enabled: bool = _bool("GUEST_ENABLED", False)
    guest_bind: str = os.environ.get("GUEST_BIND", "")
    guest_port: int = int(os.environ.get("GUEST_PORT", "8443"))
    guest_domain: str = os.environ.get("GUEST_PUBLIC_DOMAIN", "")
    guest_tls_cert: str = os.environ.get("GUEST_TLS_CERT", "")
    guest_tls_key: str = os.environ.get("GUEST_TLS_KEY", "")

    @property
    def guest_base_url(self) -> str:
        return f"https://{self.guest_domain}:{self.guest_port}"

    @property
    def guest_allowed_hosts(self) -> set[str]:
        hosts = {f"{self.guest_domain}:{self.guest_port}"}
        if self.guest_port == 443:
            hosts.add(self.guest_domain)
        return hosts

    @property
    def guest_allowed_origins(self) -> set[str]:
        return {self.guest_base_url, f"https://{self.guest_domain}"} if self.guest_port == 443 else {self.guest_base_url}

    @property
    def allowed_origins(self) -> set[str]:
        return {
            f"http://127.0.0.1:{self.host_port}",
            f"http://localhost:{self.host_port}",
            *self.dev_origins,
        }

    @property
    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.host_port}", f"localhost:{self.host_port}"}


def assert_loopback(bind: str) -> None:
    """Refuse to start the private host app on anything except loopback."""
    if bind == "localhost":
        return
    try:
        addr = ipaddress.ip_address(bind)
    except ValueError as exc:
        raise SystemExit(f"HOST_BIND={bind!r} is not an IP address; refusing to start") from exc
    if not addr.is_loopback:
        raise SystemExit(f"HOST_BIND={bind!r} is not loopback; the private host app must bind 127.0.0.1")


def assert_guest_bind(bind: str) -> None:
    """Guest listener must bind one specific interface address — never a wildcard, never loopback."""
    try:
        addr = ipaddress.ip_address(bind)
    except ValueError as exc:
        raise SystemExit(f"GUEST_BIND={bind!r} must be an exact IP (e.g. the Mac's Tailscale 100.x address)") from exc
    if addr.is_loopback and _bool("GUEST_DEV_LOOPBACK", False):
        return  # automated local testing only
    if addr.is_unspecified or addr.is_loopback:
        raise SystemExit(f"GUEST_BIND={bind!r} is not allowed; use the exact Tailscale/LAN IP")


settings = Settings()

DEMO_DIR = ROOT / "demo"
FRONTEND_DIST = ROOT / "frontend" / "dist"
GUEST_DIST = ROOT / "frontend" / "dist-guest"
