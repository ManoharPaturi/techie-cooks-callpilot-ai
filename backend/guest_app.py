"""Minimal guest app on the Tailscale/LAN interface (HTTPS). Serves ONLY the join page, guest assets and
guest signaling. It has no host routes, no events, no notes, no model or audio-upload endpoints."""
from __future__ import annotations

import json

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import GUEST_DIST, settings
from .rooms import SignalError, rooms

MAX_SIGNAL_BYTES = 24_000

app = FastAPI(title="CallPilot guest", docs_url=None, redoc_url=None, openapi_url=None)

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self' blob: mediastream:; connect-src 'self' wss:; frame-ancestors 'none'; base-uri 'none'"
)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.headers.get("host") not in settings.guest_allowed_hosts:
        return JSONResponse({"error": "host not allowed"}, status_code=403)
    if request.method not in ("GET", "HEAD"):
        return JSONResponse({"error": "method not allowed"}, status_code=405)
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["Referrer-Policy"] = "no-referrer"  # never leak the join token
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.get("/join/{token}", response_class=HTMLResponse)
async def join_page(token: str):
    if not rooms.peek(token):
        return HTMLResponse(
            "<!doctype html><meta name=viewport content='width=device-width'><body style='font-family:system-ui;padding:24px'>"
            "<h2>This call link is invalid, expired or already used.</h2><p>Ask the host for a new link.</p>",
            status_code=404,
        )
    page = GUEST_DIST / "guest" / "index.html"
    if not page.exists():
        return HTMLResponse("Guest page not built", status_code=503)
    return FileResponse(page)


if (GUEST_DIST / "guest-assets").exists():
    app.mount("/guest-assets", StaticFiles(directory=GUEST_DIST / "guest-assets"), name="guest-assets")


@app.websocket("/ws/guest-signal/{token}")
async def guest_signal(ws: WebSocket, token: str):
    if ws.headers.get("host") not in settings.guest_allowed_hosts or ws.headers.get("origin") not in settings.guest_allowed_origins:
        await ws.close(code=4403)
        return
    await ws.accept()
    room = await rooms.reserve(token, ws.send_json)
    if not room:
        await ws.send_json({"type": "error", "detail": "link invalid, expired or already in use"})
        await ws.close(code=4409)
        return
    await ws.send_json({"type": "ready"})
    try:
        while not room.closed:
            text = await ws.receive_text()
            if len(text) > MAX_SIGNAL_BYTES:
                break
            try:
                await rooms.relay(room, from_host=False, raw=json.loads(text))
            except (SignalError, ValueError):
                continue  # drop invalid messages silently
    except WebSocketDisconnect:
        pass
    finally:
        await rooms.guest_left(room)
