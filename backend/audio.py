"""PCM frame validation, per-source energy VAD and anti-aliased resampling.

Wire format of one binary WebSocket frame (little-endian):
    uint32  seq        monotonically increasing per capture
    float64 t_ms       session-relative capture time of the first sample (AudioContext clock)
    int16[] samples    mono PCM16 at the capture's registered sample rate
"""
from __future__ import annotations

import io
import struct
import wave
from dataclasses import dataclass, field
from math import gcd
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

HEADER = struct.Struct("<Id")
TARGET_SR = 16_000
ALLOWED_SAMPLE_RATES = {16_000, 22_050, 24_000, 32_000, 44_100, 48_000, 88_200, 96_000}
MAX_FRAME_MS = 100


class FrameError(ValueError):
    pass


def parse_frame(data: bytes, sample_rate: int, last_seq: int | None) -> tuple[int, float, np.ndarray]:
    if len(data) < HEADER.size + 2:
        raise FrameError("frame too short")
    payload_len = len(data) - HEADER.size
    if payload_len % 2:
        raise FrameError("odd PCM16 payload length")
    max_bytes = int(sample_rate * MAX_FRAME_MS / 1000) * 2
    if payload_len > max_bytes:
        raise FrameError(f"frame exceeds {MAX_FRAME_MS} ms")
    seq, t_ms = HEADER.unpack_from(data)
    if last_seq is not None and seq <= last_seq:
        raise FrameError(f"non-monotonic sequence {seq} after {last_seq}")
    if not np.isfinite(t_ms) or t_ms < 0:
        raise FrameError("invalid timestamp")
    pcm = np.frombuffer(data, dtype="<i2", offset=HEADER.size).astype(np.float32) / 32768.0
    return seq, t_ms, pcm


def resample_to_16k(samples: np.ndarray, sr: int) -> np.ndarray:
    """Polyphase resampling with an anti-aliasing FIR (never naive decimation)."""
    if sr == TARGET_SR:
        return samples.astype(np.float32)
    g = gcd(sr, TARGET_SR)
    return resample_poly(samples, TARGET_SR // g, sr // g).astype(np.float32)


def to_wav_bytes(samples_16k: np.ndarray) -> bytes:
    pcm = np.clip(samples_16k, -1.0, 1.0)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_SR)
        w.writeframes((pcm * 32767).astype("<i2").tobytes())
    return buf.getvalue()


@dataclass
class Segment:
    samples: np.ndarray  # at the capture sample rate
    sample_rate: int
    start_ms: int
    end_ms: int


@dataclass
class EnergyVAD:
    """Adaptive-threshold energy VAD with pre-roll and end-of-speech hangover.

    One instance per capture so HOST and REMOTE never share state.
    """

    sample_rate: int
    preroll_ms: int = 300
    hangover_ms: int = 550
    min_speech_ms: int = 250
    max_segment_ms: int = 12_000
    abs_threshold: float = 0.010
    noise_ratio: float = 3.0

    _noise: float = 0.003
    _preroll: list[tuple[float, np.ndarray]] = field(default_factory=list)
    _active: list[np.ndarray] = field(default_factory=list)
    _start_ms: float | None = None
    _last_voice_ms: float = 0.0
    _voiced_ms: float = 0.0
    _cursor_ms: float = 0.0
    level: float = 0.0  # last frame RMS, for status reporting

    @property
    def in_speech(self) -> bool:
        return self._start_ms is not None

    def push(self, t_ms: float, pcm: np.ndarray) -> list[Segment]:
        out: list[Segment] = []
        dur_ms = len(pcm) * 1000.0 / self.sample_rate
        rms = float(np.sqrt(np.mean(pcm * pcm))) if len(pcm) else 0.0
        self.level = rms
        voiced = self._is_voiced(pcm, rms)
        if not voiced:
            # Track the noise floor only while not speaking.
            self._noise = 0.95 * self._noise + 0.05 * rms if not self.in_speech else self._noise
        self._cursor_ms = t_ms + dur_ms

        if self._start_ms is None:
            self._preroll.append((t_ms, pcm))
            while self._preroll and (t_ms + dur_ms - self._preroll[0][0]) > self.preroll_ms:
                self._preroll.pop(0)
            if voiced:
                self._start_ms = self._preroll[0][0]
                self._active = [p for _, p in self._preroll]
                self._preroll = []
                self._last_voice_ms = t_ms + dur_ms
                self._voiced_ms = dur_ms
            return out

        self._active.append(pcm)
        if voiced:
            self._last_voice_ms = t_ms + dur_ms
            self._voiced_ms += dur_ms
        silence = (t_ms + dur_ms) - self._last_voice_ms
        length = (t_ms + dur_ms) - self._start_ms
        if silence >= self.hangover_ms or length >= self.max_segment_ms:
            seg = self._close(end_ms=t_ms + dur_ms)
            if seg:
                out.append(seg)
        return out

    def _is_voiced(self, pcm: np.ndarray, rms: float) -> bool:
        return rms > max(self.abs_threshold, self._noise * self.noise_ratio)

    def flush(self) -> list[Segment]:
        if self._start_ms is None:
            return []
        seg = self._close(end_ms=self._cursor_ms)
        return [seg] if seg else []

    def _close(self, end_ms: float) -> Segment | None:
        start = self._start_ms or 0.0
        samples = np.concatenate(self._active) if self._active else np.zeros(0, np.float32)
        voiced = self._voiced_ms
        self._start_ms = None
        self._active = []
        self._voiced_ms = 0.0
        if voiced < self.min_speech_ms:
            return None
        return Segment(samples=samples, sample_rate=self.sample_rate, start_ms=int(start), end_ms=int(end_ms))


