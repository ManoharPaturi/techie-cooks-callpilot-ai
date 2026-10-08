"""Rules, ID-grounding validation and (when available) the local Qwen labeled cases."""
import json
from pathlib import Path

import httpx
import pytest

from backend import grounding, safety
from backend.models import Utterance

from .conftest import requires_ollama

CASES = json.loads((Path(__file__).parent.parent / "demo" / "labeled_cases.json").read_text())


def utt(i: int, text: str, source: str = "REMOTE") -> Utterance:
    return Utterance(id=f"u-{i:04d}", session_id="t", source=source, origin="replay", text=text, start_ms=i * 1000, end_ms=i * 1000 + 900)


# ---------- stage 1 rules ----------
@pytest.mark.parametrize("text,expected", [
    ("Give me the OTP so I can unlock your account.", "otp_or_code"),
    ("Please tell me the O.T.P. right now", "otp_or_code"),
    ("Read out the six digit code you just received.", "otp_or_code"),
    ("What is your login password?", "password"),
    ("Please install AnyDesk so I can fix it.", "remote_access"),
    ("Transfer 10,000 rupees immediately.", "payment"),
    ("Your account will be closed today.", "pressure"),
    ("Enter your UPI PIN to receive the refund.", "upi_pin"),
    ("You are under digital arrest, do not disconnect this video call.", "digital_arrest"),
    ("A parsel booked in your name was seized by customs.", "parcel_scam"),  # Whisper misspelling
    ("Share your Aadhaar number to update KYC.", "kyc_update"),
    ("The courier will deliver your parcel tomorrow afternoon.", None),
    ("We need the product images before Friday.", None),
    ("Can you confirm the website will be delivered by Friday?", None),
    ("I'll send the menu text tomorrow.", None),
])
def test_rules(text, expected):
    assert safety.scan(text) == expected


# ---------- app-side alert policy ----------
def test_warning_or_mention_never_displayed_as_danger():
    assert safety.alert_level("warning", "high") == "cleared"
    assert safety.alert_level("mention", "high") == "info"
    assert safety.alert_level("request", "high") == "danger"
    assert safety.alert_level("request", "uncertain") == "uncertain"
    assert safety.alert_level("uncertain", "high") == "uncertain"


# ---------- grounding ----------
def test_schema_enum_is_exactly_the_supplied_ids():
    schema = grounding.safety_schema(["u-0001", "u-0002"], "u-0002")
    assert schema["properties"]["assessed_id"]["enum"] == ["u-0002"]
    assert schema["properties"]["supporting_ids"]["items"]["enum"] == ["u-0001"]
    assert "evidence_quote" not in schema["properties"]


def test_schema_rejects_trigger_outside_allowed():
    with pytest.raises(grounding.GroundingError):
        grounding.safety_schema(["u-0001"], "u-0009")


GOOD = {"assessed_id": "u-0002", "supporting_ids": ["u-0001"], "intent": "request", "category": "otp_or_code", "severity": "high"}


def test_validate_accepts_good():
    out = grounding.validate_safety(GOOD, ["u-0001", "u-0002"], "u-0002")
    assert out["intent"] == "request" and out["transcript_ids"] == ["u-0002", "u-0001"]


@pytest.mark.parametrize("patch", [
    {"supporting_ids": ["u-9999"]},                    # made-up ID
    {"assessed_id": "u-0001"},                         # assesses something other than the trigger
    {"supporting_ids": ["u-0001", "u-0001"]},          # duplicate
    {"supporting_ids": ["u-0002"]},                    # trigger is not a supporting line
    {"intent": "scam"},                                 # not an allowed enum
    {"explanation": "Model-written prose is not allowed"},  # the app writes all user-facing text
    {"evidence_quote": "Give me the OTP"},              # quotes are not allowed at all
])
def test_validate_rejects_bad(patch):
    with pytest.raises(grounding.GroundingError):
        grounding.validate_safety({**GOOD, **patch}, ["u-0001", "u-0002"], "u-0002")


def test_coach_note_claim_without_citation_is_downgraded():
    out = grounding.validate_coach(
        {"basis": "notes", "transcript_ids": [], "note_paragraph_ids": [], "text": "Yes."}, ["u-0001"], ["n-001:p-001"])
    assert out["basis"] == "general" and out["found_in_notes"] is False
    out = grounding.validate_coach(
        {"basis": "conversation", "transcript_ids": ["u-0001"], "note_paragraph_ids": [], "text": "No."}, ["u-0001"], [])
    assert out["basis"] == "conversation"


def test_coach_rejects_invented_note_id():
    with pytest.raises(grounding.GroundingError):
        grounding.validate_coach(
            {"basis": "notes", "transcript_ids": [], "note_paragraph_ids": ["n-009:p-001"], "text": "x"}, [], ["n-001:p-001"])


async def test_model_failure_becomes_uncertain_not_confident(monkeypatch):
    async def boom(*a, **k):
        raise safety.llm.LLMError("down")
    monkeypatch.setattr(safety.llm, "chat_json", boom)
    trig = utt(1, "Give me the OTP")
    async with httpx.AsyncClient() as client:
        fields, reason = await safety.assess(client, [trig], trig, "otp_or_code")
    assert fields["intent"] == "uncertain" and reason


async def test_invented_id_from_model_is_rejected(monkeypatch):
    async def invent(*a, **k):
        return {**GOOD, "supporting_ids": ["u-0042"]}
    monkeypatch.setattr(safety.llm, "chat_json", invent)
    trig = utt(2, "Give me the OTP")
    async with httpx.AsyncClient() as client:
        fields, reason = await safety.assess(client, [utt(1, "hello"), trig], trig, "otp_or_code")
    assert fields["intent"] == "uncertain" and "unknown IDs" in reason


# ---------- local Qwen labeled set (real model, real results) ----------
@requires_ollama
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
async def test_labeled_case_with_local_qwen(case):
    context = [utt(i + 1, t, "REMOTE") for i, t in enumerate(case["context"])]
    trig = utt(len(context) + 1, case["text"])
    category = safety.scan(trig.text)
    if category is None:
        # Not flagged by rules: no Qwen call and no alert, which must match expectation.
        assert case["expect_alert"] == "none", "rules missed a case that expects an alert"
        return
    async with httpx.AsyncClient() as client:
        fields, reason = await safety.assess(client, context + [trig], trig, category)
    alert = safety.alert_level(fields["intent"], fields["severity"])
    shown = "none" if alert in ("cleared", "info") else alert
    if shown != case["expect_alert"] and case.get("known_miss"):
        pytest.xfail(case["known_miss"])  # ran, failed as documented; a pass is reported as a normal pass
    assert shown == case["expect_alert"], f"intent={fields['intent']} severity={fields['severity']} fallback={reason}"


def test_app_writes_the_advice_not_the_model():
    expl, action = safety.describe("request", "digital_arrest")
    assert "digital arrest" in action and "1930" in action
    assert safety.describe("warning", "otp_or_code")[1] == "No action needed."
    for cat in safety.NOUN:
        assert safety.describe("request", cat)[1]  # every category has vetted advice
