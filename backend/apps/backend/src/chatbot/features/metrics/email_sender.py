"""Reusable bare SMTP email sender (Phase 2, item 13).

Splits the transport concern out of SmtpEmailReport so escalation emails
(To + CC + case summary) and scheduled metric reports can both use the same
underlying send path.
"""
from __future__ import annotations

from collections.abc import Callable
from email.message import EmailMessage
from smtplib import SMTP
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

Attachment = tuple[str, bytes, str]  # (filename, content, mimetype)


class SmtpEmailSender:
    """Sends a single email (To + CC + optional attachments) via STARTTLS SMTP.

    No-op when ``smtp_host`` is empty or ``to`` is empty — callers must never
    need to guard these conditions themselves.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        smtp_factory: Callable[[str, int], Any] | None = None,
    ) -> None:
        self._s = settings
        self._smtp = smtp_factory or SMTP

    def send(
        self,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        attachments: list[Attachment],
    ) -> None:
        """Send an email synchronously. Swallows and logs all errors."""
        if not to or not self._s.smtp_host:
            return
        msg = EmailMessage()
        msg["From"] = self._s.smtp_from
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
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
        try:
            with self._smtp(self._s.smtp_host, self._s.smtp_port) as smtp:
                smtp.starttls()
                if self._s.smtp_user:
                    smtp.login(self._s.smtp_user, self._s.smtp_password)
                smtp.send_message(msg)
            _log.info("email_sent", to_count=len(to), cc_count=len(cc), subject=subject)
        except Exception as exc:
            _log.warning("email_send_failed", subject=subject, error=str(exc))
