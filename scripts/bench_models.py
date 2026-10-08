"""Compare small local models on CallPilot's real tasks, one model at a time (others unloaded from memory).

    uv run python scripts/bench_models.py                 # all installed candidates -> docs/report/model-comparison.md
    uv run python scripts/bench_models.py qwen3:0.6b      # just these
Per model: 20 labeled safety cases (accuracy, false alarms, missed scams, invalid outputs, latency) and 6 private-assistant
questions scored by fixed rules (no human judgement). Sample answers are saved so the quality can be read, not just counted.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = ["qwen3:1.7b", "qwen3:0.6b", "gemma3:1b", "llama3.2:1b", "qwen2.5:1.5b"]
OUT = ROOT / "docs" / "report"


# ---------------------------------------------------------------- child: benchmark ONE model (env OLLAMA_MODEL)
async def run_one() -> dict:
    sys.path.insert(0, str(ROOT))
    import httpx

    from backend import assistant, llm, safety
    from backend.config import settings
    from backend.models import Utterance
    from backend.notes import NoteStore

    def utt(i, text, src="REMOTE"):
        return Utterance(id=f"u-{i:04d}", session_id="m", source=src, origin="replay", text=text, start_ms=i * 1000, end_ms=i * 1000 + 900)

    cases = json.loads((ROOT / "demo" / "labeled_cases.json").read_text())
    res = {"model": settings.ollama_model, "safety": [], "coach": []}
    async with httpx.AsyncClient() as client:
        await llm.warmup(client)
        # ---- safety
        for c in cases:
            ctx = [utt(i + 1, t) for i, t in enumerate(c["context"])]
            trig = utt(len(ctx) + 1, c["text"])
            cat = safety.scan(trig.text)
            if cat is None:
                res["safety"].append({"id": c["id"], "expect": c["expect_alert"], "shown": "none", "ok": c["expect_alert"] == "none",
                                      "ms": None, "invalid": False, "intent": "-"})
                continue
            t0 = time.perf_counter()
            fields, reason = await safety.assess(client, ctx + [trig], trig, cat)
            ms = (time.perf_counter() - t0) * 1000
            alert = safety.alert_level(fields["intent"], fields["severity"])
            shown = "none" if alert in ("cleared", "info") else alert
            invalid = bool(reason) and "request verb" not in reason  # model output rejected (bad JSON / bad IDs / timeout)
            res["safety"].append({"id": c["id"], "expect": c["expect_alert"], "shown": shown, "ok": shown == c["expect_alert"],
                                  "ms": round(ms), "invalid": invalid, "intent": f'{fields["intent"]}/{fields["severity"]}',
                                  "reason": reason or ""})
        # ---- private assistant
        scam = [utt(1, "Hello, I am calling from the fraud prevention team of your bank."),
                utt(2, "Your account has been blocked because of a suspicious transaction."),
                utt(3, "Okay, what do you need from me?", "HOST"),
                utt(4, "We just sent a one-time password to your phone."),
                utt(5, "Please tell me the OTP right now, so I can unlock your account.")]
        alerts = ["[u-0005] otp_or_code: request, severity high (danger)"]
        notes = NoteStore()
        notes.import_text("client_agreement.md", (ROOT / "demo" / "client_agreement.md").read_text())
        qs = [
            ("so do i have to share?", scam, alerts, lambda o: o["basis"] in ("conversation", "general") and "not found" not in o["text"].lower()),
            ("what is this call about", scam, alerts, lambda o: any(w in o["text"].lower() for w in ("bank", "fraud", "account"))),
            ("is this a scam?", scam, alerts, lambda o: "not found" not in o["text"].lower() and len(o["text"]) > 20),
            ("heyy", scam, alerts, lambda o: "not found" not in o["text"].lower() and len(o["text"]) > 20),
            ("Is bulk product upload included in the package?", [], [], lambda o: o["basis"] == "notes" and bool(o["note_paragraph_ids"])
             and ("not" in o["text"].lower() or "extra" in o["text"].lower())),
            ("What is the client's refund policy?", [], [], lambda o: not o["note_paragraph_ids"]),
        ]
        for q, recent, al, check in qs:
            t0 = time.perf_counter()
            out, err = await assistant.coach(client, q, recent, notes.search(f"{q} {recent[-1].text if recent else ''}"), al)
            ms = (time.perf_counter() - t0) * 1000
            ok = err is None and check(out)
            res["coach"].append({"q": q, "ok": ok, "ms": round(ms), "basis": out["basis"], "text": out["text"], "guard": err or ""})
    return res


# ---------------------------------------------------------------- parent: run each model in isolation
def ollama_unload_all() -> None:
    import httpx
    try:
        loaded = httpx.get("http://127.0.0.1:11434/api/ps", timeout=5).json().get("models", [])
        for m in loaded:
            httpx.post("http://127.0.0.1:11434/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=30)
    except httpx.HTTPError:
        pass
    time.sleep(2)


def installed() -> set[str]:
    import httpx
    tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=5).json()
    return {m["name"] for m in tags.get("models", [])}


def summarize(r: dict) -> dict:
    s, c = r["safety"], r["coach"]
    lat = [x["ms"] for x in s if x["ms"] is not None]
    flagged = lambda v: v in ("danger", "caution")
    return {
        "model": r["model"],
        "safety_correct": sum(x["ok"] for x in s), "safety_total": len(s),
        "missed_scams": sum(1 for x in s if flagged(x["expect"]) and not flagged(x["shown"])),
        "false_alarms": sum(1 for x in s if flagged(x["shown"]) and not flagged(x["expect"])),
        "invalid_outputs": sum(x["invalid"] for x in s),
        "safety_median_s": statistics.median(lat) / 1000 if lat else None,
        "coach_ok": sum(x["ok"] for x in c), "coach_total": len(c),
        "coach_guarded": sum(1 for x in c if x["guard"]),
        "coach_median_s": statistics.median(x["ms"] for x in c) / 1000,
    }


def main() -> None:
    if "--one" in sys.argv:
        print("RESULT" + json.dumps(asyncio.run(run_one())))
        return
    want = [a for a in sys.argv[1:] if not a.startswith("-")] or CANDIDATES
    have = installed()
    models = [m for m in want if m in have]
    print("benchmarking:", models, "| not installed:", [m for m in want if m not in have])
    results = []
    for m in models:
        ollama_unload_all()
        t0 = time.time()
        p = subprocess.run([sys.executable, __file__, "--one"], env={**os.environ, "OLLAMA_MODEL": m, "ASSISTANT_MODEL": m},
                           capture_output=True, text=True, timeout=1800)
        line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT")), None)
        if not line:
            print(f"{m}: FAILED\n{p.stdout[-800:]}\n{p.stderr[-800:]}")
            continue
        r = json.loads(line[6:])
        r["summary"] = summarize(r)
        results.append(r)
        s = r["summary"]
        print(f"{m:14} safety {s['safety_correct']}/{s['safety_total']} (missed {s['missed_scams']}, false alarms {s['false_alarms']}, "
              f"invalid {s['invalid_outputs']}) {s['safety_median_s']:.1f}s | assistant {s['coach_ok']}/{s['coach_total']} "
              f"(guard fired {s['coach_guarded']}) {s['coach_median_s']:.1f}s | {time.time()-t0:.0f}s total")
    ollama_unload_all()
    OUT.mkdir(parents=True, exist_ok=True)
    old = json.loads((OUT / "model-comparison.json").read_text()) if (OUT / "model-comparison.json").exists() else []
    merged = {r["model"]: r for r in old} | {r["model"]: r for r in results}
    (OUT / "model-comparison.json").write_text(json.dumps(list(merged.values()), indent=2, ensure_ascii=False))
    write_md(list(merged.values()))


def write_md(results: list[dict]) -> None:
    rows = sorted(results, key=lambda r: (-r["summary"]["safety_correct"], r["summary"]["missed_scams"], -r["summary"]["coach_ok"]))
    md = ["# Small local model comparison", "",
          "Same tasks, same prompts, same 8 GB laptop, one model loaded at a time. Real Ollama calls, temperature 0.", "",
          "| Model | Safety cases | Missed scams | False alarms | Invalid outputs | Safety check (median) | Assistant answers | Unsafe answer replaced | Answer time (median) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        s = r["summary"]
        md.append(f"| `{s['model']}` | {s['safety_correct']}/{s['safety_total']} | {s['missed_scams']} | {s['false_alarms']} | {s['invalid_outputs']} | "
                  f"{s['safety_median_s']:.1f} s | {s['coach_ok']}/{s['coach_total']} | {s['coach_guarded']} | {s['coach_median_s']:.1f} s |")
    md += ["", "## Sample assistant answers", ""]
    for q in [c["q"] for c in rows[0]["coach"]]:
        md.append(f"**Q: {q}**")
        for r in rows:
            a = next(c for c in r["coach"] if c["q"] == q)
            md.append(f"- `{r['model']}` {'✓' if a['ok'] else '✗'} — {a['text']}" + (f" *(guard: {a['guard']})*" if a["guard"] else ""))
        md.append("")
    (OUT / "model-comparison.md").write_text("\n".join(md))
    print("wrote", OUT / "model-comparison.md")


if __name__ == "__main__":
    main()
