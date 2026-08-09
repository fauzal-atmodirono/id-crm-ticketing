"""Chatwoot-side sync and notification helpers (Chatwoot-only; no external
ticketing backend).

  - EM-7 two-thread email-channel escalation notification
    (`maybe_escalate` / `_maybe_notify_escalation`).
  - Dealer-label escalation timestamping for reporting
    (`maybe_stamp_dealer_escalation`).
  - Per-ticket follow-up REMINDER DATE validation
    (`maybe_validate_follow_up_date`) -- an agent's own note that a case
    needs attention on some future date, stored as
    `custom_attributes.follow_up_at`. Deliberately kept separate from
    `custom_attributes.sla_minutes`: that is a policy-set *duration* read by
    the backend's deadline engine; this is an agent-set *date* with no
    bearing on any SLA. See `backend/apps/backend/src/chatbot/features/
    tasks/deadline.py` for where the two are proven never to merge.
  - `upsert_contact` / `record_conversation_status`: no-op stubs kept as the
    Chatwoot router's dispatch targets for contact/status events, so the
    router doesn't need to change and a future Chatwoot-side integration has
    a place to hook in.

Every entry point here is designed to run as a FastAPI background task: it
takes an already-parsed webhook payload and never raises out to the caller
for expected "nothing to do" cases (missing fields, unknown ids) — those are
logged and skipped, not treated as errors.
"""

import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.clients.deps import get_chatwoot_client, get_proton_config_client
from app.config import get_settings
from app.services.business_hours import is_within_business_hours, next_working_instant

logger = logging.getLogger(__name__)

# Dealer labels are applied manually today (an agent picks `dealer_<slug>`
# from Chatwoot's native label picker) -- see `maybe_stamp_dealer_escalation`.
_DEALER_LABEL = re.compile(r"^dealer_(.+)$")
_DEPT_LABEL = re.compile(r"^dept_(.+)$")

_WHITESPACE_RUN = re.compile(r"\s+")

# Once-per-escalation guard for the EM-7 fan-out. See `maybe_escalate`.
_NOTIFIED_ATTR = "escalation_notified_at"

# Once-per-conversation intake stamp. See `maybe_stamp_business_hours`.
_BUSINESS_HOURS_ATTR = "received_in_business_hours"

# Operator-set follow-up reminder DATE. See `maybe_validate_follow_up_date`.
_FOLLOW_UP_ATTR = "follow_up_at"

# Bookmark of the last `follow_up_at` value this module has already validated
# and accepted. Chatwoot resends the FULL `custom_attributes` set on every
# `conversation_updated` event (see `_rearm_escalation_guard`'s docstring
# above for where this is established), so a `follow_up_at` an operator set
# last week arrives again, unchanged, on every later event for that
# conversation -- a customer reply, a label change, our own
# `dealer_escalated_at` write, anything. Without something to tell "changed
# by an operator" apart from "echoed back by Chatwoot", the first such event
# to land *after* the date comes due looks identical, on the wire, to an
# operator just having typed in a stale date -- and gets rejected, which
# destroys the reminder at exactly the moment it was supposed to fire.
#
# This stamp is that memory, following the same once-per-value pattern as
# `_NOTIFIED_ATTR` above: written back to the conversation only when a
# genuinely new value is accepted, and read straight off the SAME event's
# `custom_attributes` (Chatwoot echoes the stamp back too), so recognising
# "nothing changed" costs zero extra API calls and converges in one step.
_FOLLOW_UP_VALIDATED_ATTR = "follow_up_at_validated_value"


def _single_line(text: str, limit: int = 100) -> str:
    """Collapse *text* onto one line, for use as an email Subject header.

    The title we send to the backend ends up in `msg["Subject"]`, and
    `EmailMessage.__setitem__` raises ValueError on any value containing CR/LF
    ("values may not contain linefeed or carriage return characters"). An
    Email-channel conversation's first incoming message IS the raw email body,
    which is virtually always multi-line -- so an unsanitised title made every
    real email escalation fail to send. Collapsing (rather than cutting at the
    first newline) preserves the same leading content the subject always
    carried; only the line breaks go.
    """
    return _WHITESPACE_RUN.sub(" ", text).strip()[:limit]


