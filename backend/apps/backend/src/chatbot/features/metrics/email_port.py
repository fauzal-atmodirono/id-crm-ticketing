"""EmailReportPort: send the metrics report as an email with attachments."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from chatbot.features.metrics.email_sender import Attachment, SmtpEmailSender

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


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
        self._sender = SmtpEmailSender(settings, smtp_factory=smtp_factory)

    def send_report(
        self, recipients: list[str], subject: str, body: str, attachments: list[Attachment]
    ) -> None:
        self._sender.send(to=recipients, cc=[], subject=subject, body=body, attachments=attachments)
        _log.info("metrics_report_email_sent", recipients=len(recipients))


def build_email_report_port(settings: Settings) -> EmailReportPort:
    if settings.report_enabled and settings.smtp_host:
        return SmtpEmailReport(settings)
    return MockEmailReport()
