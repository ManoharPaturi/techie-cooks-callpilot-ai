"""Compare speech detectors (energy vs Silero) on demo calls: clean, with background chatter, and with loud hiss.
Each detected segment is transcribed by the real local Whisper; we report word error rate against the known script.
    uv run python scripts/bench_vad.py
"""
import asyncio
import re
import sys
import wave
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import asr  # noqa: E402
from backend.audio import EnergyVAD, SileroVAD, resample_to_16k, to_wav_bytes  # noqa: E402

DEMO = Path(__file__).resolve().parent.parent / "demo"
SCRIPTS = {
    "remote_scam.wav": "Hello, I am calling from the fraud prevention team of your bank. Your account has been blocked because of a suspicious transaction. We just sent a one time password to your phone. Please tell me the OTP right now, so I can unlock your account. If you do not share it in the next five minutes, your account will be closed permanently. You can also transfer ten thousand rupees to a safe account immediately.",
    "remote_legit.wav": "Hi, this is Priya from Lotus Bakery. I wanted to check on the website agreement. Can you confirm the website will be delivered by Friday? And is bulk product upload included in the package, or is that extra? By the way, our bank keeps reminding us to never tell anyone your OTP, so we are careful with the payment portal. We will send the product images before Friday.",
    "remote_digital_arrest.wav": "Hello, this is Inspector Rajesh Sharma from the CBI cyber crime branch. A parcel booked in your name was seized by customs in Mumbai. It contains illegal drugs. A money laundering case has been registered against you. You are now under digital arrest. Do not disconnect this video call, and do not tell anyone. To verify your bank account, I have sent a collect request. Now, please enter your UPI PIN to receive the verification amount.",
}
NUM = {"10,000": "ten thousand", "5": "five", "rs": "rupees", "otp": "o t p", "one-time": "one time", "upi": "u p i"}


def norm(t: str) -> list[str]:
    t = t.lower()
    for k, v in NUM.items():
        t = re.sub(rf"\b{re.escape(k)}\b", v, t)
    return re.findall(r"[a-z]+", t)


def wer(ref: str, hyp: str) -> float:
    r, h = norm(ref), norm(hyp)
    d = np.arange(len(h) + 1)
    for i in range(1, len(r) + 1):
        prev, d[0] = d.copy(), i
        for j in range(1, len(h) + 1):
            d[j] = min(prev[j] + 1, d[j - 1] + 1, prev[j - 1] + (r[i - 1] != h[j - 1]))
    return d[len(h)] / max(len(r), 1)


def load(name):
    with wave.open(str(DEMO / name)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768, w.getframerate()


def mix(x, noise, snr_db):
    noise = np.resize(noise, len(x))
    px, pn = np.mean(x ** 2), np.mean(noise ** 2) + 1e-12
    return np.clip(x + noise * np.sqrt(px / (pn * 10 ** (snr_db / 10))), -1, 1).astype(np.float32)


def segments(vad, x, sr):
    n, out = int(sr * 0.02), []
    for i in range(0, len(x), n):
        out += vad.push(i * 1000 / sr, x[i:i + n])
    return out + vad.flush()


async def main():
    rng = np.random.default_rng(7)
    rows = []
    async with httpx.AsyncClient() as client:
        for name, ref in SCRIPTS.items():
            x, sr = load(name)
            others = [load(o)[0] for o in SCRIPTS if o != name]
            chatter = np.concatenate([o[::-1] for o in others])  # reversed speech = unintelligible babble
            conditions = {"clean": x, "chatter 12 dB": mix(x, chatter, 12), "hiss 10 dB": mix(x, rng.normal(0, 1, len(x)), 10)}
            for cond, audio in conditions.items():
                for label, vad in (("energy", EnergyVAD(sample_rate=sr)), ("silero", SileroVAD(sample_rate=sr))):
                    segs = segments(vad, audio, sr)
                    texts = [await asr.transcribe(client, to_wav_bytes(resample_to_16k(s.samples, s.sample_rate))) for s in segs]
                    junk = sum(1 for t in texts if len(norm(t)) < 2)
                    rows.append((name.replace("remote_", "").replace(".wav", ""), cond, label, len(segs), junk, wer(ref, " ".join(texts))))
    print(f"{'call':16} {'condition':14} {'detector':8} {'segments':>8} {'junk':>5} {'WER':>6}")
    for r in rows:
        print(f"{r[0]:16} {r[1]:14} {r[2]:8} {r[3]:8d} {r[4]:5d} {r[5]*100:5.1f}%")
    for label in ("energy", "silero"):
        sel = [r for r in rows if r[2] == label]
        print(f"MEAN {label:7}  WER {np.mean([r[5] for r in sel])*100:5.1f}%   junk segments {sum(r[4] for r in sel)}   "
              f"noisy-only WER {np.mean([r[5] for r in sel if r[1] != 'clean'])*100:5.1f}%")


asyncio.run(main())