async def upsert_contact(payload: dict) -> None:
    """Handle a Chatwoot `contact_created`/`contact_updated` event.

    No-op: see module docstring. Kept as the router's dispatch target.
    """
    return None


def _previous_status(payload: dict) -> str | None:
    """The status this conversation held before the change, or None.

    Chatwoot reports it under `changed_attributes`, a list of single-key dicts.
    None means we cannot tell what happened -- and without that, a reopen is
    indistinguishable from a close, so the caller skips rather than guesses.
    """
    for change in payload.get("changed_attributes") or []:
        if not isinstance(change, dict):
            continue
        status_change = change.get("status")
        if isinstance(status_change, dict):
            previous = status_change.get("previous_value")
            return str(previous) if previous is not None else None
    return None


def _as_count(value: object) -> int:
    """Stored reopen count as an int. Anything unparseable is 0.

    Chatwoot round-trips custom attributes as strings often enough that "3"
    is a real shape, and letting int() raise here would abort the whole task
    and lose the increment.
    """
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return 0


async def record_conversation_status(payload: dict) -> None:
    """Handle a Chatwoot `conversation_status_changed`/`conversation_resolved`
    event.

    Was a no-op stub, kept as the router's dispatch target "so a future
    Chatwoot-side integration has a place to hook in" (see module docstring).
    This is that integration.

    Counts REOPENS. `reopen_count` has been a warehouse column and a
    `v_reopen_rate` view since Phase 3, and nothing ever wrote it -- the mapper
    reads it from `additional_attributes`, and no code put it there, so the
    reopen rate has been a chart of zeroes that renders perfectly.

    A reopen is a **resolved -> not-resolved** transition and nothing else:
    open -> pending is an agent moving a live case around, open -> resolved is
    the case being closed. Counting either would inflate a quality metric the
    client reads.

    Fail-open like every helper here. Duplicate webhook deliveries are already
    dropped upstream by `claim_delivery`, which matters more than usual: a
    reopen rate that drifts upward because a delivery arrived twice looks
    exactly like a service getting worse.
    """
    settings = get_settings()
    if not settings.reopen_tracking_enabled:
        return

    status = str(payload.get("status") or "")
    previous = _previous_status(payload)
    if previous is None:
        logger.info(
            "record_conversation_status: no previous status on conversation %s; "
            "cannot distinguish a reopen from a close, skipping",
            payload.get("id"),
        )
        return
    if previous != "resolved" or status == "resolved":
        return

    conversation_id = payload.get("id")
    if conversation_id is None:
        return

    try:
        chatwoot = get_chatwoot_client()
        conversation = await chatwoot.get_conversation(conversation_id)
        existing = (conversation or {}).get("custom_attributes") or {}
        await chatwoot.set_custom_attributes(
            conversation_id,
            {
                "reopen_count": _as_count(existing.get("reopen_count")) + 1,
                "last_reopened_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        logger.exception(
            "record_conversation_status: failed to record reopen on conversation %s",
            conversation_id,
        )


async def _maybe_notify_escalation(conversation_id: int, labels: list[str]) -> None:
    """EM-7: ask the backend to send the escalation (customer ack + PIC/dealer
    forward) for a conversation an agent has labelled `escalate`.

    With ``escalation_all_channels_enabled`` off this is Email-only, exactly as
    before. With it on, every channel escalates and the *customer
    acknowledgement* picks its transport from the channel -- mail on an Email
    inbox, a message in the thread on WhatsApp/social/web, nothing at all on
    voice (the caller was already spoken to). The PIC and dealer legs never
    depended on the channel and are unchanged either way.

    Fail-open throughout: any missing config, unreachable service, or
    resolution failure just means no escalation fires -- never raises, matching
    every other background-task helper in this module.
    """
    settings = get_settings()
    if not settings.email_escalation_enabled:
        return

    proton = get_proton_config_client()
    if proton is None:
        return

    try:
        chatwoot = get_chatwoot_client()
        conversation = await chatwoot.get_conversation(conversation_id)
        inbox_id = (conversation or {}).get("inbox_id")
        if inbox_id is None:
            return
        inbox = await chatwoot.get_inbox(inbox_id)
    except Exception:
        logger.exception(
            "maybe_escalate: failed to resolve channel for conversation %s", conversation_id
        )
        return

    # With the flag off this is the original Email-only gate. With it on, the
    # channel is reported to the backend rather than resolved here: the backend
    # is what dispatches to a transport, so it owns that mapping (see
    # features/chat/escalation_ack.py). Duplicating the table in both services
    # would mean two places to update when a channel is added, and the two
    # deploy independently.
    channel_type = (inbox or {}).get("channel_type")
    if not settings.escalation_all_channels_enabled and channel_type != "Channel::Email":
        return

    # Once-per-escalation guard. The stamp is read off the conversation we
    # just fetched for the channel check -- no extra call and, importantly,
    # no new failure mode: if that GET had failed we would already have
    # returned above, so a "stamp read failure" can never be what suppresses
    # a genuine first escalation. The guard is checked after the channel
    # check so a conversation we are not escalating is never stamped.
    existing = (conversation or {}).get("custom_attributes") or {}
    if existing.get(_NOTIFIED_ATTR):
        logger.info(
            "maybe_escalate: conversation %s already notified for this escalation, "
            "skipping",
            conversation_id,
        )
        return

    department = next(
        (m.group(1) for lbl in labels if (m := _DEPT_LABEL.match(lbl))), None
    )
    dealer = next(
        (m.group(1) for lbl in labels if (m := _DEALER_LABEL.match(lbl))), None
    )

    title = f"Escalated conversation #{conversation_id}"
    body = f"Conversation #{conversation_id} was escalated by an agent."
    try:
        raw_messages = await chatwoot.get_messages(conversation_id)
        if isinstance(raw_messages, dict):
            message_list = raw_messages.get("payload") or []
        else:
            message_list = raw_messages or []

        first_incoming_text: str | None = None
        transcript_lines: list[str] = []
        for message in message_list:
            if message.get("private"):
                continue
            sender_name = (message.get("sender") or {}).get("name", "Customer")
            text = message.get("content") or ""
            transcript_lines.append(f"{sender_name}: {text}")

            if first_incoming_text is None and message.get("message_type") == 0:
                first_incoming_text = text

        if first_incoming_text:
            title = _single_line(first_incoming_text)
        if transcript_lines:
            body = "\n".join(transcript_lines[-10:])
    except Exception:
        logger.exception(
            "maybe_escalate: failed to build email-escalation transcript for "
            "conversation %s; falling back to generic title/body",
            conversation_id,
        )

    sent = await proton.notify_email_escalation(
        conversation_id=conversation_id,
        title=title,
        body=body,
        department=department,
        dealer=dealer,
        channel_type=channel_type,
    )
    if not sent:
        # Deliberately notify-then-stamp, and only on a confirmed send.
        #
        # The two orderings trade different failures against each other.
        # Stamping first would suppress this escalation forever if the send
        # then failed -- a customer complaint that silently reaches nobody,
        # with no error surface anywhere (every leg of this path is
        # fail-open). Stamping afterwards instead risks a duplicate mail if
        # two `conversation_updated` events for the same conversation are
        # ever processed concurrently and both read an unstamped row.
        #
        # We take the duplicate. A duplicate is visible, self-correcting and
        # already the behaviour operators know from label re-toggling; a
        # dropped escalation is neither. The race is also narrow in practice:
        # identical webhook deliveries are already dropped by `claim_delivery`,
        # so it needs two *distinct* Chatwoot events landing inside the same
        # few hundred milliseconds.
        return

    try:
        await get_chatwoot_client().set_custom_attributes(
            conversation_id, {_NOTIFIED_ATTR: datetime.now(timezone.utc).isoformat()}
        )
    except Exception:
        # The mail is already out; a failed stamp only costs a possible
        # duplicate on the next update, never a lost escalation.
        logger.exception(
            "maybe_escalate: failed to stamp %s on conversation %s",
            _NOTIFIED_ATTR,
            conversation_id,
        )


async def _rearm_escalation_guard(conversation_id: int, payload: dict) -> None:
    """Clear the once-per-escalation stamp once `escalate` is gone, so a
    genuine later re-escalation of the same case notifies again.

    Reads the stamp straight off the webhook payload (Chatwoot's conversation
    event data carries `custom_attributes`), so the overwhelmingly common
    case -- an ordinary `conversation_updated` on a conversation that was
    never escalated -- costs zero API calls. A payload that happens not to
    carry `custom_attributes` simply doesn't re-arm: the guard then behaves
    as a plain once-per-conversation one, which is the safe degradation.

    Writing None rather than deleting the key is deliberate: Chatwoot's
    custom-attributes endpoint assigns the whole object and our client merges,
    so a null is how you clear one key without a read-modify-delete race. Every
    reader here tests truthiness, and the resulting `conversation_updated`
    echo re-enters this function with a falsy stamp, so it converges in one
    step rather than looping.
    """
    attrs = payload.get("custom_attributes")
    if not isinstance(attrs, dict) or not attrs.get(_NOTIFIED_ATTR):
        return
    try:
        await get_chatwoot_client().set_custom_attributes(
            conversation_id, {_NOTIFIED_ATTR: None}
        )
    except Exception:
        logger.exception(
            "maybe_escalate: failed to clear %s on conversation %s",
            _NOTIFIED_ATTR,
            conversation_id,
        )


async def maybe_escalate(payload: dict) -> None:
    """Handle a Chatwoot `conversation_updated` event: fire the EM-7
    email-channel escalation notification when the `escalate` label is
    present. Escalation stays entirely inside Chatwoot / the agent-bot's
    handoff path -- there is no external ticketing backend to sync to.

    Edge-triggered, not level-triggered: the fan-out runs once per escalation
    and is re-armed when the `escalate` label is removed. `conversation_updated`
    fires on every label/attribute/status write, and nothing removes `escalate`
    on its own -- so without the guard the reply linker's own writes back onto
    the escalated conversation (its stamp, its label, the reopen on the
    customer branch) would re-run the whole fan-out and send the end customer
    a second `Update on your case:` email, plus a duplicate PIC and dealer
    forward, on every single reply.
    """
    conversation_id = payload.get("id")
    labels = payload.get("labels") or []
    if conversation_id is None:
        return
    if "escalate" not in labels:
        await _rearm_escalation_guard(conversation_id, payload)
        return

    await _maybe_notify_escalation(conversation_id, labels)


async def maybe_stamp_business_hours(payload: dict) -> None:
    """Handle a Chatwoot `message_created` event: on the FIRST inbound customer
    message, record whether the case arrived inside the inbox's configured
    business hours.

    Why intake is the only correct moment: an operator can edit an inbox's
    working hours at any time, so the same question asked at report time
    ("would this have been in hours under TODAY's config?") is a different
    question from the one the requirement asks ("was it in hours when it
    arrived?"). The flag is a fact about arrival, so it is written once and
    never overwritten -- the same never-overwrite discipline as
    `maybe_stamp_dealer_escalation` below.

    Dispatched from `message_created`, NOT `conversation_updated`: the latter
    fires on every subsequent label write, long after arrival.

    Writes three attributes:
      * `received_in_business_hours` (bool)
      * `received_at_local`  (ISO-8601, in the inbox's timezone)
      * `attend_after`       (ISO-8601) -- only when out of hours

    Fail-open throughout: any Chatwoot error is logged and swallowed, matching
    every other background-task helper in this module.
    """
    settings = get_settings()
    if not settings.business_hours_stamp_enabled:
        return

    conversation = payload.get("conversation") or {}
    conversation_id = conversation.get("id")
    if conversation_id is None:
        return

    # Only a real inbound customer message marks arrival. An agent's reply or a
    # private note is not the customer contacting us.
    if payload.get("message_type") != "incoming" or payload.get("private"):
        return

    try:
        chatwoot = get_chatwoot_client()
        existing = ((await chatwoot.get_conversation(conversation_id)) or {}).get(
            "custom_attributes"
        ) or {}
        # `in`, not truthiness: False is a real answer and must count as stamped.
        if _BUSINESS_HOURS_ATTR in existing:
            return

        inbox_id = conversation.get("inbox_id")
        inbox = (await chatwoot.get_inbox(inbox_id)) or {} if inbox_id is not None else {}

        created = payload.get("created_at")
        arrived_at = (
            datetime.fromtimestamp(float(created), tz=timezone.utc)
            if isinstance(created, (int, float, str))
            else datetime.now(timezone.utc)
        )

        tz_name = inbox.get("timezone") or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc

        in_hours = is_within_business_hours(inbox, arrived_at)
        attrs: dict = {
            _BUSINESS_HOURS_ATTR: in_hours,
            "received_at_local": arrived_at.astimezone(tz).isoformat(),
        }
        if not in_hours:
            attrs["attend_after"] = next_working_instant(arrived_at, inbox).isoformat()

        await chatwoot.set_custom_attributes(conversation_id, attrs)
    except Exception:
        logger.exception(
            "maybe_stamp_business_hours: failed for conversation %s",
            conversation_id,
        )


async def maybe_stamp_dealer_escalation(payload: dict) -> None:
    """Handle a Chatwoot `conversation_updated` event: stamp a
    `dealer_escalated_at` custom attribute the first time a `dealer_<slug>`
    label appears on the conversation, so the BI turnaround-time view has a
    real escalation timestamp to diff against `resolved_at`. Idempotent
    (never overwrites an existing stamp) and fail-open -- a Chatwoot API
    error here must never affect the rest of the webhook dispatch."""
    conversation_id = payload.get("id")
    labels = payload.get("labels") or []
    if conversation_id is None or not any(_DEALER_LABEL.match(lbl) for lbl in labels):
        return

    try:
        chatwoot = get_chatwoot_client()
        conversation = await chatwoot.get_conversation(conversation_id)
        existing = (conversation or {}).get("custom_attributes") or {}
        if existing.get("dealer_escalated_at"):
            return  # already stamped -- never overwrite

        await chatwoot.set_custom_attributes(
            conversation_id,
            {"dealer_escalated_at": datetime.now(timezone.utc).isoformat()},
        )
    except Exception:
        logger.exception(
            "maybe_stamp_dealer_escalation: failed for conversation %s",
            conversation_id,
        )


def _parse_follow_up_date(raw: str) -> datetime:
    """Parse an operator-supplied ISO-8601 date or date-time.

    A bare date (``2026-08-15``) is accepted and treated as midnight; a naive
    date-time is treated as UTC. Raises ``ValueError`` on anything else --
    callers turn that into an operator-facing message rather than letting it
    propagate.
    """
    parsed = datetime.fromisoformat(raw.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def _reject_follow_up_date(conversation_id: int, message: str) -> None:
    """Clear the just-rejected `follow_up_at` and tell the agent why.

    Clearing (rather than leaving the bad value in place) matters: an
    unparseable or past date left standing would silently never fire as a
    reminder, which is worse than no reminder at all because nothing else
    ever surfaces the mistake. The private note is the one channel
    guaranteed visible on the case the agent is already looking at.
    """
    try:
        chatwoot = get_chatwoot_client()
        await chatwoot.set_custom_attributes(conversation_id, {_FOLLOW_UP_ATTR: None})
        await chatwoot.create_message(conversation_id, message, private=True)
    except Exception:
        logger.exception(
            "maybe_validate_follow_up_date: failed to reject invalid follow-up "
            "date on conversation %s",
            conversation_id,
        )


async def maybe_validate_follow_up_date(payload: dict) -> None:
    """Handle a Chatwoot `conversation_updated` event: validate an
    operator-set `follow_up_at` conversation custom attribute -- a per-ticket
    reminder DATE an agent chooses, distinct from
    `custom_attributes.sla_minutes` (a policy-set *duration* the backend's
    deadline engine reads; see that module's docstring for why the two must
    never merge).

    Chatwoot writes whatever the CRM panel gives it straight through -- there
    is no server-side validation on their end -- so this IS the write
    boundary: an unparseable string or a date already in the past is
    corrected back to cleared (never left standing, see
    `_reject_follow_up_date`) rather than silently accepted or silently
    dropped.

    Validation only ever runs against an operator EDIT, never against an
    echo. Chatwoot resends the whole `custom_attributes` set on every
    `conversation_updated` (see `_FOLLOW_UP_VALIDATED_ATTR` above), so a
    `follow_up_at` that was valid when chosen and has since come due arrives
    again unchanged on the very next unrelated event -- and "the date is now
    in the past" is true of it, exactly as true as it is of a stale value an
    operator just typed in. Only the value's presence in this event tells
    those apart, and `_FOLLOW_UP_VALIDATED_ATTR` supplies that: a value that
    matches the last one this module accepted is left alone, due or not --
    that is the reminder working, not a mistake to correct. A value that
    doesn't match (first time seen, or genuinely changed) is validated fresh.

    Liberal in what "cleared" means on the wire: an empty string, an
    explicit null, or the key being absent from this event's
    `custom_attributes` altogether all count as "nothing to validate" --
    Chatwoot's custom-attribute API sends whatever the CRM panel's
    date-picker clear action gives it, and that has been observed to vary.
    A deliberate clear does not scrub `_FOLLOW_UP_VALIDATED_ATTR`: the only
    way the stale stamp could then wrongly suppress validation is if an
    operator later retypes, character for character, the exact same string
    that was previously accepted -- a narrower failure mode than the one
    this fix closes, and one a genuinely different value (even one second
    off) never hits.

    Fail-open like every helper in this module: a Chatwoot API error here is
    logged and swallowed, never raised out of the background task.
    """
    settings = get_settings()
    if not settings.follow_up_date_enabled:
        return

    conversation_id = payload.get("id")
    if conversation_id is None:
        return

    attrs = payload.get("custom_attributes")
    if not isinstance(attrs, dict) or _FOLLOW_UP_ATTR not in attrs:
        # Attribute wasn't part of this update, or the payload carries no
        # custom_attributes at all -- nothing for us to validate.
        return

    raw = attrs.get(_FOLLOW_UP_ATTR)
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return  # a deliberate clear -- valid, cancels the reminder

    raw_str = raw if isinstance(raw, str) else str(raw)

    if attrs.get(_FOLLOW_UP_VALIDATED_ATTR) == raw_str:
        # Chatwoot echoing back a value we already validated -- not a new
        # instruction from an operator. See the docstring above; this is the
        # fix for the "reminder deleted the moment it comes due" finding.
        return

    try:
        follow_up_dt = _parse_follow_up_date(raw_str)
    except ValueError:
        await _reject_follow_up_date(
            conversation_id,
            f'Follow-up date "{raw_str}" could not be understood. Use an '
            "ISO-8601 date or date-time, for example 2026-08-15 or "
            "2026-08-15T09:00:00+08:00.",
        )
        return

    now = datetime.now(timezone.utc)
    if follow_up_dt <= now:
        await _reject_follow_up_date(
            conversation_id,
            f"Follow-up date {raw_str} is in the past (now is "
            f"{now.isoformat(timespec='seconds')}). Choose a date/time after "
            "now.",
        )
        return

    # A valid, genuinely new future date -- already stored correctly by
    # Chatwoot, nothing to correct. Stamp it as validated so a later
    # `conversation_updated` that merely echoes this same value back
    # (including one delivered after the date has come due) is recognised
    # as an echo rather than re-validated and wrongly rejected.
    try:
        await get_chatwoot_client().set_custom_attributes(
            conversation_id, {_FOLLOW_UP_VALIDATED_ATTR: raw_str}
        )
    except Exception:
        logger.exception(
            "maybe_validate_follow_up_date: failed to stamp validated "
            "follow-up date on conversation %s",
            conversation_id,
        )

