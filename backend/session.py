"""Session state and the audio -> ASR -> rules -> Qwen pipeline orchestration."""
from __future__ import annotations

import asyncio
import re
import itertools
import secrets
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import WebSocket

from . import assistant, asr, llm, safety
from .audio import EnergyVAD, Segment, make_vad, resample_to_16k, to_wav_bytes
from .config import settings
from .models import (
    CoachSuggestion,
    EvidenceLine,
    SafetyAssessment,
    SafetyCandidate,
    TaskProposal,
    Utterance,
)
from .notes import NoteStore

ASR_QUEUE_MAX = 8
PROACTIVE_MIN_GAP_S = 3.0  # a newer question replaces a pending suggestion (key='auto')
_QUESTION_START = re.compile(
    r"^(can|could|would|will|is|are|am|do|does|did|when|what|where|why|how|which|who|should|shall|may|have|has)\b", re.I)


def is_question(text: str) -> bool:
    """A caller line worth answering: a real question of at least 4 words."""
    t = text.strip()
    if len(t.split()) < 4:
        return False
    if t.endswith("?"):
        return True
    first_clause = re.split(r"[,.;]", t)[-1].strip() or t
    return bool(_QUESTION_START.match(first_clause)) or bool(_QUESTION_START.match(t))


@dataclass
class Capture:
    id: str
    source: str  # HOST | REMOTE — bound server-side at registration, never changed by frames
    origin: str  # replay | live
    sample_rate: int
    vad: EnergyVAD
    last_seq: int | None = None
    frames: int = 0
    dropped: int = 0
    speaking: bool = False


@dataclass
class Session:
    id: str
    utterances: dict[str, Utterance] = field(default_factory=dict)
    captures: dict[str, Capture] = field(default_factory=dict)
    tasks: dict[str, TaskProposal] = field(default_factory=dict)
    assessments: dict[str, SafetyAssessment] = field(default_factory=dict)
    active: bool = True
    auto_suggest: bool = True
    last_proactive: float = 0.0
    summary: str | None = None
    summary_model: str | None = None  # set only when the summary model actually wrote the summary
    caller_requests: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    _utt_counter: itertools.count = field(default_factory=lambda: itertools.count(1))
    _task_counter: itertools.count = field(default_factory=lambda: itertools.count(1))

    def next_utt_id(self) -> str:
        return f"u-{next(self._utt_counter):04d}"

    def evidence(self, ids: list[str]) -> list[EvidenceLine]:
        out = []
        for i in ids:
            u = self.utterances.get(i)
            if u:
                out.append(EvidenceLine(id=u.id, source=u.source, text=u.text, start_ms=u.start_ms))
        return out

    def context_before(self, trigger_id: str, n: int = 3) -> list[Utterance]:
        ordered = list(self.utterances.values())
        idx = next(i for i, u in enumerate(ordered) if u.id == trigger_id)
        return ordered[max(0, idx - n): idx + 1]

    def recent(self, n: int = 6) -> list[Utterance]:
        return list(self.utterances.values())[-n:]


class Metrics:
    def __init__(self) -> None:
        self.samples: dict[str, list[float]] = {}

    def add(self, name: str, ms: float) -> None:
        self.samples.setdefault(name, []).append(round(ms, 1))

    def report(self) -> dict[str, Any]:
        out = {}
        for name, vals in self.samples.items():
            s = sorted(vals)
            p95 = s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]
            out[name] = {"n": len(s), "median_ms": statistics.median(s), "p95_ms": p95, "max_ms": s[-1]}
        return out


@dataclass
class _AsrJob:
    session: Session
    capture: Capture
    segment: Segment
    closed_at: float


