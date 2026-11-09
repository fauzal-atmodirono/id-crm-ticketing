"""One pre-filled private note for every AI -> human handoff, chat and phone.

When the AI hands a Proton case to a person (WhatsApp, web chat, the API
inbox or a phone call), the agent who picks it up should not have to read the
whole thread to know who this is, which car, which dealer, why, and how
urgent it is (RFP 3.2.2, 3.2.4, 4.29-4.36). Before this module the chat note
was ``"[Handoff to human agent]\\n<the AI's last reply>"`` and the phone note
was the reason and summary plus three caller lines -- two formats, two code
paths. ``format_handoff_note`` is now the ONE formatter both use (phone:
``phone/bridge.py``; chat: ``HandoffNoteService`` below, called from the
``crm-`` /chat/turn handoff branch and ``begin_handoff``), so the agent sees the
same shape on every channel. Pattern from the Logisly demo (escalate_to_
operations_tool + one pre-filled note on the ``crm-`` branch).

Why the note never labels the dealer (controller ruling, 9 Nov 2026): reports
count ``dealer_<slug>`` as "escalated to dealer", and a handoff to customer
service is not that. The note SHOWS the dealer -- a dealer the customer named
and that matched exactly one configured dealer (``match_dealer``, the same
matcher the phone path uses), else the usual dealer from history marked "not
confirmed" -- and the confirmed match is kept on the conversation as the
``caller_dealer`` / ``caller_dealer_text`` attributes. Escalating to the dealer
is then ONE step for the agent: add the ``escalate`` label. The agent service's
``maybe_escalate`` calls ``/escalation/notify``, which (with this flag on)
reads ``caller_dealer`` when no ``dealer_<slug>`` label is present, forwards to
that dealer and applies the label at that moment (``escalation_router.py``). A
history dealer is never used for that automatically -- the note tells the
agent to add both labels by hand if the customer confirms it.

Priority follows the AI's urgency: the classified priority from
``classify_ticket_tool`` (chat), raised to ``urgent`` whenever the reason or
summary reads as a breakdown / accident / safety matter, in English or Malay
(the phone tool has no urgency field, so for calls the text is all there is).

Everything is behind ``handoff_note_enabled`` (default off: the old notes,
byte-identical) and fail-open: a Chatwoot error costs the note, never the
handoff. Personal data: the note goes only to the CRM (an authenticated
agent); the mobile is masked there too, and nothing here logs a name, number
or plate.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.chat.phone.caller_context import dealer_display, match_dealer
from chatbot.features.privacy.pii import mask_phone_value

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

NOTE_HEADING = "[Handoff to human agent]"
ESCALATE_LABEL = "escalate"

_PRIORITIES = ("low", "medium", "high", "urgent")
_PRIORITY_ALIASES = {"normal": "medium", "critical": "urgent"}

# Breakdown / roadside / accident / safety, English and Malay -- PHRASES, not
# loose words (review fix): "asap" (ASAP), "tunda" (postpone), "rosak"
# (any fault: "aircond rosak"), "bahaya", a bare "safety" ("safety rating")
# and a bare "crash" ("the app crash") all marked everyday cases urgent.
_SAFETY_RE = re.compile(
    r"\b(?:break\s?down|broke\s?down|broken\s?down|roadside|tow\s?truck|towing"
    r"|kereta\s+tunda|lori\s+tunda|(?:car|road|vehicle)\s+crash|accident|collision"
    r"|injur\w*|fire\b|on\s+fire|smoke|smoking\s+engine|burning\s+smell"
    r"|brakes?\s+(?:fail|failed|failure|not\s+working)|brek\s+tak\s+makan|brek\s+rosak"
    r"|stranded|stuck\s+on\s+the\s+(?:highway|road)|highway|lebuhraya"
    r"|rosak\s+(?:di\s+)?(?:tepi\s+jalan|tengah\s+jalan|di\s+tol|tol|lebuhraya)"
    r"|kemalangan|terkandas|kebakaran|berasap|airbag|emergency|kecemasan)",
    re.IGNORECASE,
)
_RANK = {p: i for i, p in enumerate(_PRIORITIES)}

_CHANNEL_NAMES = {
    "Channel::Whatsapp": "WhatsApp",
    "Channel::TwilioSms": "WhatsApp / SMS",
    "Channel::WebWidget": "Web chat",
    "Channel::Api": "API inbox",
    "Channel::Email": "Email",
    "Channel::FacebookPage": "Facebook",
    "Channel::Instagram": "Instagram",
    "Channel::Voice": "Phone call",
}

_DEALER_CACHE_SECONDS = 300.0
_DEALER_FETCH_TIMEOUT_SECONDS = 1.5
_MAX_SUMMARY = 1200
_DEALER_LABEL = re.compile(r"^dealer_(.+)$")
# caller_context SOURCE_SPOKEN / SOURCE_WEB_CALL_AS (caller ID is the only
# source the network proves).
_UNVERIFIED_SOURCES = frozenset({"spoken", "web_call_as"})


def handoff_priority(
    *,
    classified: str | None = None,
    texts: Iterable[str | None] = (),
    urgent: bool = False,
) -> str:
    """Chatwoot priority for a handoff: ``urgent`` for breakdown/safety (or
    ``urgent=True``: the sentiment classifier said so), else the classified
    priority, else ``medium``."""
    if urgent or any(_SAFETY_RE.search(t or "") for t in texts):
        return "urgent"
    key = (classified or "").strip().lower()
    key = _PRIORITY_ALIASES.get(key, key)
    return key if key in _PRIORITIES else "medium"


def conversation_path(account_id: int | str, conversation_id: str) -> str:
    """Dashboard path of a conversation (relative: the note is read inside
    the same Chatwoot, whatever host it is served on)."""
    return f"/app/accounts/{account_id}/conversations/{conversation_id}"


def is_phone_conversation(conv: Mapping[str, Any] | None) -> bool:
    """Whether a Chatwoot conversation payload is a phone call. Calls live in
    the API inbox like other API traffic, so the channel type cannot tell;
    the markers the phone path writes can (external_id / session_id
    ``phone-<CallSid>``, caller_phone_source from caller context)."""
    if not isinstance(conv, Mapping):
        return False
    custom = conv.get("custom_attributes") or {}
    extra = conv.get("additional_attributes") or {}
    if not isinstance(custom, Mapping):
        custom = {}
    if not isinstance(extra, Mapping):
        extra = {}
    return (
        str(custom.get("external_id") or "").startswith("phone-")
        or str(extra.get("session_id") or "").startswith("phone-")
        or bool(custom.get("caller_phone_source"))
    )


@dataclass(frozen=True)
class DealerLine:
    """The dealer as the note shows it. ``slug`` + ``confirmed`` = the
    customer named it and it matched one configured dealer; ``slug`` without
    ``confirmed`` = the usual dealer from history; ``text`` = what the
    customer said that matched nothing."""

    slug: str = ""
    confirmed: bool = False
    text: str = ""
    # A dealer_<slug> label is already on the conversation.
    escalated: bool = False


@dataclass(frozen=True)
class HandoffNote:
    channel: str
    reason: str
    summary: str
    priority: str
    name: str = ""
    mobile: str = ""
    # Told by the caller / picked in the test console, not proven by the
    # network: the agent must not trust it as the caller's identity.
    mobile_unverified: bool = False
    vehicle_model: str = ""
    vehicle_plate: str = ""
    dealer: DealerLine = DealerLine()
    case_type: str = ""
    category: str = ""
    subcategory: str = ""
    transcript_path: str = ""


def _dealer_line(d: DealerLine) -> str:
    if d.slug and d.escalated:
        return (
            f"Dealer: {dealer_display(d.slug)} — already escalated to the dealer (dealer_{d.slug})"
        )
    if d.slug and d.confirmed:
        return (
            f"Dealer: {dealer_display(d.slug)} — escalate by adding the `{ESCALATE_LABEL}` label "
            f"(the dealer_{d.slug} label and the dealer email follow)"
        )
    if d.slug:
        return (
            f"Dealer: {dealer_display(d.slug)} (usual dealer from history, not confirmed) — "
            f"if the customer confirms it, add the `dealer_{d.slug}` and `{ESCALATE_LABEL}` labels"
        )
    if d.text:
        return (
            f"Dealer: {d.text} (not a configured dealer — add the matching dealer_<name> label, "
            f"then `{ESCALATE_LABEL}`)"
        )
    return "Dealer: not given"


def format_handoff_note(n: HandoffNote) -> str:
    """The one handoff note. Plain lines (Chatwoot renders the markdown link
    and backticks in a private note)."""
    who = n.name or "not identified"
    if n.mobile:
        who += f" · {mask_phone_value(n.mobile)}"
        if n.mobile_unverified:
            who += " (unverified)"
    vehicle = " · ".join(x for x in (n.vehicle_model, n.vehicle_plate) if x) or "not given"
    lines = [
        f"{NOTE_HEADING} {n.channel} · Priority: {n.priority.upper()}",
        f"Reason: {n.reason or 'not given'}",
        f"Summary: {(n.summary or 'not given')[:_MAX_SUMMARY]}",
        f"Who: {who}",
        f"Vehicle: {vehicle}",
        _dealer_line(n.dealer),
    ]
    # The subcategory already carries its category ("Aftersales: Breakdown").
    case = " · ".join(x for x in (n.case_type, n.subcategory or n.category) if x)
    if case:
        lines.append(f"Case: {case}")
    if n.transcript_path:
        lines.append(
            f"Live transcript: [open]({n.transcript_path}) — the call transcript posts in this "
            "conversation as it happens"
        )
    return "\n".join(lines)


async def apply_priority(
    port: Any, ticket_id: str, priority: str, *, current: str | None = None
) -> None:
    """Raise the conversation priority when the port can (ChatwootAdapter);
    never lower it -- an agent or an SLA rule may already have set it
    higher. ``current`` is read from the conversation when not given; an
    unreadable or unset one counts as lowest. A port without a setter
    (Zendesk, test fakes) just skips."""
    setter = getattr(port, "set_conversation_priority", None)
    if setter is None:
        return
    if current is None:
        getter = getattr(port, "get_conversation", None)
        if getter is not None:
            try:
                conv = await getter(ticket_id)
                current = str((conv or {}).get("priority") or "") if isinstance(conv, dict) else ""
            except Exception:
                current = ""
    if _RANK.get((current or "").lower(), -1) >= _RANK.get(priority, 1):
        return
    await setter(ticket_id, priority)


def _s(value: Any) -> str:
    return " ".join(str(value or "").split())


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


DealerSource = Callable[[], Awaitable[list[str]]]
NotePoster = Callable[[str], Awaitable[Any]]


class HandoffNoteService:
    """Chat side: builds the note from the session state the ADK tools wrote
    (handoff reason/summary/dealer, classification) plus the Chatwoot
    conversation (contact name and mobile, plate, stored dealer), posts it,
    sets the priority and stores a confirmed dealer match. ``chatwoot`` is a
    ``ChatwootAdapter`` (duck-typed)."""

    def __init__(
        self,
        chatwoot: Any,
        settings: Settings,
        *,
        dealer_source: DealerSource | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._chatwoot = chatwoot
        self._settings = settings
        self._dealer_source = dealer_source
        self._clock = clock
        self._dealer_cache: tuple[float, list[str]] | None = None

    @property
    def chatwoot(self) -> Any:
        """The adapter this writes to (callers check it is the one their
        ticket lives in)."""
        return self._chatwoot

    async def dealer_slugs(self) -> list[str]:
        now = self._clock()
        if self._dealer_cache is not None and now - self._dealer_cache[0] < _DEALER_CACHE_SECONDS:
            return list(self._dealer_cache[1])
        if self._dealer_source is None:
            return []
        try:
            slugs = await asyncio.wait_for(
                self._dealer_source(), timeout=_DEALER_FETCH_TIMEOUT_SECONDS
            )
        except Exception as e:
            _log.warning("handoff_note_dealer_list_failed", error_type=type(e).__name__)
            return []
        clean = list(dict.fromkeys(str(s).strip().lower() for s in slugs if str(s).strip()))
        self._dealer_cache = (now, clean)
        return list(clean)

    async def post_for_chat(
        self,
        conversation_id: str,
        state: Mapping[str, Any],
        *,
        channel: str | None = None,
        customer_name: str | None = None,
        customer_phone: str | None = None,
        fallback_summary: str = "",
        post: NotePoster | None = None,
    ) -> None:
        """Never raises; bounded by ``handoff_note_timeout_seconds``.
        ``post`` replaces the plain private-note write (begin_handoff passes
        its own comment write, which also reopens the conversation)."""
        try:
            await asyncio.wait_for(
                self._post_for_chat(
                    conversation_id,
                    dict(state),
                    channel=channel,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                    fallback_summary=fallback_summary,
                    post=post,
                ),
                timeout=self._settings.handoff_note_timeout_seconds,
            )
        except Exception as e:
            _log.warning(
                "handoff_note_failed", ticket_id=conversation_id, error_type=type(e).__name__
            )

    async def _read_conversation(self, conversation_id: str) -> dict[str, Any]:
        try:
            return _dict(await self._chatwoot.get_conversation(conversation_id))
        except Exception as e:
            # Still post what the session knows.
            _log.warning(
                "handoff_note_conversation_read_failed",
                ticket_id=conversation_id,
                error_type=type(e).__name__,
            )
            return {}

    async def _resolve_dealer(
        self, state: dict[str, Any], attrs: dict[str, Any], labels: list[str]
    ) -> tuple[DealerLine, dict[str, Any]]:
        """(the note's dealer, the attributes to store). Confirmed = named by
        the customer and matched; nothing from history is ever stored."""
        label_slug = next((m.group(1) for lbl in labels if (m := _DEALER_LABEL.match(lbl))), "")
        if label_slug:
            # Already escalated to a dealer by someone: show it as it is.
            return DealerLine(slug=label_slug, confirmed=True, escalated=True), {}
        stored = _s(attrs.get("caller_dealer")).lower()
        if stored:
            return DealerLine(slug=stored, confirmed=True), {}
        lead = _dict(state.get("lead_details"))
        said = (
            _s(state.get("handoff_dealer"))
            or _s(lead.get("preferred_dealer"))
            or _s(attrs.get("caller_dealer_text"))
        )
        if not said:
            return DealerLine(), {}
        slug = match_dealer(said, await self.dealer_slugs())
        store: dict[str, Any] = {}
        if said != _s(attrs.get("caller_dealer_text")):
            store["caller_dealer_text"] = said[:80]
        if slug:
            store["caller_dealer"] = slug
            return DealerLine(slug=slug, confirmed=True), store
        return DealerLine(text=said[:80]), store

    async def _post_for_chat(
        self,
        conversation_id: str,
        state: dict[str, Any],
        *,
        channel: str | None,
        customer_name: str | None,
        customer_phone: str | None,
        fallback_summary: str,
        post: NotePoster | None,
    ) -> None:
        conv = await self._read_conversation(conversation_id)
        meta = _dict(conv.get("meta"))
        sender = _dict(meta.get("sender"))
        contact_attrs = _dict(sender.get("custom_attributes"))
        attrs = _dict(conv.get("custom_attributes"))
        labels = [str(x) for x in conv.get("labels") or [] if x]

        reason = _s(state.get("handoff_reason")) or "help_request"
        summary = _s(state.get("handoff_summary")) or _s(fallback_summary)
        dealer, store = await self._resolve_dealer(state, attrs, labels)
        note = HandoffNote(
            channel=_CHANNEL_NAMES.get(_s(meta.get("channel")), "") or channel or "Chat",
            reason=reason,
            summary=summary,
            priority=handoff_priority(
                classified=_s(state.get("priority")),
                texts=[reason, summary, _s(state.get("subcategory"))],
                urgent=_s(state.get("sentiment")).lower() == "urgent",
            ),
            name=_s(sender.get("name")) or _s(customer_name),
            mobile=_s(sender.get("phone_number")) or _s(customer_phone),
            # A number the phone path did not get from caller ID.
            mobile_unverified=bool(attrs.get("caller_unverified"))
            or _s(attrs.get("caller_phone_source")) in _UNVERIFIED_SOURCES,
            vehicle_model=_s(state.get("vehicle_model"))
            or _s(attrs.get("vehicle_model"))
            or _s(contact_attrs.get("vehicle_model")),
            vehicle_plate=_s(attrs.get("vehicle_plate")) or _s(contact_attrs.get("vehicle_no")),
            dealer=dealer,
            case_type=_s(state.get("case_type")),
            category=_s(state.get("category")),
            subcategory=_s(state.get("subcategory")),
        )
        text = format_handoff_note(note)
        if post is not None:
            await post(text)
        else:
            await self._chatwoot.add_private_note(conversation_id, text)
        await apply_priority(
            self._chatwoot,
            conversation_id,
            note.priority,
            current=str(conv.get("priority") or "") if conv else None,
        )
        if store:
            await self._chatwoot.set_conversation_attributes(conversation_id, store)
        _log.info(
            "handoff_note_posted",
            ticket_id=conversation_id,
            priority=note.priority,
            dealer_confirmed=dealer.confirmed,
        )
