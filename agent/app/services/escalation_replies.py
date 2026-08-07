"""Link an emailed reply back onto the conversation it was escalated from.

Escalation mail leaves with a correlation token (`Reply-To:
…+case<id>@…` plus a `[CASE-<id>]` subject tag -- see the backend's
EscalationNotifier). The reply comes back through the tenant's ordinary
Email inbox, so Chatwoot files it as a NEW conversation with no connection
to the original. This module puts that connection back: it reads the token,
verifies the sender, copies the reply onto the original conversation, and
resolves the throwaway one.

Fail-open like every other background task here: an unparseable payload, an
unknown sender, or an unreachable backend means the reply is simply not
linked -- logged and skipped, never raised. For the sender check that
posture is also the safe one: refusing to link an unverifiable sender is
what stops someone guessing a conversation id to inject a private note.
"""

import logging
import re
from datetime import datetime, timezone

from app.clients.deps import get_chatwoot_client, get_proton_config_client
from app.config import get_settings

logger = logging.getLogger(__name__)

_REPLIED_ATTR = "dealer_replied_at"
_REPLIED_LABEL = "dealer_replied"
_ORPHAN_LABEL = "escalation_reply"

# `support+case42@host` in a To/Cc header — the primary correlation key.
_ADDRESS_TOKEN = re.compile(r"\+case(\d+)@", re.IGNORECASE)
# `[CASE-42]` in the subject — the fallback for relays that strip
# plus-addressing.
_SUBJECT_TOKEN = re.compile(r"\[CASE-(\d+)\]", re.IGNORECASE)

