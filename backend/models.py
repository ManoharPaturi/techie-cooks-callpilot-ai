"""Pydantic event and request models shared by the host app."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["HOST", "REMOTE"]
Origin = Literal["replay", "live"]
Intent = Literal["request", "mention", "warning", "uncertain"]
Category = Literal[
    "upi_pin", "otp_or_code", "password", "payment", "remote_access", "kyc_update", "digital_arrest",
    "parcel_scam", "pressure", "other",
]
Severity = Literal["none", "caution", "high", "uncertain"]
# What the UI shows. Derived by the app from intent+severity, never by the model directly.
AlertLevel = Literal["danger", "caution", "cleared", "info", "uncertain"]


class Utterance(BaseModel):
    """Immutable finalized transcript line. `text` is exactly what Whisper returned."""

    model_config = {"frozen": True}

    type: Literal["transcript.final"] = "transcript.final"
    id: str
    session_id: str
    source: Source
    origin: Origin
    text: str
    start_ms: int
    end_ms: int


class EvidenceLine(BaseModel):
    id: str
    source: Source
    text: str
    start_ms: int


class NoteParagraph(BaseModel):
    id: str
    filename: str
    content: str


class SafetyCandidate(BaseModel):
    type: Literal["safety.candidate"] = "safety.candidate"
    trigger_id: str
    category: Category
    status: Literal["checking_context"] = "checking_context"


class SafetyAssessment(BaseModel):
    type: Literal["safety.assessment"] = "safety.assessment"
    trigger_id: str
    transcript_ids: list[str]
    intent: Intent
    category: Category
    severity: Severity
    explanation: str
    suggested_action: str
    alert: AlertLevel
    evidence: list[EvidenceLine]  # rendered from the immutable store, never from model text
    model: str
    fallback_reason: str | None = None
    latency_ms: int | None = None


class CoachSuggestion(BaseModel):
    type: Literal["coach.suggestion"] = "coach.suggestion"
    id: str
    question: str
    transcript_ids: list[str]
    note_paragraph_ids: list[str]
    text: str
    found_in_notes: bool
    basis: Literal["notes", "conversation", "general", "not_found"] = "general"
    evidence: list[EvidenceLine]
    paragraphs: list[NoteParagraph]
    audience: Literal["HOST_ONLY"] = "HOST_ONLY"
    model: str
    fallback_reason: str | None = None
    latency_ms: int | None = None
    proactive: bool = False  # True when CallPilot suggested this on its own after a caller question
    trigger_id: str | None = None  # the caller line an automatic suggestion answers


class TaskProposal(BaseModel):
    id: str
    description: str
    due_text: str
    transcript_ids: list[str]
    evidence: list[EvidenceLine]
    status: Literal["proposed", "approved", "dismissed"] = "proposed"


class StartSessionRequest(BaseModel):
    consent: bool


class AskRequest(BaseModel):
    session_id: str
    question: str = Field(default="", max_length=400)


class ApproveTaskRequest(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    due_text: str = Field(default="", max_length=120)
