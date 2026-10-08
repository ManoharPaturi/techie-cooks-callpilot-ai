"""Host-app boundary checks: bind, Host/Origin guards, capture-source binding, untrusted caller speech."""
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import config
from backend.audio import HEADER
from backend.host_app import app
from backend.session import hub

BASE = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8765"
ORIGIN = {"Origin": "http://127.0.0.1:8765"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url=BASE) as c:
        yield c


def test_refuses_non_loopback_bind():
    for bad in ("0.0.0.0", "192.168.1.10", "::"):
        with pytest.raises(SystemExit):
            config.assert_loopback(bad)
    config.assert_loopback("127.0.0.1")


def test_foreign_host_header_rejected(client):
    assert client.get("/api/status", headers={"Host": "evil.example:8765"}).status_code == 403


def test_cross_origin_mutation_rejected(client):
    r = client.post("/api/sessions", json={"consent": True}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert client.post("/api/sessions", json={"consent": True}).status_code == 403  # missing Origin


def test_session_requires_consent(client):
    assert client.post("/api/sessions", json={"consent": False}, headers=ORIGIN).status_code == 400


def test_no_guest_or_docs_routes_on_host(client):
    for path in ("/docs", "/openapi.json", "/join/abc", "/ws/guest-signal/abc"):
        assert client.get(path).status_code == 404


def test_ws_rejects_foreign_origin(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(WS + "/ws/events", headers={"Origin": "http://evil.example"}) as ws:
            ws.receive_json()


def _start(client) -> str:
    return client.post("/api/sessions", json={"consent": True}, headers=ORIGIN).json()["session_id"]


def test_capture_source_is_bound_and_unique(client):
    sid = _start(client)
    reg = {"type": "register", "session_id": sid, "source": "REMOTE", "origin": "replay", "sample_rate": 48000}
    with client.websocket_connect(WS + "/ws/audio", headers=ORIGIN) as a:
        a.send_text(json.dumps(reg))
        first = a.receive_json()
        assert first["type"] == "registered" and first["source"] == "REMOTE"
        # A second REMOTE capture cannot be registered while the first is active.
        with client.websocket_connect(WS + "/ws/audio", headers=ORIGIN) as b:
            b.send_text(json.dumps(reg))
            assert b.receive_json()["type"] == "error"
        # Frames carry no source field at all; the server mapping is authoritative.
        a.send_bytes(HEADER.pack(1, 0.0) + np.zeros(960, "<i2").tobytes())
    assert hub.session.captures == {}


@pytest.mark.parametrize("bad", [
    {"source": "GUEST"}, {"origin": "cloud"}, {"sample_rate": 12345}, {"session_id": "s-unknown"},
])
def test_invalid_registration_rejected(client, bad):
    sid = _start(client)
    reg = {"type": "register", "session_id": sid, "source": "HOST", "origin": "replay", "sample_rate": 48000, **bad}
    with client.websocket_connect(WS + "/ws/audio", headers=ORIGIN) as ws:
        ws.send_text(json.dumps(reg))
        assert ws.receive_json()["type"] == "error"


def test_task_approval_requires_known_task_and_origin(client):
    _start(client)
    assert client.post("/api/tasks/t-99/approve", json={"description": "x"}, headers=ORIGIN).status_code == 404
    assert client.post("/api/tasks/t-99/approve", json={"description": "x"}).status_code == 403


def test_caller_speech_cannot_trigger_actions():
    """Transcript text is data: no code path maps caller words to API calls, tools or note export."""
    import inspect

    from backend import session
    src = inspect.getsource(session.Hub._transcribe) + inspect.getsource(session.Hub._safety_stage1)
    for forbidden in ("notes.search", "ask(", "approve", "import_text", "export"):
        assert forbidden not in src
