"""Shareable call report: correct content, escaped caller speech, and no private data."""
from backend import report
from backend.models import EvidenceLine, SafetyAssessment, Utterance
from backend.session import Session


def make_session() -> Session:
    s = Session(id="s-1")
    lines = [("REMOTE", "This is CBI. You are under digital arrest, do not disconnect this video call."),
             ("HOST", "Who is this?"),
             ("REMOTE", "<script>alert(1)</script> Enter your UPI PIN to receive the refund.")]
    for i, (src, text) in enumerate(lines, start=1):
        u = Utterance(id=f"u-{i:04d}", session_id="s-1", source=src, origin="replay", text=text, start_ms=i * 5000, end_ms=i * 5000 + 900)
        s.utterances[u.id] = u
    for uid, cat in (("u-0001", "digital_arrest"), ("u-0003", "upi_pin")):
        u = s.utterances[uid]
        s.assessments[uid] = SafetyAssessment(trigger_id=uid, transcript_ids=[uid], intent="request", category=cat, severity="high",
            explanation="x", suggested_action="y", alert="danger",
            evidence=[EvidenceLine(id=uid, source=u.source, text=u.text, start_ms=u.start_ms)], model="test")
    s.summary = "Caller claimed to be CBI."
    return s


def test_report_names_pattern_and_quotes_exact_words():
    name, html = report.build(make_session())
    assert name.startswith("callpilot-call-report-") and name.endswith(".html")
    assert "fake “digital arrest” scam" in html and "2 suspicious requests" in html
    assert "do not disconnect this video call" in html and "1930" in html


def test_report_escapes_caller_speech():
    _, html = report.build(make_session())
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_report_excludes_private_data():
    from backend.notes import NoteStore
    _, html = report.build(make_session())
    assert "Private notes and assistant suggestions are intentionally not included" in html
    assert "client_agreement" not in html and "Suggested reply" not in html


def test_clean_call_report():
    s = Session(id="s-2")
    _, html = report.build(s)
    assert "No suspicious requests were flagged" in html and "1930" not in html
