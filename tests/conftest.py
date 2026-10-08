import httpx
import pytest

from backend.config import settings


def _up(url: str) -> bool:
    try:
        httpx.get(url, timeout=1.5)
        return True
    except httpx.HTTPError:
        return False


def _ollama_has_model() -> bool:
    try:
        tags = httpx.get(settings.ollama_url.split("/api/")[0] + "/api/tags", timeout=1.5).json()
        return any(m.get("name") == settings.ollama_model for m in tags.get("models", []))
    except (httpx.HTTPError, ValueError):
        return False


requires_whisper = pytest.mark.skipif(
    not _up(settings.whisper_url.rsplit("/", 1)[0] + "/"), reason="local whisper.cpp server not running"
)
requires_ollama = pytest.mark.skipif(not _ollama_has_model(), reason=f"Ollama model {settings.ollama_model} not available")


@pytest.fixture(scope="session", autouse=True)
def _resolve_role_models():
    """Pick models per role exactly like the app does at startup (assistant falls back if not installed)."""
    import asyncio

    from backend import llm

    async def go():
        async with httpx.AsyncClient() as client:
            try:
                await llm.resolve_models(client)
            except httpx.HTTPError:
                pass
    asyncio.run(go())
    print("\nrole models:", llm.ROLE_MODEL)