class Hub:
    def __init__(self) -> None:
        self.client: httpx.AsyncClient | None = None
        self.llm = llm.LLMQueue()
        self.asr_q: asyncio.Queue[_AsrJob] = asyncio.Queue(maxsize=ASR_QUEUE_MAX)
        self.notes = NoteStore()
        self.session: Session | None = None
        self.subscribers: set[WebSocket] = set()
        self.metrics = Metrics()
        self.approved_tasks: list[TaskProposal] = []
        self._tasks: list[asyncio.Task] = []
        self._coach_counter = itertools.count(1)

    # ---------- lifecycle ----------
    async def start(self) -> None:
        self.client = httpx.AsyncClient()
        print("[llm] models:", await llm.resolve_models(self.client))
        self.llm.start()
        self._tasks.append(asyncio.create_task(self._asr_worker()))
        self._tasks.append(asyncio.create_task(llm.warmup(self.client)))
        self._tasks.append(asyncio.create_task(asr.warmup(self.client)))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await self.llm.stop()
        if self.client:
            await self.client.aclose()

    # ---------- events ----------
    async def broadcast(self, event: dict[str, Any], session: "Session | None" = None) -> None:
        if session is not None:
            event.setdefault("session_id", session.id)
        dead = []
        for ws in list(self.subscribers):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.subscribers.discard(ws)

    # ---------- sessions ----------
    def new_session(self) -> Session:
        if self.session and self.session.active:
            self.session.active = False
        self.session = Session(id="s-" + secrets.token_hex(6))
        return self.session

    def get_session(self, session_id: str) -> Session | None:
        if self.session and self.session.id == session_id:
            return self.session
        return None

    # ---------- audio ----------
    def register_capture(self, session: Session, source: str, origin: str, sample_rate: int) -> Capture:
        cap = Capture(
            id="c-" + secrets.token_hex(6), source=source, origin=origin,
            sample_rate=sample_rate, vad=make_vad(sample_rate, settings.vad),
        )
        session.captures[cap.id] = cap
        return cap

    async def on_frame(self, session: Session, cap: Capture, t_ms: float, pcm) -> None:
        cap.frames += 1
        segments = cap.vad.push(t_ms, pcm)
        if cap.vad.in_speech != cap.speaking:
            cap.speaking = cap.vad.in_speech
            if cap.speaking:
                await self.broadcast({"type": "asr.status", "source": cap.source, "state": "speaking"}, session)
        for seg in segments:
            await self._enqueue_segment(session, cap, seg)

    async def end_capture(self, session: Session, cap: Capture) -> None:
        for seg in cap.vad.flush():
            await self._enqueue_segment(session, cap, seg)
        session.captures.pop(cap.id, None)

    async def _enqueue_segment(self, session: Session, cap: Capture, seg: Segment) -> None:
        job = _AsrJob(session, cap, seg, time.perf_counter())
        try:
            self.asr_q.put_nowait(job)
            await self.broadcast({"type": "asr.status", "source": cap.source, "state": "processing"}, session)
        except asyncio.QueueFull:
            cap.dropped += 1
            await self.broadcast({"type": "asr.status", "source": cap.source, "state": "dropped",
                                  "detail": "ASR queue full; segment dropped"}, session)

    async def _asr_worker(self) -> None:
        while True:
            job = await self.asr_q.get()
            try:
                await self._transcribe(job)
            except Exception as exc:
                print(f"[asr] job failed: {exc!r}")
                await self.broadcast({"type": "asr.status", "source": job.capture.source,
                                      "state": "error", "detail": str(exc)[:200]}, job.session)

    async def _transcribe(self, job: _AsrJob) -> None:
        assert self.client
        seg = job.segment
        audio16 = await asyncio.to_thread(resample_to_16k, seg.samples, seg.sample_rate)
        wav = to_wav_bytes(audio16)
        text = await asr.transcribe(self.client, wav)
        del wav, audio16  # raw audio is never persisted
        now = time.perf_counter()
        self.metrics.add("speech_end_to_transcript", (now - job.closed_at) * 1000 + job.capture.vad.hangover_ms)
        await self.broadcast({"type": "asr.status", "source": job.capture.source, "state": "idle"}, job.session)
        if not text:
            return
        s = job.session
        utt = Utterance(
            id=s.next_utt_id(), session_id=s.id, source=job.capture.source, origin=job.capture.origin,
            text=text, start_ms=seg.start_ms, end_ms=seg.end_ms,
        )
        s.utterances[utt.id] = utt
        await self.broadcast(utt.model_dump())
        if utt.source == "REMOTE":
            await self._safety_stage1(s, utt, t0=now)
            self._maybe_proactive(s, utt)

    # ---------- safety ----------
    async def _safety_stage1(self, s: Session, utt: Utterance, t0: float) -> None:
        category = safety.scan(utt.text)
        if not category:
            return
        await self.broadcast(SafetyCandidate(trigger_id=utt.id, category=category).model_dump(), s)
        chip_at = time.perf_counter()
        self.metrics.add("transcript_to_chip", (chip_at - t0) * 1000)

        async def job() -> None:
            await self.run_safety(s, utt, category, chip_at)

        self.llm.submit(llm.PRIORITY_SAFETY, job)

    async def run_safety(self, s: Session, utt: Utterance, category: str, chip_at: float) -> SafetyAssessment:
        assert self.client
        context = s.context_before(utt.id)
        fields, fallback = await safety.assess(self.client, context, utt, category)
        latency = (time.perf_counter() - chip_at) * 1000
        self.metrics.add("chip_to_qwen_decision", latency)
        assessment = SafetyAssessment(
            trigger_id=utt.id, **fields,
            alert=safety.alert_level(fields["intent"], fields["severity"]),
            evidence=s.evidence(fields["transcript_ids"]),
            model=llm.model_for("safety"), fallback_reason=fallback, latency_ms=int(latency),
        )
        s.assessments[utt.id] = assessment
        await self.broadcast(assessment.model_dump(), s)
        return assessment

    # ---------- coaching ----------
    def ask(self, s: Session, question: str) -> str:
        return self._coach(s, question, question or "(latest caller question)", proactive=False)

    def _maybe_proactive(self, s: Session, utt: Utterance) -> None:
        """Suggest an answer on our own when the caller asks a normal question (never for flagged scam lines)."""
        if not s.auto_suggest or not s.active or not is_question(utt.text) or safety.scan(utt.text):
            return
        now = time.monotonic()
        if now - s.last_proactive < PROACTIVE_MIN_GAP_S:
            return
        s.last_proactive = now
        self._coach(
            s, f'The caller just asked: "{utt.text}". Write the exact words YOU could say back: first person, at most two '
               'short sentences, using facts from the note paragraphs when they help (cite them). No preamble.',
            utt.text, proactive=True, trigger_id=utt.id,
        )

    def _coach(self, s: Session, model_question: str, display_question: str, proactive: bool,
               trigger_id: str | None = None) -> str:
        req_id = f"q-{next(self._coach_counter):03d}"
        recent = s.recent(12)
        last_remote = next((u.text for u in reversed(recent) if u.source == "REMOTE"), "")
        paras = self.notes.search(f"{display_question} {last_remote}")
        alerts = [
            f"[{a.trigger_id}] {a.category}: {a.intent}, severity {a.severity} ({a.alert})"
            for a in s.assessments.values() if a.alert in ("danger", "caution", "uncertain")
        ][-5:]

        async def job() -> None:
            assert self.client
            t0 = time.perf_counter()
            fields, fallback = await assistant.coach(self.client, model_question, recent, paras, alerts)
            latency = (time.perf_counter() - t0) * 1000
            if proactive and fallback:
                return  # an automatic suggestion that failed validation is simply not shown
            self.metrics.add("auto_suggestion" if proactive else "ask_to_suggestion", latency)
            by_id = {p.id: p for p in paras}
            ev = CoachSuggestion(
                id=req_id, question=display_question,
                transcript_ids=fields["transcript_ids"], note_paragraph_ids=fields["note_paragraph_ids"],
                text=fields["text"], found_in_notes=fields["found_in_notes"], basis=fields["basis"],
                evidence=s.evidence(fields["transcript_ids"]),
                paragraphs=[by_id[i] for i in fields["note_paragraph_ids"]],
                model=llm.model_for("assistant"), fallback_reason=fallback, latency_ms=int(latency), proactive=proactive,
                trigger_id=trigger_id,
            )
            await self.broadcast(ev.model_dump(), s)

        # Separate keys: an automatic suggestion never cancels a question the host typed, and vice versa.
        if proactive:
            self.llm.submit(llm.PRIORITY_PROACTIVE, job, key="auto")
        else:
            self.llm.submit(llm.PRIORITY_ASK, job, key="coach")
        return req_id

    # ---------- end of call ----------
    def end_session(self, s: Session) -> None:
        s.active = False
        s.ended_at = time.time()

        async def job() -> None:
            assert self.client
            # Let in-flight ASR segments land so the summary sees the final utterances.
            for _ in range(40):
                if self.asr_q.empty():
                    break
                await asyncio.sleep(0.25)
            await asyncio.sleep(0.5)
            transcript = list(s.utterances.values())
            alerts = [f"[{a.trigger_id}] {a.category}: {a.intent}, severity {a.severity} ({a.alert})"
                      for a in s.assessments.values() if a.alert == "danger"]
            result, fallback = await assistant.summarize(self.client, transcript, alerts)
            s.summary = result["summary"]
            s.summary_model = None if fallback else llm.model_for("summary")
            s.caller_requests = [{"request": r["request"], "transcript_ids": r["transcript_ids"],
                                  "evidence": [e.model_dump() for e in s.evidence(r["transcript_ids"])]}
                                 for r in result["caller_requests"]]
            for t in result["tasks"]:
                tid = f"t-{next(s._task_counter):02d}"
                s.tasks[tid] = TaskProposal(
                    id=tid, description=t["description"], due_text=t["due_text"],
                    transcript_ids=t["transcript_ids"], evidence=s.evidence(t["transcript_ids"]),
                )
            if llm.model_for("summary") not in {llm.model_for(r) for r in llm.LIVE_ROLES}:
                asyncio.create_task(llm.warmup(self.client))  # reload the live model(s) for the next call
            await self.broadcast({
                "type": "call.summary", "session_id": s.id, "summary": s.summary,
                "caller_requests": s.caller_requests,
                "tasks": [t.model_dump() for t in s.tasks.values()],
                "model": llm.model_for("summary"), "fallback_reason": fallback,
            })

        self.llm.submit(llm.PRIORITY_SUMMARY, job)


hub = Hub()
