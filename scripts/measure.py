"""Day 3 measurement: real local-model safety results + live pipeline metrics + memory. Writes demo/results.md.

Run AFTER replaying the demo WAVs in the browser (so /api/metrics holds browser-path ASR numbers):
    uv run python scripts/measure.py
Nothing here is estimated: every number is measured on this Mac in this run.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import platform
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import safety  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.models import Utterance  # noqa: E402

CASES = json.loads((ROOT / "demo" / "labeled_cases.json").read_text())


def sh(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def mem_free_pct() -> str:
    m = re.search(r"free percentage:\s*(\d+)%", sh("memory_pressure -Q"))
    return f"{m.group(1)}%" if m else "n/a"


def rss_mb(pattern: str) -> str:
    out = sh(f"ps -axo rss=,command= | grep -E '{pattern}' | grep -v grep")
    total = sum(int(l.split()[0]) for l in out.splitlines() if l.strip())
    return f"{total / 1024:.0f} MB" if total else "n/a"


def pct(vals: list[float], q: float) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def utt(i: int, text: str) -> Utterance:
    return Utterance(id=f"u-{i:04d}", session_id="m", source="REMOTE", origin="replay", text=text, start_ms=i * 1000, end_ms=i * 1000 + 900)


async def run_safety() -> tuple[list[dict], list[float]]:
    rows, lat = [], []
    async with httpx.AsyncClient() as client:
        for case in CASES:
            context = [utt(i + 1, t) for i, t in enumerate(case["context"])]
            trig = utt(len(context) + 1, case["text"])
            t0 = time.perf_counter()
            category = safety.scan(trig.text)
            if category is None:
                shown, intent, reason = "none", "(no rule hit, no Qwen call)", None
            else:
                fields, reason = await safety.assess(client, context + [trig], trig, category)
                lat.append((time.perf_counter() - t0) * 1000)
                alert = safety.alert_level(fields["intent"], fields["severity"])
                shown = "none" if alert in ("cleared", "info") else alert
                intent = f'{fields["intent"]}/{fields["severity"]}'
            rows.append({"id": case["id"], "text": case["text"], "expect": case["expect_alert"], "shown": shown,
                         "intent": intent, "ok": shown == case["expect_alert"], "note": reason or "",
                         "known_miss": bool(case.get("known_miss")), "lat_ms": round(lat[-1]) if category else None})
    return rows, lat


def main() -> None:
    mem_before = mem_free_pct()
    rows, lat = asyncio.run(run_safety())
    mem_after = mem_free_pct()
    try:
        live = httpx.get(f"http://127.0.0.1:{settings.host_port}/api/metrics", headers={"Host": f"127.0.0.1:{settings.host_port}"}, timeout=3).json()
    except httpx.HTTPError:
        live = {}

    flagged = lambda r: r in ("danger", "caution")
    fp = sum(1 for r in rows if flagged(r["shown"]) and not flagged(r["expect"]))
    fn = sum(1 for r in rows if flagged(r["expect"]) and not flagged(r["shown"]))
    unc = sum(1 for r in rows if r["shown"] == "uncertain")
    correct = sum(r["ok"] for r in rows)

    hw = f'{platform.machine()}, {int(sh("sysctl -n hw.memsize")) // 2**30} GB RAM, macOS {platform.mac_ver()[0]}'
    lines = [
        "# CallPilot AI — measured results",
        "",
        f"Measured {dt.datetime.now():%Y-%m-%d %H:%M} on {hw}. ASR `{settings.whisper_model_name}` (whisper.cpp), "
        f"LLM `{settings.ollama_model}` (Ollama), all on 127.0.0.1. Small sample: demo evidence, not general accuracy.",
        "",
        "## Live pipeline (browser replay path, from `/api/metrics`)",
        "",
        "| Stage | n | median | p95 | max |",
        "|---|---|---|---|---|",
    ]
    names = {"speech_end_to_transcript": "speech end → final transcript (incl. 550 ms VAD hangover)",
             "transcript_to_chip": "transcript → provisional chip", "chip_to_qwen_decision": "chip → Qwen decision",
             "ask_to_suggestion": "private Ask → suggestion", "auto_suggestion": "caller question → automatic reply"}
    for k, label in names.items():
        m = live.get(k)
        if m:
            lines.append(f'| {label} | {m["n"]} | {m["median_ms"]:.0f} ms | {m["p95_ms"]:.0f} ms | {m["max_ms"]:.0f} ms |')
        else:
            lines.append(f"| {label} | 0 | — | — | — |")
    lines += [
        "",
        f"## Safety labeled set ({len(rows)} cases, rules → local Qwen, temperature 0)",
        "",
        f"**{correct}/{len(rows)} match the expected alert.** False positives: {fp}. False negatives: {fn}. "
        f"Shown as *uncertain*: {unc}.",
        f"Qwen call latency (rule-hit cases, n={len(lat)}): median {statistics.median(lat):.0f} ms, p95 {pct(lat, .95):.0f} ms." if lat else "",
        "",
        "| id | caller text | expected | shown | model intent/severity | ok |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        mark = "✓" if r["ok"] else ("✗ known miss" if r["known_miss"] else "✗")
        lines.append(f'| {r["id"]} | {r["text"]} | {r["expect"]} | {r["shown"]} | {r["intent"]} | {mark} |')
    lines += [
        "",
        "## Memory",
        "",
        f"System free memory: {mem_before} before the safety run, {mem_after} after. "
        f"Resident: Ollama runner {rss_mb('ollama')}, whisper-server {rss_mb('whisper-server')}, host app {rss_mb('backend.main')}.",
        f"Swap: {sh('sysctl -n vm.swapusage')}.",
        "",
        "Latency is sensitive to memory pressure on 8 GB Macs: when other apps push the system into swap, Qwen calls slow",
        "down and safety checks queue behind each other (chip → decision includes that queue wait). Accuracy is unaffected.",
        "For the demo, quit other heavy apps first.",
        "",
    ]
    (ROOT / "demo" / "results.json").write_text(json.dumps({
        "measured_at": f"{dt.datetime.now():%Y-%m-%d %H:%M}", "hardware": hw,
        "asr": settings.whisper_model_name, "llm": settings.ollama_model, "live": live, "safety": rows,
        "safety_latency_ms": {"n": len(lat), "median": statistics.median(lat) if lat else None, "p95": pct(lat, .95) if lat else None},
        "fp": fp, "fn": fn, "uncertain": unc, "correct": correct,
        "memory": {"free_before": mem_before, "free_after": mem_after, "swap": sh("sysctl -n vm.swapusage"),
                   "ollama_rss": rss_mb("ollama"), "whisper_rss": rss_mb("whisper-server")},
    }, indent=2, ensure_ascii=False))
    out = ROOT / "demo" / "results.md"
    out.write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
