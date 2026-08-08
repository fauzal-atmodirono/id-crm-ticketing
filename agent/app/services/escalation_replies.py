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
from app.services import lifecycle, lifecycle_store

logger = logging.getLogger(__name__)

# Deliberately `escalation_replied`, NOT `dealer_replied`: three consumers
# parse the `dealer_<slug>` label namespace and would read "replied" as a
# real dealer slug -- the BigQuery mapping's `_first_tag(labels,
# _DEALER_TAG)` (backend metrics/mapping.py) would report `dealer =
# "replied"` on a PIC-only escalation, and `sync.maybe_stamp_dealer_
# escalation` would stamp `dealer_escalated_at` on a case never sent to a
# dealer. Both silently corrupt the dealer turnaround-time reporting, so the
# marker stays outside that namespace. The attribute is named to match.
_REPLIED_ATTR = "escalation_replied_at"
_REPLIED_LABEL = "escalation_replied"
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
    customer replying to their own ack -- an incoming message is attempted
    first, unstamped since a customer may reply more than once, but Chatwoot
    only allows message_type="incoming" on Api-channel inboxes, so on the
    Channel::Email inboxes this loop actually runs on it always falls back to
    a private note carrying the customer's own words, with the conversation
    reopened either way) and an address in the escalation contact allowlist
    (dealer/PIC -- posted as a private note, stamped and labelled so a
    second internal reply doesn't pile on). These two are checked in that
    order: an address that happens
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
            # inbound message, not as an agent note -- and posting one would
            # also reopen the conversation exactly as a real inbound message
            # would. But Chatwoot only accepts message_type="incoming" on
            # Api-channel inboxes; this loop only ever runs on Channel::Email
            # (checked above), so in production that post always 422s
            # ("Incoming messages are only allowed in Api inboxes"). Attempt
            # it anyway -- it is correct and would succeed if this ever runs
            # on an Api inbox -- and fall back to a private note carrying the
            # customer's own text when Chatwoot rejects it. Either way,
            # reopen the conversation directly: that's the part of "reads as
            # an inbound message" still deliverable on any inbox, and it
            # covers both the success case (belt-and-braces, in case some
            # future Chatwoot version silently drops the auto-reopen) and
            # the fallback case (a private note does not reopen anything on
            # its own).
            try:
                await chatwoot.create_message(
                    case_id, text, private=False, message_type="incoming"
                )
            except Exception:
                logger.info(
                    "escalation_replies: incoming post to conversation %s was "
                    "rejected (expected on a Channel::Email inbox -- Chatwoot "
                    "only allows message_type=incoming on Api inboxes); "
                    "falling back to a private note",
                    case_id,
                )
                await chatwoot.create_message(
                    case_id,
                    f"Customer's own reply (from {sender_email}, could not be "
                    f"posted inline -- see conversation {reply_conv_id}):\n\n{text}",
                    private=True,
                )
            await chatwoot.toggle_status(case_id, "open")
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

        # Tidy away the throwaway conversation the reply landed in -- but only
        # if it really is a throwaway. Chatwoot does not always file a reply as
        # a new conversation: its SupportMailbox looks up `In-Reply-To` against
        # existing message source ids and, when it hits, threads the reply back
        # onto the ORIGINAL conversation. `reply_conv_id` is then the customer's
        # own live case, and labelling it `escalation_reply`, ending its
        # lifecycle and resolving it would close the case the customer just
        # wrote into -- and swallow the rating survey they should get when it is
        # genuinely resolved later. Nothing needs tidying in that case anyway:
        # the reply is already on the right conversation.
        if reply_conv_id is not None and reply_conv_id != case_id:
            await chatwoot.add_labels(reply_conv_id, [_ORPHAN_LABEL])
            await _close_reply_lifecycle(reply_conv_id)
            await chatwoot.toggle_status(reply_conv_id, "resolved")
    except Exception:
        logger.exception(
            "escalation_replies: failed to link reply to conversation %s", case_id
        )


async def _close_reply_lifecycle(reply_conv_id: int) -> None:
    """End the throwaway conversation's lifecycle before it is resolved.

    `on_conversation_created` seeded this conversation ACTIVE like any other
    new Email thread. Resolving an ACTIVE conversation fires
    `conversation_resolved` -> `lifecycle.on_human_resolved`, which (with
    `LIFECYCLE_SURVEY_ENABLED`, on by default) posts the public
    agent-performance survey -- ie. emails an external dealer or PIC a request
    to rate a Proton agent 1-5. Moving the row to CLOSED first lands it in
    that handler's existing terminal-state guard, which is the same mechanism
    the bot's own survey-complete resolve already relies on; no new suppression
    logic and nothing that can affect a real customer conversation.

    Must run BEFORE `toggle_status`: the guard is read when the resulting
    webhook is handled, so a close written afterwards can lose the race.

    Fail-open on its own, not inside the caller's try: a DB blip here would
    otherwise abort the resolve and leave the throwaway conversation sitting
    open in the agent's inbox, which is a worse outcome than an unwanted
    survey. No `lifecycle_state` mirror either -- this conversation is about
    to be resolved and closed, and the write would only cost another Chatwoot
    round-trip plus another `conversation_updated` echo.
    """
    try:
        await lifecycle_store.transition(reply_conv_id, lifecycle.CLOSED)
    except Exception:
        logger.exception(
            "escalation_replies: failed to close lifecycle for reply conversation %s",
            reply_conv_id,
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
