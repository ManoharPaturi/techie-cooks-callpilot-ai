"""Automatic suggestions: fire on real caller questions, never on scam lines, rate-limited, can be switched off."""
from backend import llm
from backend.models import Utterance
from backend.session import Hub, Session


def utt(i: int, text: str, source: str = "REMOTE") -> Utterance:
    return Utterance(id=f"u-{i:04d}", session_id="s", source=source, origin="replay", text=text, start_ms=i * 1000, end_ms=i * 1000 + 900)


def harness(monkeypatch):
    hub, submitted = Hub(), []
    monkeypatch.setattr(hub.llm, "submit", lambda prio, fn, key=None: submitted.append((prio, key)))
    s = Session(id="s")
    return hub, s, submitted


def test_caller_question_triggers_auto_suggestion(monkeypatch):
    hub, s, sub = harness(monkeypatch)
    hub._maybe_proactive(s, utt(1, "Can you confirm the website will be delivered by Friday?"))
    assert sub == [(llm.PRIORITY_PROACTIVE, "auto")]


def test_scam_question_and_host_lines_do_not_trigger(monkeypatch):
    hub, s, sub = harness(monkeypatch)
    hub._maybe_proactive(s, utt(1, "Can you tell me the OTP you just received?"))  # safety handles this
    hub._maybe_proactive(s, utt(2, "We will send the images before Friday."))       # not a question
    assert sub == []


def test_rate_limit_and_toggle(monkeypatch):
    hub, s, sub = harness(monkeypatch)
    hub._maybe_proactive(s, utt(1, "Can you confirm the delivery date for the website?"))
    hub._maybe_proactive(s, utt(2, "Is bulk product upload included in the package?"))  # within 8 s
    assert len(sub) == 1
    s.last_proactive = 0
    s.auto_suggest = False
    hub._maybe_proactive(s, utt(3, "Is bulk product upload included in the package?"))
    assert len(sub) == 1


def test_auto_and_manual_use_separate_keys(monkeypatch):
    hub, s, sub = harness(monkeypatch)
    hub._maybe_proactive(s, utt(1, "Is bulk product upload included in the package?"))
    hub.ask(s, "what should I say?")
    assert {k for _, k in sub} == {"auto", "coach"}
