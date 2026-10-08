"""Per-request ID-constrained JSON schemas and post-validation.

The model may only *reference* IDs that were supplied in that exact call. It never produces quotes:
the app renders evidence from the immutable stores using validated IDs.
"""
from __future__ import annotations

from typing import Any, Iterable

INTENTS = ["request", "mention", "warning", "uncertain"]
CATEGORIES = ["upi_pin", "otp_or_code", "password", "payment", "remote_access", "kyc_update", "digital_arrest", "parcel_scam", "pressure", "other"]
SEVERITIES = ["none", "caution", "high", "uncertain"]
# Where a private answer comes from: the user's notes, this call's transcript, general safety knowledge,
# or "not_found" when a specific document fact was asked for and is not in the notes.
BASES = ["notes", "conversation", "general", "not_found"]


class GroundingError(ValueError):
    pass


def _id_array(allowed: list[str], *, min_items: int, max_items: int) -> dict[str, Any]:
    if not allowed:
        return {"type": "array", "maxItems": 0, "items": {"type": "string"}}
    return {
        "type": "array",
        "minItems": min_items,
        "maxItems": min(max_items, len(allowed)),
        "uniqueItems": True,
        "items": {"type": "string", "enum": list(allowed)},
    }


def safety_schema(allowed_transcript_ids: list[str], trigger_id: str) -> dict[str, Any]:
    """`assessed_id` is a single-value enum, so the grammar forces the verdict to be anchored on the trigger;
    `supporting_ids` may cite only the other IDs supplied in this call."""
    if trigger_id not in allowed_transcript_ids:
        raise GroundingError("trigger_id must be one of the supplied transcript IDs")
    others = [i for i in allowed_transcript_ids if i != trigger_id]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["assessed_id", "supporting_ids", "intent", "category", "severity"],
        "properties": {
            "assessed_id": {"type": "string", "enum": [trigger_id]},
            "supporting_ids": _id_array(others, min_items=0, max_items=2),
            "intent": {"type": "string", "enum": INTENTS},
            "category": {"type": "string", "enum": CATEGORIES},
            "severity": {"type": "string", "enum": SEVERITIES},
        },
    }


def coach_schema(allowed_transcript_ids: list[str], allowed_note_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["basis", "transcript_ids", "note_paragraph_ids", "text"],
        "properties": {
            "basis": {"type": "string", "enum": BASES},
            "transcript_ids": _id_array(allowed_transcript_ids, min_items=0, max_items=3),
            "note_paragraph_ids": _id_array(allowed_note_ids, min_items=0, max_items=3),
            "text": {"type": "string", "maxLength": 280},
        },
    }


def summary_schema(allowed_transcript_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "caller_requests", "tasks"],
        "properties": {
            "summary": {"type": "string", "maxLength": 500},
            "caller_requests": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["request", "transcript_ids"],
                    "properties": {
                        "request": {"type": "string", "maxLength": 120},
                        "transcript_ids": _id_array(allowed_transcript_ids, min_items=1, max_items=2),
                    },
                },
            },
            "tasks": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["description", "due_text", "transcript_ids"],
                    "properties": {
                        "description": {"type": "string", "maxLength": 200},
                        "due_text": {"type": "string", "maxLength": 80},
                        "transcript_ids": _id_array(allowed_transcript_ids, min_items=1, max_items=3),
                    },
                },
            },
        },
    }


def check_ids(value: Any, allowed: Iterable[str], *, min_items: int, max_items: int, field: str) -> list[str]:
    allowed_set = set(allowed)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise GroundingError(f"{field} must be a list of strings")
    if len(set(value)) != len(value):
        raise GroundingError(f"{field} contains duplicates")
    unknown = [v for v in value if v not in allowed_set]
    if unknown:
        raise GroundingError(f"{field} references unknown IDs {unknown}")
    if not (min_items <= len(value) <= max_items):
        raise GroundingError(f"{field} must have {min_items}-{max_items} items")
    return value


def check_text(value: Any, field: str, max_len: int) -> str:
    if not isinstance(value, str):
        raise GroundingError(f"{field} must be a string")
    value = " ".join(value.split())
    if len(value) > max_len:
        raise GroundingError(f"{field} too long")
    return value


def check_enum(value: Any, allowed: list[str], field: str) -> str:
    if value not in allowed:
        raise GroundingError(f"{field}={value!r} not allowed")
    return value


def validate_safety(raw: dict[str, Any], allowed_ids: list[str], trigger_id: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise GroundingError("response is not an object")
    extra = set(raw) - {"assessed_id", "supporting_ids", "intent", "category", "severity"}
    if extra:
        raise GroundingError(f"unexpected fields {sorted(extra)}")
    if raw.get("assessed_id") != trigger_id:
        raise GroundingError("assessment does not reference the triggering utterance")
    others = [i for i in allowed_ids if i != trigger_id]
    supporting = check_ids(raw.get("supporting_ids"), others, min_items=0, max_items=2, field="supporting_ids")
    return {
        "transcript_ids": [trigger_id, *supporting],
        "intent": check_enum(raw.get("intent"), INTENTS, "intent"),
        "category": check_enum(raw.get("category"), CATEGORIES, "category"),
        "severity": check_enum(raw.get("severity"), SEVERITIES, "severity"),
    }


def validate_coach(raw: dict[str, Any], allowed_ids: list[str], allowed_notes: list[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise GroundingError("response is not an object")
    basis = check_enum(raw.get("basis"), BASES, "basis")
    notes = check_ids(raw.get("note_paragraph_ids"), allowed_notes, min_items=0, max_items=3, field="note_paragraph_ids")
    ids = check_ids(raw.get("transcript_ids"), allowed_ids, min_items=0, max_items=3, field="transcript_ids")
    # App-side consistency: a "notes" answer must cite a paragraph; a "conversation" answer must cite a line.
    if basis == "notes" and not notes:
        basis = "conversation" if ids else "general"
    if basis == "conversation" and not ids:
        basis = "general"
    if notes and basis != "notes":
        basis = "notes"
    elif ids and basis == "general":
        basis = "conversation"  # citations of call lines define the basis
    return {
        "basis": basis,
        "transcript_ids": ids,
        "note_paragraph_ids": notes,
        "found_in_notes": basis == "notes",
        "text": check_text(raw.get("text"), "text", 280),
    }


def validate_summary(raw: dict[str, Any], allowed_ids: list[str]) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("tasks"), list):
        raise GroundingError("summary response malformed")
    tasks = []
    for t in raw["tasks"][:3]:
        if not isinstance(t, dict):
            raise GroundingError("task malformed")
        tasks.append({
            "description": check_text(t.get("description"), "description", 200),
            "due_text": check_text(t.get("due_text", ""), "due_text", 80),
            "transcript_ids": check_ids(t.get("transcript_ids"), allowed_ids, min_items=1, max_items=3, field="task.transcript_ids"),
        })
    requests = []
    for r in (raw.get("caller_requests") or [])[:3]:
        if not isinstance(r, dict):
            raise GroundingError("caller request malformed")
        requests.append({
            "request": check_text(r.get("request"), "request", 120),
            "transcript_ids": check_ids(r.get("transcript_ids"), allowed_ids, min_items=1, max_items=2,
                                        field="caller_requests.transcript_ids"),
        })
    return {"summary": check_text(raw.get("summary"), "summary", 500), "caller_requests": requests, "tasks": tasks}
