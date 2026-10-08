"""Single active host/guest room with a short-lived token, plus a strictly-filtered signaling relay.

This is the ONLY object shared between the private host app and the LAN/Tailscale guest app.
Only SDP, ICE candidates, mute and leave messages may cross it.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

TOKEN_TTL_S = 15 * 60
MAX_SDP = 20_000
MAX_CANDIDATE = 1_000
MAX_MESSAGES_PER_ROOM = 600

Send = Callable[[dict[str, Any]], Awaitable[None]]

HOST_TO_GUEST = {"offer", "ice", "leave"}
GUEST_TO_HOST = {"answer", "ice", "mute", "leave"}


class SignalError(ValueError):
    pass


def sanitize(msg: Any, allowed: set[str]) -> dict[str, Any]:
    """Rebuild the message from known fields only; anything else is dropped."""
    if not isinstance(msg, dict) or msg.get("type") not in allowed:
        raise SignalError("message type not allowed")
    t = msg["type"]
    if t in ("offer", "answer"):
        sdp = msg.get("sdp")
        if not isinstance(sdp, str) or not sdp.startswith("v=0") or len(sdp) > MAX_SDP:
            raise SignalError("invalid sdp")
        return {"type": t, "sdp": sdp}
    if t == "ice":
        c = msg.get("candidate")
        if c is None:
            return {"type": "ice", "candidate": None}  # end-of-candidates
        if not isinstance(c, dict):
            raise SignalError("invalid candidate")
        cand, mid, idx = c.get("candidate"), c.get("sdpMid"), c.get("sdpMLineIndex")
        if not isinstance(cand, str) or len(cand) > MAX_CANDIDATE:
            raise SignalError("invalid candidate string")
        if mid is not None and (not isinstance(mid, str) or len(mid) > 32):
            raise SignalError("invalid sdpMid")
        if idx is not None and (not isinstance(idx, int) or not 0 <= idx < 16):
            raise SignalError("invalid sdpMLineIndex")
        return {"type": "ice", "candidate": {"candidate": cand, "sdpMid": mid, "sdpMLineIndex": idx}}
    if t == "mute":
        return {"type": "mute", "muted": bool(msg.get("muted"))}
    return {"type": "leave"}


@dataclass
class Room:
    token: str
    created: float = field(default_factory=time.monotonic)
    guest_reserved: bool = False
    closed: bool = False
    messages: int = 0
    host_send: Send | None = None
    guest_send: Send | None = None

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created > TOKEN_TTL_S

    @property
    def joinable(self) -> bool:
        return not self.closed and not self.expired and not self.guest_reserved


class RoomCoordinator:
    def __init__(self) -> None:
        self.room: Room | None = None
        self._lock = asyncio.Lock()
        self.on_status: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    async def _status(self, state: str, **extra: Any) -> None:
        if self.on_status:
            await self.on_status({"type": "room.status", "state": state, **extra})

    # ----- host side -----
    async def create(self) -> Room:
        async with self._lock:
            if self.room and not self.room.closed:
                await self._close_locked("replaced")
            self.room = Room(token=secrets.token_urlsafe(16))  # 128 bits
        await self._status("waiting")
        return self.room

    async def attach_host(self, send: Send) -> Room | None:
        room = self.room
        if not room or room.closed:
            return None
        room.host_send = send
        return room

    async def close(self, reason: str = "closed") -> None:
        async with self._lock:
            await self._close_locked(reason)

    async def _close_locked(self, reason: str) -> None:
        room = self.room
        if not room or room.closed:
            return
        room.closed = True
        for send in (room.guest_send, room.host_send):
            if send:
                try:
                    await send({"type": "leave"})
                except Exception:
                    pass
        await self._status("closed", reason=reason)

    # ----- guest side -----
    def peek(self, token: str) -> bool:
        """GET of the join page: valid token, does NOT consume it."""
        room = self.room
        return bool(room and secrets.compare_digest(room.token, token) and room.joinable)

    async def reserve(self, token: str, send: Send) -> Room | None:
        """First successful guest signaling socket atomically reserves the room."""
        async with self._lock:
            room = self.room
            if not room or not secrets.compare_digest(room.token, token) or not room.joinable:
                return None
            room.guest_reserved = True
            room.guest_send = send
        await self._status("guest_joined")
        if room.host_send:
            await room.host_send({"type": "guest-joined"})
        return room

    async def guest_left(self, room: Room) -> None:
        if room is self.room and not room.closed:
            await self.close("guest_left")

    # ----- relay -----
    async def relay(self, room: Room, from_host: bool, raw: Any) -> None:
        if room.closed:
            raise SignalError("room closed")
        room.messages += 1
        if room.messages > MAX_MESSAGES_PER_ROOM:
            raise SignalError("too many signaling messages")
        msg = sanitize(raw, HOST_TO_GUEST if from_host else GUEST_TO_HOST)
        target = room.guest_send if from_host else room.host_send
        if msg["type"] == "mute" and not from_host:
            await self._status("guest_muted" if msg["muted"] else "guest_unmuted")
        if target:
            await target(msg)
        if msg["type"] == "leave":
            await self.close("host_left" if from_host else "guest_left")


rooms = RoomCoordinator()
