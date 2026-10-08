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
    _, html = report.build(make_session())
    assert "Private notes and assistant suggestions are intentionally not included" in html
    assert "client_agreement" not in html and "Suggested reply" not in html


def test_clean_call_report():
    s = Session(id="s-2")
    _, html = report.build(s)
    assert "No suspicious requests were flagged" in html and "1930" not in html


def test_report_shows_what_the_caller_asked_for_and_who_wrote_it():
    s = make_session()
    s.summary = "A caller claiming to be CBI said you were under digital arrest."
    s.summary_model = "gemma4:e2b"
    first = next(iter(s.utterances.values()))
    s.caller_requests = [{"request": "stay on the <video> call", "transcript_ids": [first.id],
                          "evidence": [{"id": first.id, "source": first.source, "text": first.text, "start_ms": first.start_ms}]}]
    _, html = report.build(s)
    assert "What the caller asked for" in html and "stay on the &lt;video&gt; call" in html
    assert "Written by Gemma 4 E2B on this device" in html


def test_report_does_not_credit_a_model_for_a_fallback_summary():
    s = make_session()
    s.summary = "Summary unavailable (local model error)."
    _, html = report.build(s)
    assert "Written by" not in html and "What the caller asked for" not in html
