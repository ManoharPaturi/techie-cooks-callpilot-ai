"""Integration fixtures ONLY: the backend reads WAVs directly to test VAD -> ASR -> rules without Chrome.
This never substitutes for the interactive demo, where replay audio is played by the browser."""
import wave
from pathlib import Path

import httpx
import numpy as np

from backend import asr, safety
from backend.audio import EnergyVAD, resample_to_16k, to_wav_bytes
from backend.notes import NoteStore

from .conftest import requires_whisper

DEMO = Path(__file__).parent.parent / "demo"


def load(name: str) -> tuple[np.ndarray, int]:
    with wave.open(str(DEMO / name)) as w:
        sr = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
    return pcm, sr


def segments(name: str):
    pcm, sr = load(name)
    vad, n, out = EnergyVAD(sample_rate=sr), int(sr * 0.02), []
    for i in range(0, len(pcm), n):
        out += vad.push(i * 1000 / sr, pcm[i:i + n])
    return out + vad.flush()


def test_vad_finds_sentences_in_demo_wav():
    assert 4 <= len(segments("remote_scam.wav")) <= 9


@requires_whisper
async def test_scam_wav_through_vad_asr_rules():
    texts = []
    async with httpx.AsyncClient() as client:
        for seg in segments("remote_scam.wav"):
            texts.append(await asr.transcribe(client, to_wav_bytes(resample_to_16k(seg.samples, seg.sample_rate))))
    joined = " ".join(texts).lower()
    assert "otp" in joined or "one-time password" in joined
    assert any(safety.scan(t) == "otp_or_code" for t in texts)


def test_note_retrieval_finds_bulk_upload_paragraph():
    store = NoteStore()
    store.import_text("client_agreement.md", (DEMO / "client_agreement.md").read_text())
    top = store.search("Is bulk product upload included in the package?")
    assert top and "Bulk product upload" in top[0].content
    assert store.search("zebra quantum") == []


def test_reimporting_same_note_replaces_it():
    store = NoteStore()
    text = (DEMO / "client_agreement.md").read_text()
    first = store.import_text("client_agreement.md", text)
    store.import_text("client_agreement.md", text)
    assert store.files == ["client_agreement.md"]
    assert len(store.paragraphs) == len(first)
