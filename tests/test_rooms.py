"""Room token lifecycle, signaling sanitization and guest-app route isolation."""
import dataclasses
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import guest_app as guest_mod
from backend import rooms as rooms_mod
from backend.config import settings as _base_settings
from backend.guest_app import app as guest_app
from backend.rooms import RoomCoordinator, SignalError, sanitize

# Self-contained guest config (independent of any local .env).
settings = dataclasses.replace(_base_settings, guest_domain="mac.example.ts.net", guest_port=8443)
GUEST_HOST = f"{settings.guest_domain}:{settings.guest_port}"


async def _noop(_msg):
    pass


async def test_peek_does_not_consume_and_second_guest_rejected():
    rc = RoomCoordinator()
    room = await rc.create()
    assert rc.peek(room.token) and rc.peek(room.token)  # GET twice is fine
    assert await rc.reserve(room.token, _noop) is room
    assert not rc.peek(room.token)
    assert await rc.reserve(room.token, _noop) is None  # second guest


async def test_wrong_expired_and_replaced_tokens_fail(monkeypatch):
    rc = RoomCoordinator()
    old = await rc.create()
    new = await rc.create()  # replaces old
    assert await rc.reserve(old.token, _noop) is None
    assert await rc.reserve("guess", _noop) is None
    monkeypatch.setattr(rooms_mod, "TOKEN_TTL_S", 0)
    time.sleep(0.01)
    assert await rc.reserve(new.token, _noop) is None


async def test_token_is_long_and_random():
    rc = RoomCoordinator()
    a, b = (await rc.create()).token, (await rc.create()).token
    assert a != b and len(a) >= 22  # 16 bytes urlsafe


@pytest.mark.parametrize("msg,allowed", [
    ({"type": "notes"}, {"answer"}),
    ({"type": "offer", "sdp": "v=0..."}, {"answer", "ice"}),            # guest may not send offers
    ({"type": "answer", "sdp": "<script>"}, {"answer"}),
    ({"type": "answer", "sdp": "v=0" + "x" * 30000}, {"answer"}),
    ({"type": "ice", "candidate": {"candidate": 5}}, {"ice"}),
    ({"type": "ice", "candidate": {"candidate": "c", "sdpMLineIndex": 99}}, {"ice"}),
    ("not a dict", {"ice"}),
])
def test_sanitize_rejects(msg, allowed):
    with pytest.raises(SignalError):
        sanitize(msg, allowed)


def test_sanitize_strips_unknown_fields():
    out = sanitize({"type": "mute", "muted": 1, "transcript": "secret"}, {"mute"})
    assert out == {"type": "mute", "muted": True}


# ---------- guest app isolation ----------
@pytest.fixture(scope="module")
def guest():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(guest_mod, "settings", settings)
        with TestClient(guest_app, base_url=f"https://{GUEST_HOST}") as c:
            yield c


def test_guest_has_no_private_routes(guest):
    for path in ("/", "/api/status", "/api/notes", "/api/ask", "/api/tasks/t-01/approve", "/api/metrics",
                 "/api/demo-audio/remote_scam", "/host-assets/x.js", "/docs", "/openapi.json"):
        assert guest.get(path).status_code == 404, path
    for path in ("/ws/events", "/ws/audio", "/ws/host-signal"):
        with pytest.raises(WebSocketDisconnect):
            with guest.websocket_connect(f"wss://{GUEST_HOST}{path}", headers={"Origin": settings.guest_base_url}):
                pass


def test_guest_rejects_mutations_and_foreign_host(guest):
    assert guest.post("/api/notes", json={}).status_code == 405
    assert guest.get("/join/x", headers={"Host": "evil.example"}).status_code == 403


def test_join_page_invalid_token_and_headers(guest):
    r = guest.get("/join/not-a-token")
    assert r.status_code == 404
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_guest_ws_requires_origin_and_valid_token(guest):
    with pytest.raises(WebSocketDisconnect):
        with guest.websocket_connect(f"wss://{GUEST_HOST}/ws/guest-signal/x", headers={"Origin": "https://evil.example"}):
            pass
    with guest.websocket_connect(f"wss://{GUEST_HOST}/ws/guest-signal/x", headers={"Origin": settings.guest_base_url}) as ws:
        assert ws.receive_json()["type"] == "error"


def test_guest_bind_must_be_exact_ip():
    from backend.config import assert_guest_bind
    for bad in ("0.0.0.0", "::", "my-mac.local"):
        with pytest.raises(SystemExit):
            assert_guest_bind(bad)
    assert_guest_bind("100.101.102.103")
