"""Private host app. Served ONLY on 127.0.0.1 — never mount this on the LAN listener."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import asr, llm
from .audio import ALLOWED_SAMPLE_RATES, FrameError, parse_frame
from .config import DEMO_DIR, FRONTEND_DIST, settings
from .models import ApproveTaskRequest, AskRequest, StartSessionRequest
from .rooms import SignalError, rooms
from .session import hub

DEMO_AUDIO = {"remote_scam": "remote_scam.wav", "remote_legit": "remote_legit.wav", "remote_injection": "remote_injection.wav",
              "remote_digital_arrest": "remote_digital_arrest.wav"}
DEMO_NOTES = {"client_agreement": "client_agreement.md"}
MAX_NOTE_CHARS = 100_000
MAX_DROPPED_BEFORE_CLOSE = 50


@asynccontextmanager
async def lifespan(_: FastAPI):
    await hub.start()
    rooms.on_status = hub.broadcast
    yield
    await hub.stop()


app = FastAPI(title="CallPilot host (private)", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


# ---------- origin / host guard ----------
def _origin_ok(origin: str | None) -> bool:
    return origin in settings.allowed_origins


def _host_ok(host: str | None) -> bool:
    # Blocks DNS-rebinding: a malicious site's hostname will not match.
    return host in settings.allowed_hosts


@app.middleware("http")
async def guard(request: Request, call_next):
    if not _host_ok(request.headers.get("host")):
        return JSONResponse({"error": "host not allowed"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _origin_ok(request.headers.get("origin")):
        return JSONResponse({"error": "origin not allowed"}, status_code=403)
    return await call_next(request)


async def _ws_guard(ws: WebSocket) -> bool:
    if not _host_ok(ws.headers.get("host")) or not _origin_ok(ws.headers.get("origin")):
        await ws.close(code=4403)
        return False
    return True


# ---------- pages ----------
@app.get("/", response_class=HTMLResponse)
async def index():
    page = FRONTEND_DIST / "host" / "index.html"
    if not page.exists():
        return HTMLResponse("<h1>Frontend not built</h1><p>Run <code>cd frontend && npm run build</code>.</p>", 503)
    return FileResponse(page, headers={"Cache-Control": "no-store"})


if (FRONTEND_DIST / "host-assets").exists():
    app.mount("/host-assets", StaticFiles(directory=FRONTEND_DIST / "host-assets"), name="host-assets")


@app.get("/favicon.ico")
async def favicon():
    from fastapi import Response
    return Response(status_code=204)


# ---------- status ----------
@app.get("/api/status")
async def status():
    assert hub.client
    s = hub.session
    return {
        "asr": {"model": settings.whisper_model_name, "ok": await asr.health(hub.client), "location": "127.0.0.1 (whisper.cpp)"},
        "llm": {"model": llm.model_for("safety"), **(await llm.health(hub.client)), "location": "127.0.0.1 (Ollama)"},
        "assistant": {"model": llm.model_for("assistant"), "location": "127.0.0.1 (Ollama)"},
        "summary": {"model": llm.model_for("summary"), "location": "127.0.0.1 (Ollama)"},
        "session": {"id": s.id, "active": s.active} if s else None,
        "notes": [{"filename": f} for f in hub.notes.files],
        "paragraph_count": len(hub.notes.paragraphs),
        "llm_pending": hub.llm.pending,
        "asr_pending": hub.asr_q.qsize(),
    }


@app.get("/api/metrics")
async def metrics():
    return hub.metrics.report()


# ---------- sessions ----------
@app.post("/api/sessions")
async def start_session(body: StartSessionRequest):
    if not body.consent:
        raise HTTPException(400, "explicit host consent is required to start capture")
    s = hub.new_session()
    await hub.broadcast({"type": "session.started", "session_id": s.id})
    return {"session_id": s.id}


@app.post("/api/sessions/{session_id}/stop")
async def stop_session(session_id: str):
    s = hub.get_session(session_id)
    if not s:
        raise HTTPException(404, "unknown session")
    if s.active:
        hub.end_session(s)
        await hub.broadcast({"type": "session.stopped", "session_id": s.id})
    return {"ok": True}


class AutoSuggest(BaseModel):
    enabled: bool


@app.post("/api/sessions/{session_id}/auto-suggest")
async def set_auto_suggest(session_id: str, body: AutoSuggest):
    s = hub.get_session(session_id)
    if not s:
        raise HTTPException(404, "unknown session")
    s.auto_suggest = body.enabled
    return {"auto_suggest": s.auto_suggest}


@app.get("/api/sessions/{session_id}/report")
async def call_report(session_id: str):
    from fastapi import Response

    from . import report
    s = hub.get_session(session_id)
    if not s:
        raise HTTPException(404, "unknown session")
    name, html = report.build(s)
    return Response(html, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@app.get("/api/demo-audio/{audio_id}")
async def demo_audio(audio_id: str):
    name = DEMO_AUDIO.get(audio_id)
    if not name:
        raise HTTPException(404)
    return FileResponse(DEMO_DIR / name, media_type="audio/wav")


# ---------- notes ----------
class NoteImport(BaseModel):
    filename: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=MAX_NOTE_CHARS)


@app.post("/api/notes")
async def import_note(body: NoteImport):
    if not body.filename.lower().endswith((".md", ".txt")):
        raise HTTPException(400, "only .md or .txt notes")
    paras = hub.notes.import_text(body.filename, body.text)
    return {"filename": body.filename, "paragraphs": [p.model_dump() for p in paras]}


@app.post("/api/notes/demo/{note_id}")
async def import_demo_note(note_id: str):
    name = DEMO_NOTES.get(note_id)
    if not name:
        raise HTTPException(404)
    paras = hub.notes.import_text(name, (DEMO_DIR / name).read_text())
    return {"filename": name, "paragraphs": [p.model_dump() for p in paras]}


@app.delete("/api/notes")
async def clear_notes():
    hub.notes.clear()
    return {"ok": True}


# ---------- private assistance ----------
@app.post("/api/ask")
async def ask(body: AskRequest):
    s = hub.get_session(body.session_id)
    if not s:
        raise HTTPException(404, "unknown session")
    return {"request_id": hub.ask(s, body.question.strip())}


@app.post("/api/tasks/{task_id}/approve")
async def approve_task(task_id: str, body: ApproveTaskRequest):
    s = hub.session
    task = s.tasks.get(task_id) if s else None
    if not task:
        raise HTTPException(404, "unknown task")
    approved = task.model_copy(update={"description": body.description.strip(),
                                       "due_text": body.due_text.strip(), "status": "approved"})
    s.tasks[task_id] = approved
    hub.approved_tasks.append(approved)
    await hub.broadcast({"type": "task.updated", "task": approved.model_dump()}, s)
    return approved.model_dump()


@app.post("/api/tasks/{task_id}/dismiss")
async def dismiss_task(task_id: str):
    s = hub.session
    task = s.tasks.get(task_id) if s else None
    if not task:
        raise HTTPException(404, "unknown task")
    s.tasks[task_id] = task.model_copy(update={"status": "dismissed"})
    await hub.broadcast({"type": "task.updated", "task": s.tasks[task_id].model_dump()}, s)
    return {"ok": True}


# ---------- live call rooms ----------
@app.post("/api/rooms")
async def create_room():
    if not settings.guest_enabled:
        raise HTTPException(409, "guest listener disabled (set GUEST_ENABLED=true and Tailscale settings in .env)")
    if not hub.session or not hub.session.active:
        raise HTTPException(409, "start a session first")
    room = await rooms.create()
    return {"join_url": f"{settings.guest_base_url}/join/{room.token}", "expires_in_s": 15 * 60}


@app.post("/api/rooms/close")
async def close_room():
    await rooms.close("host_closed")
    return {"ok": True}


@app.get("/api/live-config")
async def live_config():
    return {"guest_enabled": settings.guest_enabled, "guest_base_url": settings.guest_base_url if settings.guest_enabled else None}


# ---------- websockets ----------
@app.websocket("/ws/host-signal")
async def host_signal(ws: WebSocket):
    if not await _ws_guard(ws):
        return
    await ws.accept()
    room = await rooms.attach_host(ws.send_json)
    if not room:
        await ws.send_json({"type": "error", "detail": "no open room"})
        await ws.close(code=4404)
        return
    if room.guest_reserved:
        await ws.send_json({"type": "guest-joined"})
    try:
        while not room.closed:
            text = await ws.receive_text()
            if len(text) > 24_000:
                break
            try:
                await rooms.relay(room, from_host=True, raw=json.loads(text))
            except (SignalError, ValueError):
                continue
    except WebSocketDisconnect:
        pass
    finally:
        if room.host_send is not None and not room.closed:
            await rooms.close("host_disconnected")



@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    if not await _ws_guard(ws):
        return
    await ws.accept()
    hub.subscribers.add(ws)
    await ws.send_json({"type": "hello", "asr_model": settings.whisper_model_name, "llm_model": llm.model_for("safety"), "assistant_model": llm.model_for("assistant")})
    try:
        while True:
            await ws.receive_text()  # host never sends commands here; keepalive only
    except WebSocketDisconnect:
        pass
    finally:
        hub.subscribers.discard(ws)


class _Register(BaseModel):
    type: str
    session_id: str
    source: str
    origin: str
    sample_rate: int


@app.websocket("/ws/audio")
async def ws_audio(ws: WebSocket):
    """One WebSocket per capture. The first message registers it; the server binds source for its lifetime."""
    if not await _ws_guard(ws):
        return
    await ws.accept()
    try:
        reg = _Register.model_validate(json.loads(await ws.receive_text()))
    except (ValidationError, ValueError, WebSocketDisconnect):
        await ws.close(code=4400)
        return
    s = hub.get_session(reg.session_id)
    if (reg.type != "register" or not s or not s.active or reg.source not in ("HOST", "REMOTE")
            or reg.origin not in ("replay", "live") or reg.sample_rate not in ALLOWED_SAMPLE_RATES):
        await ws.send_json({"type": "error", "detail": "invalid registration"})
        await ws.close(code=4400)
        return
    if any(c.source == reg.source for c in s.captures.values()):
        await ws.send_json({"type": "error", "detail": f"a {reg.source} capture is already registered"})
        await ws.close(code=4409)
        return
    cap = hub.register_capture(s, reg.source, reg.origin, reg.sample_rate)
    await ws.send_json({"type": "registered", "capture_id": cap.id, "source": cap.source, "sample_rate": cap.sample_rate})
    try:
        while s.active:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is None:
                continue
            try:
                seq, t_ms, pcm = parse_frame(data, cap.sample_rate, cap.last_seq)
            except FrameError as exc:
                cap.dropped += 1
                if cap.dropped > MAX_DROPPED_BEFORE_CLOSE:
                    await ws.close(code=4400, reason=str(exc)[:100])
                    break
                continue
            cap.last_seq = seq
            await hub.on_frame(s, cap, t_ms, pcm)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.end_capture(s, cap)
