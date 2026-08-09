"""A recipient this service must never mail, enforced at the transport.

Four different things can aim mail at a dead address -- a routing record, an
env var, a stale automation rule, a hand-typed CC -- and only the transport
sees all four. So the block lives there, not in each caller.

This is not about tidiness. Sustained delivery failures to a non-existent
domain are what gets the sending Gmail account rate-limited or suspended, and
that takes every real escalation down with it.
"""

from __future__ import annotations

from typing import Any

from chatbot.features.metrics.email_sender import SmtpEmailSender


class _FakeSMTP:
    sent: list = []

    def __init__(self, host: str, port: int) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)


class _Settings:
    smtp_host = "smtp.test"
    smtp_port = 587
    smtp_user = ""
    smtp_password = ""
    smtp_from = "Support <support@test>"
    email_blocked_recipients = "pic@emas.proton.com, proton.demo@demo.com"


def _send(**kwargs: Any) -> list:
    _FakeSMTP.sent = []
    sender = SmtpEmailSender(_Settings(), smtp_factory=_FakeSMTP)
    sender.send(
        to=kwargs.get("to", ["ok@test"]),
        cc=kwargs.get("cc", []),
        subject="s",
        body="b",
        attachments=[],
    )
    return _FakeSMTP.sent


def test_a_blocked_address_in_to_is_dropped():
    sent = _send(to=["pic@emas.proton.com", "ok@test"])
    assert len(sent) == 1
    assert "pic@emas.proton.com" not in sent[0]["To"]
    assert "ok@test" in sent[0]["To"]


def test_a_mail_addressed_only_to_blocked_recipients_is_not_sent_at_all():
    assert _send(to=["proton.demo@demo.com"]) == []


def test_a_blocked_cc_is_dropped_but_the_mail_still_goes():
    sent = _send(to=["ok@test"], cc=["pic@emas.proton.com", "mgr@test"])
    assert len(sent) == 1
    assert "pic@emas.proton.com" not in (sent[0]["Cc"] or "")
    assert "mgr@test" in sent[0]["Cc"]


def test_a_cc_is_never_promoted_into_an_empty_to_line():
    """Otherwise blocking every To recipient would quietly redirect the mail."""
    assert _send(to=["pic@emas.proton.com"], cc=["mgr@test"]) == []


def test_the_block_is_case_and_whitespace_insensitive():
    assert _send(to=["  PIC@Emas.Proton.COM "]) == []


def test_an_unblocked_address_is_unaffected():
    sent = _send(to=["real@test"], cc=["cc@test"])
    assert len(sent) == 1
    assert sent[0]["To"] == "real@test"


def test_an_empty_blocklist_changes_nothing():
    class _Open(_Settings):
        email_blocked_recipients = ""

    _FakeSMTP.sent = []
    SmtpEmailSender(_Open(), smtp_factory=_FakeSMTP).send(
        to=["pic@emas.proton.com"], cc=[], subject="s", body="b", attachments=[]
    )
    assert len(_FakeSMTP.sent) == 1
