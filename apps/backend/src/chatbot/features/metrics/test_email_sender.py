from __future__ import annotations

from email.message import EmailMessage
from typing import Any
from unittest.mock import MagicMock

from chatbot.features.metrics.email_sender import SmtpEmailSender
from chatbot.platform.config import Settings


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "user@example.com",
        "smtp_password": "secret",
        "smtp_from": "noreply@proton.my",
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


def _capture_smtp() -> tuple[list[EmailMessage], MagicMock]:
    sent: list[EmailMessage] = []

    class _FakeSMTP:
        def __init__(self, host: str, port: int) -> None:
            pass
        def __enter__(self) -> _FakeSMTP:
            return self
        def __exit__(self, *_: Any) -> None:
            pass
        def starttls(self) -> None:
            pass
        def login(self, user: str, pw: str) -> None:
            pass
        def send_message(self, msg: EmailMessage) -> None:
            sent.append(msg)

    return sent, _FakeSMTP  # type: ignore[return-value]


def test_send_sets_from_to_and_subject() -> None:
    sent, factory = _capture_smtp()
    sender = SmtpEmailSender(_settings(), smtp_factory=factory)
    sender.send(
        to=["alice@proton.my"],
        cc=[],
        subject="Case #42 escalated",
        body="Please review.",
        attachments=[],
    )
    assert len(sent) == 1
    assert sent[0]["From"] == "noreply@proton.my"
    assert "alice@proton.my" in sent[0]["To"]
    assert sent[0]["Subject"] == "Case #42 escalated"


def test_send_adds_cc_header() -> None:
    sent, factory = _capture_smtp()
    sender = SmtpEmailSender(_settings(), smtp_factory=factory)
    sender.send(
        to=["alice@proton.my"],
        cc=["manager@proton.my"],
        subject="s",
        body="b",
        attachments=[],
    )
    assert "manager@proton.my" in sent[0]["Cc"]


def test_send_attaches_files() -> None:
    sent, factory = _capture_smtp()
    sender = SmtpEmailSender(_settings(), smtp_factory=factory)
    sender.send(
        to=["a@b.my"],
        cc=[],
        subject="s",
        body="b",
        attachments=[("report.pdf", b"%PDF", "application/pdf")],
    )
    payloads = list(sent[0].iter_attachments())
    assert len(payloads) == 1
    assert payloads[0].get_filename() == "report.pdf"


def test_send_no_op_when_to_is_empty() -> None:
    sent, factory = _capture_smtp()
    sender = SmtpEmailSender(_settings(), smtp_factory=factory)
    sender.send(to=[], cc=[], subject="s", body="b", attachments=[])
    assert sent == []


def test_send_no_op_when_smtp_host_is_empty() -> None:
    sent, factory = _capture_smtp()
    sender = SmtpEmailSender(_settings(smtp_host=""), smtp_factory=factory)
    sender.send(to=["a@b.my"], cc=[], subject="s", body="b", attachments=[])
    assert sent == []
