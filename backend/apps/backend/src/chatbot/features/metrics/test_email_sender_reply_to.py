"""Reply-To header support on the shared SMTP sender (escalation reply loop)."""

from __future__ import annotations

from chatbot.features.metrics.email_sender import SmtpEmailSender


class _FakeSMTP:
    sent: list = []

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port

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


def test_send_sets_reply_to_when_given():
    _FakeSMTP.sent = []
    sender = SmtpEmailSender(_Settings(), smtp_factory=_FakeSMTP)

    sender.send(
        to=["dealer@test"],
        cc=[],
        subject="[CASE-42] hello",
        body="body",
        attachments=[],
        reply_to="support+case42@test",
    )

    assert _FakeSMTP.sent[0]["Reply-To"] == "support+case42@test"


def test_send_omits_reply_to_by_default():
    _FakeSMTP.sent = []
    sender = SmtpEmailSender(_Settings(), smtp_factory=_FakeSMTP)

    sender.send(to=["dealer@test"], cc=[], subject="hello", body="body", attachments=[])

    assert _FakeSMTP.sent[0]["Reply-To"] is None
