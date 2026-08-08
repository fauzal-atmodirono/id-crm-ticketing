"""AI-suggested escalation department -- suggest-only nudge.

Escalating an Email-channel case relies on a human applying two labels in
the right order: a `dept_<slug>` department label *before* the `escalate`
label (`sync.maybe_escalate` reads whichever `dept_<slug>`/`dealer_<slug>`
labels are present at the moment `escalate` shows up). Miss the department
label, or apply it after `escalate`, and the escalation still fires but
emails nobody -- silently, with no error anywhere.

This module classifies an incoming customer message on an Email-channel
conversation with no `dept_*` label yet against the departments that
currently have a PIC configured (backend `GET /escalation/departments`) and
posts a **private note** naming the best-fit `dept_<slug>` label. It never
applies the label itself: a suggestion is a prompt for a human, and letting
an AI decision silently trigger a real escalation email to real people would
recreate the exact silent-failure this feature exists to prevent --
escalation is not reversible (it stamps itself and the mail is already
sent). Candidates always come from the PIC store, never a static list, so a
label with no PIC configured (e.g. `dept_aftersales`, `dept_cs`,
`dept_technical` on the Proton tenant today) can never be suggested.

Follows `services.categorize`'s classification shape closely: one Gemini
plain-text call, one slug from a candidate list, fail-open (`None` on any
error or an answer outside the candidates). Idempotent via a
`dept_suggested_at` custom-attribute stamp (first-write-wins, the pattern
`sync.maybe_stamp_dealer_escalation` uses), so a second inbound message on
the same conversation never posts a second note.

Flag-gated (`dept_suggestion_enabled`, default `False`) and fail-open
throughout: this runs as a FastAPI background task, so no path here may
raise -- a Gemini failure, an unreachable backend, an empty candidate list,
or a conversation that already carries a `dept_*` label all just mean "post
nothing and return".
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.ai import gemini
from app.clients.deps import get_chatwoot_client, get_proton_config_client
from app.config import get_settings

logger = logging.getLogger(__name__)

_DEPT_LABEL = re.compile(r"^dept_(.+)$")

# Once-per-conversation guard, mirroring sync.py's `_NOTIFIED_ATTR` /
# `dealer_escalated_at` stamps.
_SUGGESTED_ATTR = "dept_suggested_at"

_SYSTEM_PROMPT = (
    "You are an escalation-routing assistant. Given a customer conversation "
    "transcript and a list of allowed department slugs, reply with EXACTLY "
    "ONE slug from the list -- the department best placed to handle this "
    "case if it needed to be escalated -- no punctuation, no explanation, "
    "just the slug. If none fit, reply with the single word NONE."
)


async def classify_department(transcript: str, candidates: list[str]) -> str | None:
    """One slug from `candidates` best matching `transcript`, or None.

    Fail-open, mirroring `services.categorize.classify_category`: any
    Gemini error, or an answer that isn't exactly one of the candidates,
    yields None rather than raising.
    """
    if not candidates:
        return None
    context = (
        f"Allowed department slugs: {', '.join(candidates)}\n\n"
        f"Transcript:\n{transcript}"
    )
    try:
        answer = await gemini.generate(_SYSTEM_PROMPT, context)
    except Exception:
        logger.exception("dept_suggestion: gemini classify failed; skipping")
        return None
    slug = (answer or "").strip()
    return slug if slug in candidates else None


def _transcript_from_messages(raw: object) -> str:
    if isinstance(raw, dict):
        messages = raw.get("payload") or []
    else:
        messages = raw or []
    lines: list[str] = []
    for message in messages[-20:]:
        if message.get("private"):
            continue
        sender = (message.get("sender") or {}).get("name", "Unknown")
        lines.append(f"{sender}: {message.get('content') or ''}")
    return "\n".join(lines)


def _note_text(department: str) -> str:
    return (
        f"AI-suggested escalation department: **{department}**.\n\n"
        "This is a suggestion only -- no label has been applied. If you "
        f"agree, add the `dept_{department}` label yourself, BEFORE the "
        "`escalate` label: the escalation handler only reads whichever "
        "department/dealer labels are already on the conversation at the "
        "moment `escalate` is applied, so the wrong order sends no PIC "
        "email."
    )


async def maybe_suggest_department(payload: dict) -> None:
    """Handle a Chatwoot `message_created` event: on an incoming customer
    message to an Email-channel conversation with no `dept_*` label and no
    prior suggestion, post a private note naming a best-fit `dept_<slug>`.

    Skips (no-op, no exception) when: the flag is off; the message isn't
    incoming; the conversation/inbox id is missing; the inbox isn't
    `Channel::Email`; a `dept_*` label is already present; the suggestion
    stamp is already set; the backend has no departments with a PIC
    configured; there's no transcript to classify; or Gemini's answer isn't
    one of the candidates. Every downstream failure is logged and swallowed
    -- see module docstring.
    """
    settings = get_settings()
    if not settings.dept_suggestion_enabled:
        return
    if payload.get("message_type") != "incoming":
        return

    conversation_id = (payload.get("conversation") or {}).get("id")
    inbox_id = (payload.get("inbox") or {}).get("id")
    if conversation_id is None or inbox_id is None:
        return

    chatwoot = get_chatwoot_client()
    try:
        inbox = await chatwoot.get_inbox(inbox_id)
        if (inbox or {}).get("channel_type") != "Channel::Email":
            return

        labels_raw = await chatwoot.get_labels(conversation_id)
        labels = labels_raw.get("payload") if isinstance(labels_raw, dict) else labels_raw
        if any(_DEPT_LABEL.match(str(lbl)) for lbl in (labels or [])):
            return

        conversation = await chatwoot.get_conversation(conversation_id)
        existing = (conversation or {}).get("custom_attributes") or {}
        if existing.get(_SUGGESTED_ATTR):
            return

        proton = get_proton_config_client()
        if proton is None:
            return
        candidates = await proton.get_escalation_departments()
        if not candidates:
            return

        raw_messages = await chatwoot.get_messages(conversation_id)
        transcript = _transcript_from_messages(raw_messages)
        if not transcript:
            return

        department = await classify_department(transcript, candidates)
        if department is None:
            return

        # Note-then-stamp, not the reverse: if create_message fails, the
        # exception below aborts before the stamp is written, so a retry of
        # this event can still post the suggestion. Stamping first would
        # risk permanently losing it if the note post then failed.
        await chatwoot.create_message(conversation_id, _note_text(department), private=True)
        await chatwoot.set_custom_attributes(
            conversation_id, {_SUGGESTED_ATTR: datetime.now(timezone.utc).isoformat()}
        )
    except Exception:
        logger.exception(
            "dept_suggestion: maybe_suggest_department failed for conversation %s",
            conversation_id,
        )
