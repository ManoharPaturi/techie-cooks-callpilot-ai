"""Benchmark a realistic burst: the fake-bank call's suspicious lines arrive ~5 s apart and are checked by local Qwen.
Measures rule-chip -> Qwen-decision latency per line (what the user waits for) and accuracy. Real model, no mocks.

    uv run python scripts/bench_burst.py            # uses LLM_WORKERS / SAFETY_NUM_PREDICT from env
"""
import asyncio
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import llm, safety  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.models import Utterance  # noqa: E402

CALL = [  # (seconds into call, text, expected alert)
    (2, "Hello, I am calling from the fraud prevention team of your bank.", None),
    (7, "Your account has been blocked because of a suspicious transaction.", "any"),
    (12, "We just sent a one-time password to your phone.", "any"),
    (16, "Please tell me the OTP right now, so I can unlock your account.", "danger"),
    (21, "If you do not share it in the next five minutes, your account will be closed permanently.", "danger"),
    (28, "You can also transfer 10,000 rupees to a safe account immediately.", "danger"),
    (34, "Now, please enter your UPI PIN to receive the verification amount.", "danger"),
]
SPEEDUP = float(__import__("os").environ.get("BURST_SPEEDUP", "4"))  # >1 compresses the call timeline (denser than real life)


async def main() -> None:
    queue = llm.LLMQueue()
    queue.start()
    results, utts = [], []
    async with httpx.AsyncClient() as client:
        await llm.warmup(client)
        done = asyncio.Event()
        pending = 0
        all_sent = False
        t_start = time.perf_counter()
        for i, (t, text, expect) in enumerate(CALL, start=1):
            await asyncio.sleep(max(0.0, t / SPEEDUP - (time.perf_counter() - t_start)))
            u = Utterance(id=f"u-{i:04d}", session_id="b", source="REMOTE", origin="replay", text=text, start_ms=t * 1000, end_ms=t * 1000 + 900)
            utts.append(u)
            cat = safety.scan(text)
            if not cat:
                continue
            chip = time.perf_counter()
            pending += 1
            ctx = utts[-4:]

            async def job(u=u, cat=cat, chip=chip, ctx=ctx, expect=expect):
                nonlocal pending
                fields, reason = await safety.assess(client, ctx, u, cat)
                alert = safety.alert_level(fields["intent"], fields["severity"])
                results.append(((time.perf_counter() - chip) * 1000, alert, expect, reason))
                pending -= 1
                if pending == 0 and all_sent:
                    done.set()
            queue.submit(llm.PRIORITY_SAFETY, job)
        all_sent = True
        if pending == 0:
            done.set()
        await done.wait()
    await queue.stop()
    lat = sorted(r[0] for r in results)
    ok = sum(1 for _, a, e, _ in results if e is None or e == "any" or a == e)
    print(f"workers={queue.workers} num_predict={settings.safety_num_predict}  checks={len(results)}  "
          f"median={statistics.median(lat)/1000:.1f}s  max={lat[-1]/1000:.1f}s  correct={ok}/{len(results)}")
    for ms, a, e, r in results:
        print(f"   {ms/1000:5.1f}s  {a:9} expected={e}  {r or ''}")


asyncio.run(main())
