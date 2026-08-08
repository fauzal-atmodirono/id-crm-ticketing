"""Chatwoot-side sync and notification helpers (Chatwoot-only; no external
ticketing backend).

  - EM-7 two-thread email-channel escalation notification
    (`maybe_escalate` / `_maybe_notify_escalation`).
  - Dealer-label escalation timestamping for reporting
    (`maybe_stamp_dealer_escalation`).
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


async def record_conversation_status(payload: dict) -> None:
    """Handle a Chatwoot `conversation_status_changed`/`conversation_resolved`
    event.

    No-op: see module docstring. Kept as the router's dispatch target.
    """
    return None


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