# Simple trail markers that are always a sign of quoted content.
_SIMPLE_TRAIL_MARKERS = (
    re.compile(r"^On .{0,200}\bwrote:\s*$", re.MULTILINE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^_{5,}\s*$", re.MULTILINE),
)


def _email_meta(message: dict) -> dict:
    attrs = message.get("content_attributes")
    if not isinstance(attrs, dict):
        return {}
    meta = attrs.get("email")
    return meta if isinstance(meta, dict) else {}


def extract_case_id(message: dict) -> int | None:
    """The escalated conversation id this message is a reply to, or None.

    Checks the To/Cc addresses first (a header the sender cannot edit by
    accident), then the subject tag.
    """
    meta = _email_meta(message)
    for key in ("to", "cc"):
        addresses = meta.get(key)
        if isinstance(addresses, str):
            addresses = [addresses]
        if not isinstance(addresses, list):
            continue
        for address in addresses:
            match = _ADDRESS_TOKEN.search(str(address))
            if match:
                return int(match.group(1))
    match = _SUBJECT_TOKEN.search(str(meta.get("subject") or ""))
    return int(match.group(1)) if match else None


def strip_quoted_trail(text: str) -> str:
    """Drop everything from the first quote marker onward.

    Keeps the reply the sender actually typed, so neither the private note
    nor the AI draft re-ingests the whole thread. If stripping would leave
    nothing (the sender top-quoted with no new text), the original is
    returned instead -- an over-eager strip that silently drops the reply is
    worse than a noisy note.
    """
    cut = len(text)

    # Check simple markers: "On ... wrote:", "-----Original Message-----", underscores
    for marker in _SIMPLE_TRAIL_MARKERS:
        match = marker.search(text)
        if match and match.start() < cut:
            cut = match.start()

    # Check "From:" only if followed by Sent:/To:/Subject: within 4 lines
    # (to avoid cutting at "From: ..." in prose paragraphs)
    for match in re.finditer(r"^From: .+$", text, re.MULTILINE):
        end_of_line = match.end()
        remaining_text = text[end_of_line:]
        # Split to get lines after this match
        lines_after = remaining_text.split('\n', 5)  # Max 5 parts
        next_4_lines = lines_after[1:5]  # Skip first part (rest of current line)

        # Check if any of the next 4 lines start with an email header
        has_email_header = any(
            line.lstrip().startswith(('Sent:', 'To:', 'Subject:'))
            for line in next_4_lines
        )
        if has_email_header and match.start() < cut:
            cut = match.start()

    # Check ">" - find trailing quoted block that extends to end of text.
    # Walk backwards from the end to avoid false positives when isolated quotes
    # appear earlier in the body.
    lines = text.split('\n')
    # Find the last non-blank line
    last_non_blank_idx = len(lines) - 1
    while last_non_blank_idx >= 0 and not lines[last_non_blank_idx].strip():
        last_non_blank_idx -= 1

    # If the last non-blank line is quoted, find the start of the trailing block
    if last_non_blank_idx >= 0 and lines[last_non_blank_idx].lstrip().startswith('>'):
        block_start_idx = last_non_blank_idx
        # Walk backwards through blank/quoted lines to find block start
        while block_start_idx > 0:
            prev_line = lines[block_start_idx - 1]
            if not prev_line.strip() or prev_line.lstrip().startswith('>'):
                block_start_idx -= 1
            else:
                break

        # Calculate text position of the block start
        cut_position = sum(len(lines[i]) for i in range(block_start_idx)) + block_start_idx
        if cut_position < cut:
            cut = cut_position

    stripped = text[:cut].strip()
    return stripped or text.strip()


async def maybe_link_escalation_reply(payload: dict) -> None:
    """Handle a Chatwoot `message_created` event: if this is an emailed
    reply carrying an escalation token, copy it onto the escalated
    conversation and close the throwaway one it arrived in.

    Two senders are trusted to link: the conversation's own contact (the
    customer replying to their own ack -- posted as a public incoming
    message, unstamped, since a customer may reply more than once) and an
    address in the escalation contact allowlist (dealer/PIC -- posted as a
    private note, stamped and labelled so a second internal reply doesn't
    pile on). These two are checked in that order: an address that happens
    to be both the conversation's contact and an allowlisted dealer/PIC is
    classified as the customer, and the allowlist is never consulted for
    it -- a sensible default (the customer's own words go in the customer
    thread), not a security gap, since the allowlist path is strictly more
    restrictive. A reply from neither -- or one that arrives while the
    allowlist itself is unreachable -- is left unlinked: this endpoint has
    no other proof the reply is real, so silently doing nothing is the only
    safe default. Every other "can't tell what this is" case (missing
    token, missing inbox, downstream HTTP failure) is likewise a skip,
    never a raise -- this runs as a background task and an exception here
    only produces an unretrieved-exception log, not a retry.
    """
    settings = get_settings()
    if not settings.escalation_reply_linking_enabled:
        return
    if payload.get("message_type") != "incoming":
        return

    case_id = extract_case_id(payload)
    if case_id is None:
        return

    sender_email = str((payload.get("sender") or {}).get("email") or "").strip().lower()
    if not sender_email:
        return

    reply_conv_id = (payload.get("conversation") or {}).get("id")
    inbox_id = (payload.get("inbox") or {}).get("id")
    if inbox_id is None:
        return

    chatwoot = get_chatwoot_client()
    try:
        inbox = await chatwoot.get_inbox(inbox_id)
        if (inbox or {}).get("channel_type") != "Channel::Email":
            return

        conversation = await chatwoot.get_conversation(case_id)
        if conversation is None:
            logger.info("escalation_replies: conversation %s not found", case_id)
            return
        existing = (conversation or {}).get("custom_attributes") or {}
        contact_email = str(
            ((conversation.get("meta") or {}).get("sender") or {}).get("email") or ""
        ).strip().lower()
        is_customer = bool(contact_email) and sender_email == contact_email

        sender_name = sender_email
        if not is_customer:
            # The stamp only gates the internal-reply path: a customer
            # replying to their own ack is a normal, repeatable event, but a
            # second dealer/PIC note past the first stamp would just be
            # duplicate noise on top of whatever a human already did with it.
            if existing.get(_REPLIED_ATTR):
                logger.info(
                    "escalation_replies: conversation %s already linked a reply, skipping",
                    case_id,
                )
                return

            proton = get_proton_config_client()
            if proton is None:
                return
            contacts = await proton.get_escalation_contacts()
            if contacts is None:
                logger.warning(
                    "escalation_replies: contact allowlist unavailable, not linking reply "
                    "from %s to conversation %s",
                    sender_email,
                    case_id,
                )
                return
            if sender_email not in contacts:
                logger.info(
                    "escalation_replies: sender %s is not an escalation contact, skipping",
                    sender_email,
                )
                return
            sender_name = contacts.get(sender_email) or sender_email

        text = strip_quoted_trail(str(payload.get("content") or ""))
        if not text:
            return

        if is_customer:
            # The customer's own words belong in the customer thread as an
            # inbound message, not as an agent note -- which also reopens
            # the conversation exactly as a real inbound message would.
            await chatwoot.create_message(
                case_id, text, private=False, message_type="incoming"
            )
        else:
            # Deliberately note-then-stamp, not the reverse: if
            # create_message fails, the exception below aborts before the
            # stamp is written, so a retry of this event can still land the
            # note. Stamping first would risk permanently losing the reply
            # if the note post failed after the stamp had already landed.
            await chatwoot.create_message(
                case_id,
                f"Reply from {sender_name} <{sender_email}>:\n\n{text}",
                private=True,
            )
            await chatwoot.set_custom_attributes(
                case_id, {_REPLIED_ATTR: datetime.now(timezone.utc).isoformat()}
            )
            await chatwoot.add_labels(case_id, [_REPLIED_LABEL])

            if settings.escalation_reply_draft_enabled:
                await _post_draft(case_id, text)

        if reply_conv_id is not None:
            await chatwoot.add_labels(reply_conv_id, [_ORPHAN_LABEL])
            await chatwoot.toggle_status(reply_conv_id, "resolved")
    except Exception:
        logger.exception(
            "escalation_replies: failed to link reply to conversation %s", case_id
        )


async def _post_draft(case_id: int, reply_text: str) -> None:
    """Post a KB-grounded customer-facing draft as a second private note.

    Best-effort: the linked note above is the deliverable, the draft is a
    convenience. A backend failure logs and returns.
    """
    proton = get_proton_config_client()
    if proton is None:
        return
    draft = await proton.suggest_reply(
        str(case_id), [f"The dealer replied: {reply_text}"]
    )
    if not draft:
        return
    await get_chatwoot_client().create_message(
        case_id,
        f"Suggested customer reply (draft — review before sending):\n\n{draft}",
        private=True,
    )
