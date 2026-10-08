"""Entry point: one process, two listeners.

- private host app  -> 127.0.0.1:8765 (loopback only, enforced)
- guest app (opt.)  -> exact Tailscale/LAN IP:8443 over HTTPS, guest routes only
They share only the in-process RoomCoordinator.
"""
from __future__ import annotations

import asyncio
import os

import uvicorn

from .config import assert_guest_bind, assert_loopback, settings


def _check_tls(path: str, what: str) -> None:
    if not path or not os.path.isfile(path):
        raise SystemExit(f"{what} not found: {path!r}")


async def _serve() -> None:
    assert_loopback(settings.host_bind)
    servers = [uvicorn.Server(uvicorn.Config(
        "backend.host_app:app", host=settings.host_bind, port=settings.host_port, log_level="info"))]
    print(f"CallPilot host (private): http://127.0.0.1:{settings.host_port}")
    print(f"  ASR: {settings.whisper_model_name} @ {settings.whisper_url}")
    print(f"  LLM: scam checks {settings.ollama_model} · assistant {settings.assistant_model} · after-call summary {settings.summary_model} @ {settings.ollama_url}")
    if settings.guest_enabled:
        assert_guest_bind(settings.guest_bind)
        _check_tls(settings.guest_tls_cert, "GUEST_TLS_CERT")
        _check_tls(settings.guest_tls_key, "GUEST_TLS_KEY")
        if not settings.guest_domain:
            raise SystemExit("GUEST_PUBLIC_DOMAIN is required when GUEST_ENABLED=true")
        servers.append(uvicorn.Server(uvicorn.Config(
            "backend.guest_app:app", host=settings.guest_bind, port=settings.guest_port, log_level="info",
            ssl_certfile=settings.guest_tls_cert, ssl_keyfile=settings.guest_tls_key)))
        print(f"CallPilot guest (HTTPS, {settings.guest_bind}): {settings.guest_base_url}/join/<token>")
    else:
        print("Guest listener disabled (Replay mode only).")
    await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
