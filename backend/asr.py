"""Client for the local whisper.cpp HTTP server (127.0.0.1 only)."""
from __future__ import annotations

import re

import httpx

from .config import settings

# Whisper emits these on silence/noise; they are not speech.
_NON_SPEECH = re.compile(r"^\s*[\[\(].*?[\]\)]\s*$")
_HALLUCINATIONS = {
    "", "you", "you.", "thank you.", "thanks for watching!", "thanks for watching.", "thank you for watching.",
    "thank you for watching!", "please subscribe.", "bye.", "bye!", "okay.", "so.",
}


class ASRError(RuntimeError):
    pass


_SENTENCE = re.compile(r"[^.!?]+[.!?]?")


def is_repetition_loop(text: str, min_repeats: int = 3) -> bool:
    """Whisper's decoder sometimes loops ("I am not going to. I am not going to. ..."), typically on echo or noise."""
    sentences = [s.strip().lower() for s in _SENTENCE.findall(text) if s.strip()]
    run = 1
    for prev, cur in zip(sentences, sentences[1:]):
        run = run + 1 if cur == prev else 1
        if run >= min_repeats:
            return True
    return False


def clean_text(text: str) -> str:
    text = " ".join(text.split())
    # Drop bracketed non-speech tags like [BLANK_AUDIO] or (music) anywhere in the line.
    text = re.sub(r"\[[^\]]*\]|\([^)]*(music|noise|silence|blank)[^)]*\)", "", text, flags=re.I).strip()
    if _NON_SPEECH.match(text) or text.lower() in _HALLUCINATIONS or is_repetition_loop(text):
        return ""
    return text


async def transcribe(client: httpx.AsyncClient, wav_bytes: bytes) -> str:
    data = {"response_format": "json", "temperature": "0.0"}
    if settings.whisper_prompt:
        data["prompt"] = settings.whisper_prompt
    try:
        resp = await client.post(
            settings.whisper_url,
            files={"file": ("segment.wav", wav_bytes, "audio/wav")},
            data=data,
            timeout=30.0,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ASRError(f"whisper.cpp request failed: {exc}") from exc
    if "error" in payload:
        raise ASRError(str(payload["error"]))
    return clean_text(str(payload.get("text", "")))


async def health(client: httpx.AsyncClient) -> bool:
    base = settings.whisper_url.rsplit("/", 1)[0]
    try:
        resp = await client.get(base + "/", timeout=2.0)
        return resp.status_code < 500
    except httpx.HTTPError:
        return False


async def warmup(client: httpx.AsyncClient) -> None:
    """First whisper.cpp inference compiles Metal kernels; do it before the demo, not during it."""
    import numpy as np

    from .audio import to_wav_bytes
    try:
        await transcribe(client, to_wav_bytes(np.zeros(16_000, dtype=np.float32)))
    except ASRError:
        pass
