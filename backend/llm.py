"""Local Ollama client plus a single priority queue so heavy inference is serialized.

Priority: safety (0) > explicit host Ask (1) > proactive suggestion (2) > after-call summary (3).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from .config import settings

PRIORITY_SAFETY = 0
PRIORITY_ASK = 1
PRIORITY_PROACTIVE = 2
PRIORITY_SUMMARY = 3


class LLMError(RuntimeError):
    pass


THINKING_MODELS = ("qwen3", "deepseek-r1", "magistral", "gpt-oss", "gemma4")  # accept the "think" switch; others may reject it


# Which local model does which job. The assistant model is resolved at startup (fallback if not installed).
ROLE_MODEL: dict[str, str] = {"safety": settings.ollama_model, "assistant": settings.assistant_model,
                              "summary": settings.summary_model}
LIVE_ROLES = ("safety", "assistant")  # must stay loaded during a call


def model_for(role: str) -> str:
    return ROLE_MODEL.get(role, settings.ollama_model)


async def resolve_models(client: httpx.AsyncClient) -> dict[str, str]:
    installed = (await health(client))["installed"]
    for role in ("assistant", "summary"):
        if ROLE_MODEL[role] not in installed:
            print(f"[llm] {role} model {ROLE_MODEL[role]!r} not installed; using {settings.ollama_model!r}")
            ROLE_MODEL[role] = settings.ollama_model
    return dict(ROLE_MODEL)


def _think_opts(model: str) -> dict[str, Any]:
    return {"think": False} if model.startswith(THINKING_MODELS) else {}


async def chat_json(
    client: httpx.AsyncClient,
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    num_predict: int = 320,
    role: str = "safety",
) -> dict[str, Any]:
    """One structured, non-streaming, temperature-0 call. Returns the parsed object (unvalidated)."""
    model = model_for(role)
    off_call = role not in LIVE_ROLES and model not in {model_for(r) for r in LIVE_ROLES}
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": schema,
        "stream": False,
        **_think_opts(model),
        "keep_alive": 0 if off_call else "30m",  # free memory for the live models straight after an off-call job
        "options": {"temperature": 0, "num_ctx": 2048, "num_predict": num_predict},
    }
    try:
        # Live jobs must answer fast; an off-call model may first have to be loaded into memory (~1 min on 8 GB).
        timeout = settings.ollama_timeout_s if role in LIVE_ROLES else max(settings.ollama_timeout_s, 180.0)
        resp = await client.post(settings.ollama_url, json=body, timeout=timeout)
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        return json.loads(content)
    except httpx.TimeoutException as exc:
        raise LLMError("local model timed out") from exc
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        raise LLMError(f"local model call failed: {exc}") from exc


async def health(client: httpx.AsyncClient) -> dict[str, Any]:
    base = settings.ollama_url.split("/api/")[0]
    try:
        tags = (await client.get(base + "/api/tags", timeout=2.0)).json()
        names = [m.get("name") for m in tags.get("models", [])]
        return {"ok": settings.ollama_model in names, "installed": names,
                "assistant_ok": ROLE_MODEL["assistant"] in names, "summary_ok": ROLE_MODEL["summary"] in names}
    except (httpx.HTTPError, ValueError):
        return {"ok": False, "installed": []}


async def warmup(client: httpx.AsyncClient) -> None:
    """Load each model with the SAME options as real calls; a different num_ctx would force Ollama to reload."""
    for model in dict.fromkeys(model_for(r) for r in LIVE_ROLES):  # only live models; summary model loads on demand
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "ok"}],
            "stream": False,
            **_think_opts(model),
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 2048, "num_predict": 1},
        }
        try:
            await client.post(settings.ollama_url, json=body, timeout=180.0)
        except httpx.HTTPError:
            pass


@dataclass(order=True)
class _Job:
    priority: int
    seq: int
    fn: Callable[[], Awaitable[None]] = field(compare=False)
    key: str | None = field(compare=False, default=None)
    enqueued: float = field(compare=False, default_factory=time.monotonic)


class LLMQueue:
    """Serializes local-model work. A job with a `key` replaces an older pending job with the same key
    (used to drop stale coaching requests)."""

    def __init__(self, workers: int | None = None) -> None:
        self._q: asyncio.PriorityQueue[_Job] = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._latest: dict[str, int] = {}
        # Must not exceed Ollama's OLLAMA_NUM_PARALLEL, or extra calls just wait inside Ollama.
        self.workers = workers or settings.llm_workers
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._run()) for _ in range(self.workers)]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()

    def submit(self, priority: int, fn: Callable[[], Awaitable[None]], key: str | None = None) -> None:
        seq = next(self._seq)
        if key:
            self._latest[key] = seq
        self._q.put_nowait(_Job(priority, seq, fn, key))

    @property
    def pending(self) -> int:
        return self._q.qsize()

    async def _run(self) -> None:
        while True:
            job = await self._q.get()
            if job.key and self._latest.get(job.key) != job.seq:
                continue  # superseded by a newer request
            try:
                await job.fn()
            except Exception as exc:  # a failed job must never kill the worker
                print(f"[llm-queue] job failed: {exc!r}")
