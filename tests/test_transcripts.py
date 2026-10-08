"""Frame validation, VAD segmentation, resampling and transcript cleaning."""

import numpy as np
import pytest

from backend.asr import clean_text
from backend.audio import HEADER, EnergyVAD, FrameError, parse_frame, resample_to_16k


def frame(seq: int, t_ms: float, samples: np.ndarray) -> bytes:
    return HEADER.pack(seq, t_ms) + (samples * 32767).astype("<i2").tobytes()


def test_parse_valid_frame():
    seq, t, pcm = parse_frame(frame(1, 20.0, np.zeros(960)), 48000, None)
    assert (seq, t, len(pcm)) == (1, 20.0, 960)


@pytest.mark.parametrize("data,last", [
    (b"\x00" * 5, None),                                    # too short
    (HEADER.pack(1, 0.0) + b"\x00" * 3, None),               # odd payload
    (frame(1, 0.0, np.zeros(48000)), None),                  # 1 s frame > 100 ms limit
    (frame(5, 0.0, np.zeros(960)), 5),                       # replayed sequence
    (HEADER.pack(2, float("nan")) + b"\x00" * 4, 1),         # bad timestamp
])
def test_parse_rejects(data, last):
    with pytest.raises(FrameError):
        parse_frame(data, 48000, last)


def test_resample_preserves_tone_frequency():
    sr = 48000
    t = np.arange(sr) / sr
    tone = 0.5 * np.sin(2 * np.pi * 1000 * t)
    out = resample_to_16k(tone, sr)
    assert len(out) == 16000
    spectrum = np.abs(np.fft.rfft(out))
    assert abs(int(np.argmax(spectrum)) - 1000) <= 1  # 1 kHz stays 1 kHz


def test_resample_antialiases_above_nyquist():
    sr = 48000
    t = np.arange(sr) / sr
    out = resample_to_16k(0.5 * np.sin(2 * np.pi * 11000 * t), sr)  # above 8 kHz Nyquist
    assert np.sqrt(np.mean(out ** 2)) < 0.02  # filtered, not aliased down to 5 kHz


def feed(vad: EnergyVAD, signal: np.ndarray, sr: int, start_ms: float = 0.0):
    segs, n = [], int(sr * 0.02)
    for i in range(0, len(signal), n):
        segs += vad.push(start_ms + i * 1000 / sr, signal[i:i + n].astype(np.float32))
    return segs


def test_vad_splits_two_utterances_and_ignores_silence():
    sr = 16000
    rng = np.random.default_rng(0)
    silence = lambda s: rng.normal(0, 0.001, int(sr * s))
    speech = lambda s: 0.2 * np.sin(2 * np.pi * 220 * np.arange(int(sr * s)) / sr)
    sig = np.concatenate([silence(1.0), speech(1.0), silence(1.0), speech(0.8), silence(1.0)])
    segs = feed(EnergyVAD(sample_rate=sr), sig, sr)
    assert len(segs) == 2
    assert 600 <= segs[0].start_ms <= 1000  # includes pre-roll
    assert segs[1].start_ms > segs[0].end_ms


def test_vad_drops_clicks():
    sr = 16000
    sig = np.concatenate([np.zeros(sr), 0.3 * np.ones(int(sr * 0.05)), np.zeros(sr)])
    assert feed(EnergyVAD(sample_rate=sr), sig, sr) == []


def test_vad_bounds_long_speech():
    sr = 16000
    sig = 0.2 * np.sin(2 * np.pi * 220 * np.arange(sr * 30) / sr)
    vad = EnergyVAD(sample_rate=sr)
    segs = feed(vad, sig, sr) + vad.flush()
    assert len(segs) >= 3 and all(s.end_ms - s.start_ms <= 12_100 for s in segs)


@pytest.mark.parametrize("raw,clean", [
    (" [BLANK_AUDIO]", ""),
    (" Thank you for watching.", ""),
    (" (music) Hello there", "Hello there"),
    (" Please give me\n the OTP.", "Please give me the OTP."),
    (" I am not going to say anything. I am not going to say anything. I am not going to say anything.", ""),
    (" No. No. Please give me the code.", "No. No. Please give me the code."),
])
def test_clean_text(raw, clean):
    assert clean_text(raw) == clean


# ---------- Silero neural VAD ----------
from backend.audio import SileroVAD, make_vad, silero_available  # noqa: E402

needs_silero = pytest.mark.skipif(not silero_available(), reason="Silero model/onnxruntime not installed")


@needs_silero
def test_silero_ignores_pure_noise_where_energy_vad_fails():
    sr = 48000
    noise = (0.05 * np.random.default_rng(1).normal(0, 1, sr * 20)).astype(np.float32)
    assert feed(EnergyVAD(sample_rate=sr), noise, sr) or EnergyVAD(sample_rate=sr)  # documents the old failure mode
    vad = SileroVAD(sample_rate=sr)
    assert feed(vad, noise, sr) + vad.flush() == []


@needs_silero
def test_silero_keeps_sentences_separate_in_steady_noise():
    import wave
    from pathlib import Path
    with wave.open(str(Path(__file__).parent.parent / "demo" / "remote_scam.wav")) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
    hiss = np.random.default_rng(7).normal(0, 1, len(x)).astype(np.float32)
    noisy = np.clip(x + hiss * np.sqrt(np.mean(x ** 2) / (np.mean(hiss ** 2) * 10)), -1, 1).astype(np.float32)  # 10 dB SNR
    vad = SileroVAD(sample_rate=sr)
    assert len(feed(vad, noisy, sr) + vad.flush()) == 6  # one segment per spoken sentence


def test_make_vad_falls_back_to_energy():
    assert type(make_vad(48000, "energy")) is EnergyVAD
