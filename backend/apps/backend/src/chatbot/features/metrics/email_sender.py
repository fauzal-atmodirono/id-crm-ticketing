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


def _blocked(settings: Settings) -> set[str]:
    """Addresses this service must never send to, whatever the config says.

    A last line of defence, deliberately enforced at the transport rather than
    at each caller: a routing record, an env var, a stale automation rule and a
    hand-typed CC are four different ways to aim mail at a dead address, and
    only the transport sees all four.

    The cost of getting this wrong is not a bounce. Sustained delivery failures
    to a non-existent domain are what gets the sending Gmail account
    rate-limited or suspended, which takes every real escalation down with it.
    """
    raw = getattr(settings, "email_blocked_recipients", "") or ""
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


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
        *,
        reply_to: str | None = None,
    ) -> None:
        """Send an email synchronously. Swallows and logs all errors.

        ``reply_to`` carries the escalation correlation token (see
        escalation_notifier); omitted by default so existing callers produce
        byte-identical mail.
        """
        if not to or not self._s.smtp_host:
            return

        blocked = _blocked(self._s)
        if blocked:
            to = [a for a in to if a.strip().lower() not in blocked]
            cc = [a for a in cc if a.strip().lower() not in blocked]
            if not to:
                # Every recipient was blocked: there is no mail to send, and
                # promoting a CC into the To line would defeat the block.
                _log.warning("email_send_blocked", subject=subject)
                return

        msg = EmailMessage()
        msg["From"] = self._s.smtp_from
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        if reply_to:
            msg["Reply-To"] = reply_to
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
