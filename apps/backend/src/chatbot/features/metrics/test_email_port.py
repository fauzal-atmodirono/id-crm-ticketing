from chatbot.features.metrics.email_port import MockEmailReport, SmtpEmailReport
from chatbot.platform.config import Settings


def test_mock_records_send() -> None:
    m = MockEmailReport()
    m.send_report(["a@x.com"], "subj", "body", [("f.xlsx", b"PK", "application/octet-stream")])
    assert m.sent[0]["recipients"] == ["a@x.com"]
    assert m.sent[0]["attachments"][0][0] == "f.xlsx"


def test_smtp_builds_and_sends_message() -> None:
    captured: dict[str, object] = {}

    class _SMTP:
        def __init__(self, host: str, port: int) -> None:
            captured["host"] = host

        def __enter__(self) -> "_SMTP":
            return self

        def __exit__(self, *a: object) -> None: ...
        def starttls(self) -> None:
            captured["tls"] = True

        def login(self, u: str, p: str) -> None:
            captured["user"] = u

        def send_message(self, msg: object) -> None:
            captured["msg"] = msg

    s = Settings(smtp_host="smtp.test", smtp_user="u", smtp_password="p", smtp_from="from@x.com")
    SmtpEmailReport(s, smtp_factory=_SMTP).send_report(
        ["a@x.com"], "subj", "body", [("f.xlsx", b"PK", "application/octet-stream")]
    )
    assert captured["host"] == "smtp.test"
    assert captured["user"] == "u"
    msg = captured["msg"]
    assert msg["To"] == "a@x.com" and msg["Subject"] == "subj"  # type: ignore[index]
