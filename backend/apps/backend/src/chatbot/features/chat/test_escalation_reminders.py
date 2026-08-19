"""What a ladder rung actually says, and what the telephone step does
instead of sending mail."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.chat.escalation_policy import DEFAULT_STEPS, step_by_no
from chatbot.features.chat.pic_registry import PicRegistry
from chatbot.platform.config import Settings

_DEADLINE = datetime(2026, 8, 19, 19, 0, tzinfo=UTC)


def _notifier(**settings_kw: Any):
    sent: list[dict[str, Any]] = []
    notes: list[tuple[str, dict[str, Any]]] = []
    merged: list[tuple[str, dict[str, Any]]] = []

    class _Sender:
        def send(self, to, cc, subject, body, attachments, *, reply_to=None, in_reply_to=None):
            sent.append({"to": to, "cc": cc, "subject": subject, "body": body})

    async def _post(conv_id: str, payload: dict[str, Any]) -> None:
        notes.append((conv_id, payload))

    async def _merge(conv_id: str, attributes: dict[str, Any]) -> None:
        merged.append((conv_id, attributes))

    base: dict[str, Any] = {
        "smtp_host": "smtp.example.com",
        "smtp_from": "noreply@proton.my",
        "escalation_reply_to_template": "support+case{conv_id}@proton.my",
    }
    base.update(settings_kw)

    notifier = EscalationNotifier(
        settings=Settings(_env_file=None, **base),
        pic_registry=PicRegistry({}),
        email_sender=_Sender(),
        twilio_adapter=None,
        chatwoot_request=_merge,
        chatwoot_post_message=_post,
    )
    return notifier, sent, notes, merged


def test_the_first_reminder_names_itself_and_keeps_the_case_tag() -> None:
    notifier, sent, _, _ = _notifier()

    ok, _ = notifier.send_ladder_step(
        conv_id="42",
        step=step_by_no(DEFAULT_STEPS, 3),
        to=["dp@kl.my"],
        cc=["owner@kl.my"],
        title="Charger fault",
        body="summary",
        elapsed_working_hours=4.2,
    )

    assert ok
    assert sent[0]["subject"].startswith("[1ST REMINDER] [CASE-42] ")
    assert sent[0]["to"] == ["dp@kl.my"]
    assert sent[0]["cc"] == ["owner@kl.my"]
    assert "4.2 working hours" in sent[0]["body"]
    assert "status update" in sent[0]["body"]


def test_the_second_reminder_asks_for_more_than_the_first() -> None:
    """'Please action this promptly' is the right line for a first contact and
    far too soft for a Dealer Owner on a 2nd reminder."""
    notifier, sent, _, _ = _notifier()

    notifier.send_ladder_step(
        conv_id="42",
        step=step_by_no(DEFAULT_STEPS, 4),
        to=["owner@kl.my"],
        cc=[],
        title="t",
        body="b",
        elapsed_working_hours=8.0,
    )

    assert "[2ND REMINDER]" in sent[0]["subject"]
    assert "Immediate action" in sent[0]["body"]


def test_a_rung_with_no_recipients_is_refused() -> None:
    """Mail with only CC recipients reaches the wider group while the person
    being chased receives nothing."""
    notifier, sent, _, _ = _notifier()

    ok, error = notifier.send_ladder_step(
        conv_id="42",
        step=step_by_no(DEFAULT_STEPS, 3),
        to=[],
        cc=["owner@kl.my"],
        title="t",
        body="b",
        elapsed_working_hours=9.0,
    )

    assert not ok and error == "no recipients"
    assert sent == []


async def test_the_phone_step_sends_no_mail_and_raises_a_task() -> None:
    notifier, sent, notes, merged = _notifier()

    ok = await notifier.raise_phone_task(
        conv_id="42",
        step=step_by_no(DEFAULT_STEPS, 5),
        contacts=["dp@kl.my", "owner@kl.my"],
        deadline=_DEADLINE,
    )

    assert ok
    assert sent == []
    note = notes[0][1]
    assert note["private"] is True
    assert "dp@kl.my, owner@kl.my" in note["content"]
    assert "within 1 hour" in note["content"]
    assert "Daily Complaint Clause" in note["content"]
    assert merged[0][1] == {"follow_up_at": _DEADLINE.isoformat()}


async def test_a_failed_deadline_write_does_not_cost_the_agent_the_note() -> None:
    """The note is what an agent needs to make the call; the attribute is a
    convenience."""
    notifier, _, notes, _ = _notifier()

    async def _boom(conv_id: str, attributes: dict[str, Any]) -> None:
        raise RuntimeError("chatwoot down")

    notifier._cw = _boom  # noqa: SLF001 -- exercising the failure path

    assert await notifier.raise_phone_task(
        conv_id="42",
        step=step_by_no(DEFAULT_STEPS, 5),
        contacts=["dp@kl.my"],
        deadline=_DEADLINE,
    )
    assert notes


async def test_an_unwired_post_message_is_reported_not_raised() -> None:
    notifier, _, _, _ = _notifier()
    notifier._post_message = None  # noqa: SLF001

    assert not await notifier.raise_phone_task(
        conv_id="42",
        step=step_by_no(DEFAULT_STEPS, 5),
        contacts=["dp@kl.my"],
        deadline=_DEADLINE,
    )
