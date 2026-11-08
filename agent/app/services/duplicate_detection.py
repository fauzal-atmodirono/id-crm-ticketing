"""Duplicate / related case detection with a merge suggestion (FIX-46, RFP 4.25).

The RFP asks the AI to "identify duplicate or related cases and automatically
consolidate them", linking on mobile number, email, vehicle registration or
chassis number, notifying the assigned PIC/agent, and avoiding duplicate
follow-up by multiple teams.

**Why this suggests and links rather than merges.** Chatwoot has no
conversation merge, and its contact merge is destructive (the losing contact
is deleted, its attributes overwritten) and cannot be undone. A false match
-- two family members sharing a phone, a dealer's number on several contacts,
a plate typed onto the wrong contact -- would silently fold one customer's
case history into another's. So consolidation here is a *link*: the new case
gets a private note naming the related open case(s) and why they matched, a
`possible_duplicate` label (so supervisors can filter for them) and a
`related_case_ids` custom attribute; each existing open case gets a private
note that @mentions its assignee (or team), which is what raises a Chatwoot
in-app notification. A human decides whether to resolve one in favour of the
other. Nothing is ever sent to the customer.

**Matching is deterministic first.** A new case's signals are read from the
webhook payload: the Chatwoot contact id, its phone number and email, its
`vehicle_no` contact attribute (the Cases list "Car Plate" column) or the
case's `vehicle_plate` field, and the case's `vehicle_chassis` field (or a
contact attribute of the same name). Candidates come from:

  - the same contact's other conversations;
  - contacts found by `/contacts/search` whose phone (digits-only, country-code
    tolerant, the backend Customer 360 rule) or email (case-insensitive) is
    exactly the same;
  - contacts whose `vehicle_no` normalises to the same plate (one
    `/contacts/filter` call, the backend's plate variants), and conversations
    whose `vehicle_plate` matches (`/conversations/filter`);
  - conversations whose `vehicle_chassis` matches.

Only cases that are open, pending or snoozed count -- a resolved case is
history, not a duplicate. Plates and chassis numbers are compared with the
same normalisation `backend` case_fields applies at entry (upper-case
alphanumerics only), duplicated here rather than shared, per the
agent/backend decoupling.

**Optional AI judge** (`duplicate_detection_ai_judge_enabled`, default off):
a match found only through the customer's identity may be a different issue
(a launch question vs an aircond complaint). The judge asks Gemini "same
issue?" over both cases' public messages and drops a NO. A vehicle match is
always kept (same car is related by definition), as is any match when either
side has no text yet or Gemini fails -- the AI can only remove a suggestion
on evidence, never add one.

**When it runs.** `conversation_created` (all signals), plus two late-signal
paths that keep only vehicle matches (identity matches were already checked
at creation; re-linking them would re-notify the same owners):

  - `conversation_updated` once the case carries a `vehicle_plate`/
    `vehicle_chassis` field -- an agent fills those in after the case exists;
  - `contact_updated` once the contact carries `vehicle_no`/`vehicle_chassis`,
    run for that contact's open, not-yet-flagged cases.

Both fire on unrelated edits (status, assignment, labels, our own attribute
writes), so each takes a cheap claim on (conversation-or-contact, plate,
chassis) BEFORE any API call: a negative result is remembered, an unchanged
signal set costs nothing, a changed plate/chassis is searched once.

**Idempotency.** A claim row in `processed_deliveries`, keyed on the
conversation and a fingerprint of all its signals, makes a redelivered or
repeated event a no-op atomically (same insert-or-skip as webhook dedupe).
The `related_case_ids` attribute is the durable marker: a conversation that
already carries it is never noted twice, whichever event got there first.
It is written FIRST, before the note and label, because the label write
makes Chatwoot emit conversation_updated and that payload must already
carry the marker or it would re-enter and notify twice. If the marker or
the new-case note fails, the marker is rolled back and the claim released,
so a later event retries instead of the fingerprint being burned. Only the
marker plus `duplicate_note_posted` (written after the note) counts as
flagged: a stranded marker from a lost note is retried, and a marker whose
note did land (only the flag write lost) is completed without a second note.
The late-signal paths' outer claims are likewise released when `_detect`
returns FAILED (including a failed lookup that found nothing) or raises.

Only a `User` assignee is @mentioned (an AgentBot-held case falls back to its
team). An assignee who is not a member of the new case's inbox is logged:
they get the notification but cannot open the new case's link.

Flag-gated (`duplicate_detection_enabled`, default off), inbox allow-listed
(`duplicate_detection_inbox_ids`, empty = all; a linked case in an inbox
outside the list is ignored too), and fail-open throughout: a background task
must never raise.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from app.ai import gemini
from app.clients.deps import get_chatwoot_client
from app.config import get_settings
from app.services.dedupe import claim_delivery, release_claim
from app.services.lifecycle import _is_escalation_reply

logger = logging.getLogger(__name__)

LABEL = "possible_duplicate"
RELATED_ATTR = "related_case_ids"
# Written AFTER the new-case note: marker + this = fully flagged. A marker
# without it is a half state (the note never landed) that must not block a
# retry.
NOTE_ATTR = "duplicate_note_posted"
# Opening words of the new-case note, to recognise a note that did land when
# only its NOTE_ATTR write was lost.
_NOTE_PREFIX = "**Possible duplicate case.**"
_OPEN_STATUSES = {"open", "pending", "snoozed"}
_CLAIM_SOURCE = "duplicate_detection"

# Bounds on fan-out: a shared dealer number or a fuzzy plate must not turn
# one new case into dozens of API calls or a wall of notes.
_MAX_CONTACTS_PER_SIGNAL = 5
_MAX_LINKED_CASES = 5
_MIN_PHONE_SUFFIX_DIGITS = 8

_PLATE_STRIP = re.compile(r"[^0-9A-Z]")

_JUDGE_PROMPT = (
    "You compare two customer-service cases from the same customer. Reply "
    "with exactly YES if the new case is about the same issue as the existing "
    "case (a follow-up, a chase, or the same problem reported again), or "
    "exactly NO if it is a different issue. No other words."
)

_REASON_CONTACT = "same customer"
_REASON_PHONE = "same mobile number"
_REASON_EMAIL = "same email"
_REASON_PLATE = "same vehicle registration"
_REASON_CHASSIS = "same chassis number"
_VEHICLE_REASONS = (_REASON_PLATE, _REASON_CHASSIS)


def _key(value: Any) -> str:
    """Plate/chassis normalisation, as backend case_fields._upper_alnum."""
    return _PLATE_STRIP.sub("", str(value or "").upper())


def _plate_variants(compact: str) -> list[str]:
    """Spellings to OR in Chatwoot's `contains` filter (which can't ignore
    whitespace): BMT5518, BMT 5518, BMT-5518 -- as backend Customer 360."""
    spaced = re.sub(r"(?<=[A-Z])(?=\d)|(?<=\d)(?=[A-Z])", " ", compact)
    return list(dict.fromkeys(v for v in (compact, spaced, spaced.replace(" ", "-")) if v))


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _phones_match(a: Any, b: Any) -> bool:
    """Digits-only, tolerant of a country-code/trunk-prefix difference
    (0123456789 vs +60123456789), at least 8 shared digits."""
    da, db = _digits(a), _digits(b)
    if not da or not db:
        return False
    short, long_ = sorted((da, db), key=len)
    short = short.lstrip("0")
    return len(short) >= _MIN_PHONE_SUFFIX_DIGITS and long_.endswith(short)


def _allowed_inboxes() -> set[int] | None:
    raw = get_settings().duplicate_detection_inbox_ids or ""
    ids = {int(p) for p in (s.strip() for s in raw.split(",")) if p.isdigit()}
    return ids or None


def _inbox_allowed(inbox_id: Any, allowed: set[int] | None) -> bool:
    if allowed is None:
        return True
    try:
        return int(inbox_id) in allowed
    except (TypeError, ValueError):
        return False


@dataclass
class _Signals:
    contact_id: int | None
    phone: str | None
    email: str | None
    plate: str
    chassis: str

    def fingerprint(self) -> str:
        raw = "|".join(
            [str(self.contact_id or ""), _digits(self.phone), (self.email or "").lower(),
             self.plate, self.chassis]
        )
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def empty(self) -> bool:
        return not (self.contact_id or self.phone or self.email or self.plate or self.chassis)


@dataclass
class _Match:
    conversation: dict[str, Any]
    reasons: list[str] = field(default_factory=list)

    def add(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)

    @property
    def identity_only(self) -> bool:
        return not any(r.startswith(_VEHICLE_REASONS) for r in self.reasons)


def _signals(payload: dict) -> _Signals:
    sender = ((payload.get("meta") or {}).get("sender")) or {}
    contact_attrs = sender.get("custom_attributes") or {}
    conv_attrs = payload.get("custom_attributes") or {}
    contact_id = sender.get("id")
    if contact_id is None:
        contact_id = (payload.get("contact_inbox") or {}).get("contact_id")
    try:
        contact_id = int(contact_id) if contact_id is not None else None
    except (TypeError, ValueError):
        contact_id = None
    return _Signals(
        contact_id=contact_id,
        phone=(sender.get("phone_number") or "").strip() or None,
        email=(sender.get("email") or "").strip() or None,
        plate=_key(conv_attrs.get("vehicle_plate") or contact_attrs.get("vehicle_no")),
        chassis=_key(conv_attrs.get("vehicle_chassis") or contact_attrs.get("vehicle_chassis")),
    )


async def _safe(
    coro, what: str, conversation_id: Any, errors: list[str]
) -> list[dict[str, Any]]:
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 -- fail-open per lookup
        errors.append(what)
        logger.warning(
            "duplicate_detection: %s failed for conversation %s: %s", what, conversation_id, exc
        )
        return []


async def _find_matches(
    signals: _Signals, conversation_id: int, allowed: set[int] | None
) -> tuple[list[_Match], bool]:
    """(matches, whether any lookup failed). A failed lookup with no match is
    not a reliable negative -- the caller must not remember it as one."""
    chatwoot = get_chatwoot_client()
    matches: dict[int, _Match] = {}
    errors: list[str] = []

    def consider(conv: dict[str, Any], reason: str) -> None:
        conv_id = conv.get("id")
        if conv_id is None or conv_id == conversation_id:
            return
        if conv.get("status") not in _OPEN_STATUSES:
            return
        if not _inbox_allowed(conv.get("inbox_id"), allowed):
            return
        matches.setdefault(int(conv_id), _Match(conv)).add(reason)

    # contact id -> reasons, so one contact's conversations are fetched once
    contacts: dict[int, list[str]] = {}

    def add_contact(contact_id: Any, reason: str) -> None:
        if contact_id is None:
            return
        reasons = contacts.setdefault(int(contact_id), [])
        if reason not in reasons:
            reasons.append(reason)

    if signals.contact_id is not None:
        add_contact(signals.contact_id, _REASON_CONTACT)

    if signals.phone:
        found = await _safe(
            chatwoot.search_contacts(signals.phone), "phone search", conversation_id, errors
        )
        hits = [
            c for c in found
            if c.get("id") != signals.contact_id
            and _phones_match(signals.phone, c.get("phone_number"))
        ]
        for c in hits[:_MAX_CONTACTS_PER_SIGNAL]:
            add_contact(c.get("id"), _REASON_PHONE)

    if signals.email:
        found = await _safe(
            chatwoot.search_contacts(signals.email), "email search", conversation_id, errors
        )
        wanted = signals.email.lower()
        hits = [
            c for c in found
            if c.get("id") != signals.contact_id
            and (c.get("email") or "").strip().lower() == wanted
        ]
        for c in hits[:_MAX_CONTACTS_PER_SIGNAL]:
            add_contact(c.get("id"), _REASON_EMAIL)

    if signals.plate:
        variants = _plate_variants(signals.plate)
        rows = [
            {
                "attribute_key": "vehicle_no",
                "filter_operator": "contains",
                "values": [v],
                "query_operator": "OR" if i < len(variants) - 1 else None,
            }
            for i, v in enumerate(variants)
        ]
        found = await _safe(
            chatwoot.filter_contacts(rows), "plate contact filter", conversation_id, errors
        )
        # exact plate only: `contains` also returns BMT55189 for BMT5518
        hits = [
            c for c in found
            if c.get("id") != signals.contact_id
            and _key((c.get("custom_attributes") or {}).get("vehicle_no")) == signals.plate
        ]
        for c in hits[:_MAX_CONTACTS_PER_SIGNAL]:
            add_contact(c.get("id"), f"{_REASON_PLATE} {signals.plate}")

    for contact_id, reasons in contacts.items():
        convs = await _safe(
            chatwoot.list_contact_conversations(contact_id),
            "contact conversations",
            conversation_id,
            errors,
        )
        for conv in convs:
            for reason in reasons:
                consider(conv, reason)

    for attr, value, reason in (
        ("vehicle_plate", signals.plate, f"{_REASON_PLATE} {signals.plate}"),
        ("vehicle_chassis", signals.chassis, f"{_REASON_CHASSIS} {signals.chassis}"),
    ):
        if not value:
            continue
        rows = [
            {"attribute_key": attr, "filter_operator": "equal_to", "values": [value],
             "query_operator": None}
        ]
        found = await _safe(
            chatwoot.filter_conversations(rows), f"{attr} filter", conversation_id, errors
        )
        for conv in found:
            if _key((conv.get("custom_attributes") or {}).get(attr)) == value:
                consider(conv, reason)

    return list(matches.values()), bool(errors)


def _transcript(raw: Any, limit: int = 10) -> str:
    messages = raw.get("payload") if isinstance(raw, dict) else raw
    lines = [
        str(m.get("content") or "").strip()
        for m in (messages or [])
        if isinstance(m, dict) and not m.get("private") and m.get("content")
    ]
    return "\n".join(lines[-limit:])


async def _judge(matches: list[_Match], conversation_id: int) -> list[_Match]:
    """Drop identity-only matches Gemini says are a different issue."""
    if not get_settings().duplicate_detection_ai_judge_enabled:
        return matches
    if not any(m.identity_only for m in matches):
        return matches
    chatwoot = get_chatwoot_client()
    try:
        new_text = _transcript(await chatwoot.get_messages(conversation_id))
    except Exception:  # noqa: BLE001
        return matches
    if not new_text:
        return matches  # nothing to judge on yet -- keep the deterministic match
    kept: list[_Match] = []
    for match in matches:
        if not match.identity_only:
            kept.append(match)
            continue
        try:
            old_text = _transcript(await chatwoot.get_messages(int(match.conversation["id"])))
            if not old_text:
                kept.append(match)
                continue
            answer = await gemini.generate(
                _JUDGE_PROMPT, f"Existing case:\n{old_text}\n\nNew case:\n{new_text}"
            )
        except Exception:  # noqa: BLE001 -- an AI failure never removes a match
            logger.warning(
                "duplicate_detection: AI judge failed; keeping case %s",
                match.conversation.get("id"),
            )
            kept.append(match)
            continue
        if (answer or "").strip().upper().startswith("NO"):
            logger.info(
                "duplicate_detection: AI judge says case %s is a different issue",
                match.conversation.get("id"),
            )
            continue
        kept.append(match)
    return kept


def _link(conversation_id: Any) -> str:
    s = get_settings()
    return (
        f"{s.chatwoot_display_url}/app/accounts/{s.chatwoot_account_id}"
        f"/conversations/{conversation_id}"
    )


def _owner(conv: dict[str, Any]) -> tuple[str, str | None]:
    """(who holds the case, in words; Chatwoot mention markup or None).

    `[@Name](mention://user/<id>/<name>)` in a private note is the markup the
    Chatwoot editor writes; its MentionService turns it into the assignee's
    in-app notification. A team mention notifies every team member.
    """
    meta = conv.get("meta") or {}
    assignee = meta.get("assignee") or {}
    team = meta.get("team") or {}
    # Chatwoot's assignee is polymorphic on newer versions (an agent bot can
    # hold a case). Only a User can be @mentioned; absent type = a User, as on
    # versions that predate the field.
    assignee_type = meta.get("assignee_type") or conv.get("assignee_type") or "User"
    if assignee.get("id") is not None and assignee_type == "User":
        name = assignee.get("available_name") or assignee.get("name") or f"agent {assignee['id']}"
        mention = f"[@{name}](mention://user/{assignee['id']}/{quote(name, safe='')})"
        return f"assigned to {name}", mention
    if team.get("id") is not None:
        name = team.get("name") or f"team {team['id']}"
        mention = f"[@{name}](mention://team/{team['id']}/{quote(name, safe='')})"
        return f"with team {name}", mention
    return "unassigned", None


def _new_case_note(matches: list[_Match]) -> str:
    lines = [
        "**Possible duplicate case.** This customer already has open case(s) "
        "that look related:"
    ]
    for m in matches:
        conv = m.conversation
        who, _ = _owner(conv)
        lines.append(
            f"- #{conv['id']} ({conv.get('status')}, {who}) -- "
            f"{'; '.join(m.reasons)} -- {_link(conv['id'])}"
        )
    lines.append(
        "\nCheck with the owner before replying so the same issue isn't followed up "
        "by two teams. Nothing was merged and the customer was not messaged; if it is "
        "the same issue, continue in one case and resolve the other."
    )
    return "\n".join(lines)


def _existing_case_note(match: _Match, new_id: int, mention: str | None) -> str:
    prefix = f"{mention} " if mention else ""
    return (
        f"{prefix}**Possible duplicate:** new case #{new_id} ({_link(new_id)}) was opened "
        f"for the same customer ({'; '.join(match.reasons)}). Please review it and agree "
        "with whoever picks it up so the issue is followed up once. Nothing was merged."
    )


async def _warn_if_not_inbox_member(
    match: _Match, inbox_id: Any, members: dict[str, set[int] | None]
) -> None:
    """Log (only) when the mentioned assignee can't open the new case: they
    are not a member of its inbox, so the link in the note won't work for
    them and someone else has to coordinate. Members are fetched once."""
    assignee = (match.conversation.get("meta") or {}).get("assignee") or {}
    if assignee.get("id") is None or inbox_id is None:
        return
    if "ids" not in members:
        try:
            rows = await get_chatwoot_client().list_inbox_members(int(inbox_id))
            members["ids"] = {int(a["id"]) for a in rows if a.get("id") is not None}
        except Exception:  # noqa: BLE001
            members["ids"] = None
    ids = members["ids"]
    if ids is not None and int(assignee["id"]) not in ids:
        logger.info(
            "duplicate_detection: assignee %s of case %s is not a member of inbox %s",
            assignee["id"], match.conversation.get("id"), inbox_id,
        )


# `_detect` outcomes. The late-signal paths' outer claims keep a real answer
# (POSTED, NO_MATCH, SKIPPED) and are released on FAILED, so a later event
# with the same signals retries instead of being remembered as a negative.
POSTED, NO_MATCH, SKIPPED, FAILED = "posted", "no_match", "skipped", "failed"


def _fully_flagged(attrs: dict[str, Any]) -> bool:
    """Marker AND note flag. A marker alone is the half state left when the
    note never landed; treating it as flagged would block that case forever."""
    return bool(attrs.get(RELATED_ATTR)) and bool(attrs.get(NOTE_ATTR))


def _activity(conv: dict[str, Any]) -> float:
    for key in ("last_activity_at", "timestamp", "created_at"):
        try:
            if conv.get(key) is not None:
                return float(conv[key])
        except (TypeError, ValueError):
            continue
    return 0.0


async def _note_already_posted(conversation_id: int) -> bool:
    """For a marker without NOTE_ATTR: did the note land anyway, with only
    the NOTE_ATTR write lost? Unknown counts as no, so the case is retried: a
    second note is visible and harmless; a note that never landed is the
    silent failure this guards against."""
    try:
        raw = await get_chatwoot_client().get_messages(conversation_id)
    except Exception:  # noqa: BLE001
        return False
    messages = raw.get("payload") if isinstance(raw, dict) else raw
    return any(
        isinstance(m, dict)
        and m.get("private")
        and str(m.get("content") or "").startswith(_NOTE_PREFIX)
        for m in (messages or [])
    )


async def _detect(payload: dict, signals: _Signals, *, vehicle_only: bool = False) -> str:
    """Find, then link. Returns POSTED, NO_MATCH, SKIPPED or FAILED.

    `vehicle_only` (the update and contact paths) keeps only plate/chassis
    matches. Identity matches were already checked when the case was created,
    and linking them again on every vehicle edit would re-notify the same
    owners. A lookup that failed and found nothing is FAILED, not NO_MATCH:
    it is not a reliable negative to remember."""
    conversation_id = int(payload["id"])
    allowed = _allowed_inboxes()
    matches, lookup_failed = await _find_matches(signals, conversation_id, allowed)
    if vehicle_only:
        matches = [m for m in matches if not m.identity_only]
    if not matches:
        return FAILED if lookup_failed else NO_MATCH
    matches = await _judge(matches, conversation_id)
    if not matches:
        return NO_MATCH
    matches = sorted(matches, key=lambda m: int(m.conversation["id"]))[:_MAX_LINKED_CASES]

    claim_key = f"{_CLAIM_SOURCE}:{conversation_id}:{signals.fingerprint()}"
    if not await claim_delivery(claim_key, _CLAIM_SOURCE):
        return SKIPPED

    chatwoot = get_chatwoot_client()
    ids = [str(m.conversation["id"]) for m in matches]
    marker_written = False
    try:
        # Durable marker check, after the claim: another event (created,
        # updated or contact) or an earlier run may already have flagged it.
        current = await chatwoot.get_conversation(conversation_id)
        attrs = (current or {}).get("custom_attributes") or {}
        if _fully_flagged(attrs):
            return SKIPPED
        if attrs.get(RELATED_ATTR) and await _note_already_posted(conversation_id):
            # Only the NOTE_ATTR write was lost: finish the flag and post no
            # second note.
            await chatwoot.set_custom_attributes(conversation_id, {NOTE_ATTR: "true"})
            return SKIPPED
        # The marker goes FIRST, before the note, the label or any other write
        # that makes Chatwoot emit conversation_updated. That event's payload
        # must already carry related_case_ids, or it re-enters this module.
        # marker_written is set BEFORE the call: if the request times out
        # after Chatwoot has committed the write, the marker still exists and
        # still has to be rolled back.
        marker_written = True
        await chatwoot.set_custom_attributes(conversation_id, {RELATED_ATTR: ",".join(ids)})
        await chatwoot.create_message(conversation_id, _new_case_note(matches), private=True)
    except Exception:  # noqa: BLE001
        # The new-case note is the point of this feature. If it or the marker
        # failed, undo the marker and release the claim so that a later event
        # can retry, instead of burning the fingerprint with nothing posted.
        logger.exception(
            "duplicate_detection: could not flag case %s; releasing claim", conversation_id
        )
        if marker_written:
            try:
                await chatwoot.set_custom_attributes(conversation_id, {RELATED_ATTR: ""})
            except Exception:  # noqa: BLE001
                logger.exception(
                    "duplicate_detection: marker rollback failed on %s", conversation_id
                )
        await release_claim(claim_key)
        return FAILED

    try:
        await chatwoot.set_custom_attributes(conversation_id, {NOTE_ATTR: "true"})
    except Exception:  # noqa: BLE001
        # The note did land. A later event sees the marker without the flag,
        # finds the note through _note_already_posted, and finishes the flag.
        logger.exception("duplicate_detection: note flag write failed on %s", conversation_id)

    try:
        await chatwoot.add_labels(conversation_id, [LABEL])
    except Exception:  # noqa: BLE001
        logger.exception("duplicate_detection: label write failed on %s", conversation_id)

    members: dict[str, set[int] | None] = {}
    for match in matches:
        _, mention = _owner(match.conversation)
        if mention and "mention://user/" in mention:
            await _warn_if_not_inbox_member(match, payload.get("inbox_id"), members)
        try:
            await chatwoot.create_message(
                int(match.conversation["id"]),
                _existing_case_note(match, conversation_id, mention),
                private=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "duplicate_detection: notify failed on case %s", match.conversation.get("id")
            )
    logger.info("duplicate_detection: case %s linked to %s", conversation_id, ",".join(ids))
    return POSTED


def _eligible(payload: dict) -> bool:
    if not get_settings().duplicate_detection_enabled:
        return False
    if payload.get("id") is None:
        return False
    if not _inbox_allowed(payload.get("inbox_id"), _allowed_inboxes()):
        return False
    # A dealer or PIC emailing back about an escalation is filed as a new
    # conversation. It belongs to the existing case and is not a duplicate.
    if _is_escalation_reply(payload):
        return False
    return True


async def maybe_detect_duplicates(payload: dict) -> None:
    """`conversation_created` background task. Never raises."""
    try:
        if not _eligible(payload):
            return
        signals = _signals(payload)
        if signals.empty():
            return
        await _detect(payload, signals)
    except Exception:  # noqa: BLE001
        logger.exception("duplicate_detection: failed for conversation %s", payload.get("id"))


async def maybe_detect_duplicates_on_update(payload: dict) -> None:
    """`conversation_updated` background task. Re-checks only once a vehicle
    plate or chassis case field is present and the case is not yet fully
    flagged.

    conversation_updated fires on every status, assignment and label change.
    A cheap claim on (conversation, plate, chassis) is taken BEFORE any
    search, so an answer is remembered: further updates with unchanged
    vehicle signals make no API call. A changed plate or chassis is a new
    key and is searched once. The claim is released when the run FAILED or
    raised, so a later update retries. Never raises."""
    try:
        attrs = payload.get("custom_attributes") or {}
        if _fully_flagged(attrs):
            return
        if not (_key(attrs.get("vehicle_plate")) or _key(attrs.get("vehicle_chassis"))):
            return
        if not _eligible(payload):
            return
        if payload.get("status") not in _OPEN_STATUSES:
            return
        signals = _signals(payload)
        update_key = f"{_CLAIM_SOURCE}:update:{payload['id']}:{signals.plate}:{signals.chassis}"
        if not await claim_delivery(update_key, _CLAIM_SOURCE):
            return
        status = FAILED
        try:
            status = await _detect(payload, signals, vehicle_only=True)
        finally:
            if status == FAILED:
                await release_claim(update_key)
    except Exception:  # noqa: BLE001
        logger.exception("duplicate_detection: update check failed for %s", payload.get("id"))


async def maybe_detect_duplicates_on_contact_update(payload: dict) -> None:
    """`contact_updated` background task. A `vehicle_no` (or
    `vehicle_chassis`) typed onto the contact after its case was created is
    a new signal for that contact's open cases that are not yet flagged.

    contact_updated fires on any contact edit, including this codebase's own
    attribute writes. So the same cheap claim as the update path, keyed on
    (contact, plate, chassis), is taken before any API call, and it is
    released if any case's run FAILED or the lookup raised. Cases are taken
    most recently active first and capped. Vehicle matches only (see
    `_detect`). Never raises."""
    try:
        if not get_settings().duplicate_detection_enabled:
            return
        contact_id = payload.get("id")
        attrs = payload.get("custom_attributes") or {}
        plate, chassis = _key(attrs.get("vehicle_no")), _key(attrs.get("vehicle_chassis"))
        if contact_id is None or not (plate or chassis):
            return
        contact_key = f"{_CLAIM_SOURCE}:contact:{contact_id}:{plate}:{chassis}"
        if not await claim_delivery(contact_key, _CLAIM_SOURCE):
            return
        failed = True
        try:
            allowed = _allowed_inboxes()
            convs = await get_chatwoot_client().list_contact_conversations(int(contact_id))
            sender = {
                "id": contact_id,
                "name": payload.get("name"),
                "email": payload.get("email"),
                "phone_number": payload.get("phone_number"),
                "custom_attributes": attrs,
            }
            candidates = [
                c for c in convs
                if c.get("id") is not None
                and c.get("status") in _OPEN_STATUSES
                and _inbox_allowed(c.get("inbox_id"), allowed)
                and not _fully_flagged(c.get("custom_attributes") or {})
            ]
            candidates.sort(key=_activity, reverse=True)
            if len(candidates) > _MAX_LINKED_CASES:
                logger.info(
                    "duplicate_detection: contact %s has %s open cases; checking the %s most "
                    "recently active",
                    contact_id, len(candidates), _MAX_LINKED_CASES,
                )
            any_failed = False
            for conv in candidates[:_MAX_LINKED_CASES]:
                conv_payload = {
                    "id": conv["id"],
                    "inbox_id": conv.get("inbox_id"),
                    "status": conv.get("status"),
                    "custom_attributes": conv.get("custom_attributes") or {},
                    "additional_attributes": conv.get("additional_attributes") or {},
                    "meta": {"sender": sender},
                }
                if _is_escalation_reply(conv_payload):
                    continue
                result = await _detect(conv_payload, _signals(conv_payload), vehicle_only=True)
                if result == FAILED:
                    any_failed = True
            failed = any_failed
        finally:
            if failed:
                await release_claim(contact_key)
    except Exception:  # noqa: BLE001
        logger.exception("duplicate_detection: contact check failed for %s", payload.get("id"))
