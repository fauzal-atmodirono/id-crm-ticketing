"""EmailReportPort: send the metrics report as an email with attachments."""

from __future__ import annotations

from collections.abc import Callable
from email.message import EmailMessage
from smtplib import SMTP
from typing import TYPE_CHECKING, Any, Protocol

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

Attachment = tuple[str, bytes, str]  # (filename, content, mimetype)


class EmailReportPort(Protocol):
    def send_report(
        self, recipients: list[str], subject: str, body: str, attachments: list[Attachment]
    ) -> None: ...


class MockEmailReport:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_report(
        self, recipients: list[str], subject: str, body: str, attachments: list[Attachment]
    ) -> None:
        self.sent.append(
            {"recipients": recipients, "subject": subject, "body": body, "attachments": attachments}
        )


class SmtpEmailReport:
    def __init__(
        self, settings: Settings, *, smtp_factory: Callable[[str, int], Any] | None = None
    ) -> None:
        self._s = settings
        self._smtp = smtp_factory or SMTP

    def send_report(
        self, recipients: list[str], subject: str, body: str, attachments: list[Attachment]
    ) -> None:
        if not recipients:
            return
        msg = EmailMessage()
        msg["From"] = self._s.smtp_from
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.set_content(body)
        for filename, content, mimetype in attachments:
            maintype, _, subtype = mimetype.partition("/")
            msg.add_attachment(
                content,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=filename,
            )
        with self._smtp(self._s.smtp_host, self._s.smtp_port) as smtp:
            smtp.starttls()
            if self._s.smtp_user:
                smtp.login(self._s.smtp_user, self._s.smtp_password)
            smtp.send_message(msg)
        _log.info("metrics_report_email_sent", recipients=len(recipients))


def build_email_report_port(settings: Settings) -> EmailReportPort:
    if settings.report_enabled and settings.smtp_host:
        return SmtpEmailReport(settings)
    return MockEmailReport()