# ---------------------------------------------------------------------------------------------------------------
# Silero VAD: a 2 MB neural speech detector (MIT licence) that recognises speech rather than loudness, so background
# noise in a hall does not create or merge segments. Same interface as EnergyVAD; segmentation logic is shared.
# ---------------------------------------------------------------------------------------------------------------
SILERO_PATH = Path(__file__).resolve().parent.parent / "models" / "silero_vad.onnx"
_SILERO_SESSION = None
_WIN = 512  # samples at 16 kHz (32 ms), as required by Silero v5
_CTX = 64   # context samples prepended to every window (Silero v5 streaming contract)


def silero_available() -> bool:
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return SILERO_PATH.exists()


def _silero():
    global _SILERO_SESSION
    if _SILERO_SESSION is None:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        _SILERO_SESSION = ort.InferenceSession(str(SILERO_PATH), sess_options=opts, providers=["CPUExecutionProvider"])
    return _SILERO_SESSION


@dataclass
class SileroVAD(EnergyVAD):
    hangover_ms: int = 400    # Silero detects real pauses reliably, so it can close a sentence sooner than EnergyVAD
    start_prob: float = 0.5   # enter speech
    end_prob: float = 0.35    # stay in speech (hysteresis)
    min_rms: float = 0.002    # ignore digital silence entirely
    prob: float = 0.0
    _buf16: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    _context: np.ndarray = field(default_factory=lambda: np.zeros(_CTX, np.float32))
    _state: np.ndarray = field(default_factory=lambda: np.zeros((2, 1, 128), np.float32))

    def _is_voiced(self, pcm: np.ndarray, rms: float) -> bool:
        self._buf16 = np.concatenate([self._buf16, resample_to_16k(pcm, self.sample_rate)])
        sess = _silero()
        while len(self._buf16) >= _WIN:
            win, self._buf16 = self._buf16[:_WIN], self._buf16[_WIN:]
            x = np.concatenate([self._context, win])[None, :].astype(np.float32)
            out, self._state = sess.run(None, {"input": x, "state": self._state, "sr": np.array(16000, dtype=np.int64)})
            self._context = win[-_CTX:]
            self.prob = float(out[0][0])
        if rms < self.min_rms:
            return False
        return self.prob >= (self.end_prob if self.in_speech else self.start_prob)


def make_vad(sample_rate: int, kind: str = "auto") -> EnergyVAD:
    if kind == "silero" or (kind == "auto" and silero_available()):
        return SileroVAD(sample_rate=sample_rate)
    return EnergyVAD(sample_rate=sample_rate)
