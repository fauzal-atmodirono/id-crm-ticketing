"""Package C Task 6: who a live call gets transferred to, and the TwiML
that dials them.

`HandoffTarget` is a target *descriptor*, not a bare phone number: Twilio
cannot connect a WhatsApp call to any PSTN endpoint (see the design doc's
appendix §12.3), so a future WhatsApp-capable resolver needs to be able to
return a Twilio Client identifier instead of an E.164 number. `kind`
distinguishes the two so `dial_twiml` knows which TwiML noun to emit;
today only `HandoffTargetResolver`'s "pstn" branch is reachable.

Phase 1 (this task) resolves a single static hunt-group number
(`phone_handoff_target_number`). The routing-backed per-agent
implementation described in the design doc's §5.2 is a second
implementation of the same `resolve() -> HandoffTarget | None` interface,
added once that decision lands -- not built speculatively here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape, quoteattr

import structlog

from chatbot.features.metrics.business_hours import working_minutes_between

if TYPE_CHECKING:
    from chatbot.features.chat.ports import ConversationLogPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


PLACEHOLDER_NUMBERS: frozenset[str] = frozenset(
    {"+60300000001", "+60000000000", "+1234567890", "+60123456789", "00000000", "+60300000000"}
)


def validate_handoff_target_settings(settings: Settings) -> None:
    """Refuse to start with a placeholder number configured as the handoff target.

    Called from `bootstrap_application()` (see the comment at that call site for
    why this is a refusal and not a warning). It shipped with **no caller at
    all**, which made P11's own constraint -- "placeholder numbers must fail
    loudly, at startup, not at dial time" -- false: the service booted clean and
    the first handoff dialled an unallocated number, recording a `no-answer`
    indistinguishable from an agent not picking up. `test_p11_wiring.py` boots
    the real app with a placeholder in the environment, so removing the call
    fails the suite rather than quietly restoring that state.

    Only fires when `phone_handoff_enabled` is on: with handoff off nothing
    dials, so a placeholder left in a copied env is inert and must not stop a
    boot.
    """
    if settings.phone_handoff_enabled:
        target = settings.phone_handoff_target_number.strip()
        if target in PLACEHOLDER_NUMBERS:
            raise ValueError(
                f"Setting 'phone_handoff_target_number' is configured with placeholder number {target!r}. "
                "Configure a valid E.164 phone number."
            )


@dataclass(frozen=True)
class HandoffTarget:
    """`kind` is `"pstn"` (dial_twiml emits `<Number>`) or `"client"`
    (emits `<Client>`, for a Twilio Client/softphone identifier -- not
    reachable via Phase 1's static resolver, kept for the interface's
    sake). `value` is the E.164 number or Client identifier respectively.
    """

    kind: str
    value: str


class HandoffTargetResolver:
    """Phase 1: resolve the static `phone_handoff_target_number` hunt-group
    number, gated by `phone_handoff_enabled` and by whether the tenant's
    default Chatwoot inbox is currently within its configured business
    hours -- reusing `features.metrics.business_hours.
    working_minutes_between` (the SAME row-shape parser the BigQuery ETL
    already uses for RSA/turnaround-time reporting) rather than adding a
    second notion of "open" (design doc §5.3). Returning `None` here always
    means "do not attempt a transfer right now" -- the bridge's caller
    cannot distinguish disabled / unconfigured / out-of-hours from this
    return value alone, and by design does not need to: all three fall
    back identically to today's ticket-only behaviour (see bridge.py's
    `_attempt_transfer`). A dial that actually starts but goes unanswered
    is a DIFFERENT case, handled downstream by `/webhooks/phone/dial-status`.
    """

    def __init__(self, settings: Settings, log_port: ConversationLogPort) -> None:
        self._settings = settings
        self._log_port = log_port
        # Whole-branch review fix (Important 6): the business-hours answer,
        # warmed by prefetch() off the audio path. None = cold (never
        # prefetched, or the prefetch itself failed), in which case
        # resolve() falls back to doing the lookup inline as before.
        self._hours_ok: bool | None = None

    async def prefetch(self) -> None:
        """Warm the business-hours answer so ``resolve()`` needs no HTTP.

        ``PhoneBridge._attempt_transfer`` runs INLINE inside ``pump()``,
        the sole Gemini->Twilio audio forwarder. Doing the ``GET
        /inboxes/{id}`` there meant that on every handoff, caller->Gemini
        audio kept flowing while Gemini->Twilio audio did not, so the
        buffered output dumped at once on resume and the call stayed
        skewed until a barge-in. The bridge fires this as a detached task
        at call start instead, so the answer is already in hand by the
        time (always many seconds later) a handoff is actually requested.

        Never raises: ``_within_business_hours`` already fails open, and
        this is fire-and-forget from the live-call path. Whether the
        answer is a minute stale is immaterial -- a call that starts
        inside business hours is meant to be transferable for its whole
        duration.
        """
        try:
            self._hours_ok = await self._within_business_hours()
        except Exception as e:  # pragma: no cover -- _within_business_hours never raises
            _log.error("phone_handoff_hours_prefetch_failed", error=str(e))

    async def resolve(self) -> HandoffTarget | None:
        if not self._settings.phone_handoff_enabled:
            return None
        number = self._settings.phone_handoff_target_number.strip()
        if not number:
            return None
        # Review fix (Critical): a PSTN <Dial> with no callerId defaults to
        # the parent leg's From, which on this repo's browser-softphone
        # inbound path is `client:<identity>` -- Twilio rejects that as a
        # caller id for a <Number> (error 13214), a TwiML error that drops
        # the call mid-transfer, AFTER the tool response already promised
        # "transferring". Treat "no caller id configured" exactly like the
        # no-target-configured case above -- do not dial blind.
        #
        # This resolver only ever produces a PSTN target, so this guard is
        # correctly unconditional HERE. It must NOT be copied into a resolver
        # that can return kind="client" (see agent_client_resolver.py):
        # error 13214 is a <Number> restriction, and applying it to <Client>
        # would silently disable the softphone for any tenant that never
        # configured a PSTN caller id.
        if not self._settings.phone_handoff_caller_id.strip():
            _log.warning("phone_handoff_no_caller_id_configured")
            return None
        # Prefer the prefetched answer (see prefetch()); only pay for the
        # Chatwoot round trip here if the cache is still cold.
        within = self._hours_ok
        if within is None:
            within = await self._within_business_hours()
        if not within:
            return None
        return HandoffTarget(kind="pstn", value=number)

    async def _within_business_hours(self) -> bool:
        """Fail OPEN (True): an unconfigured inbox, or any failure reading
        it, must not silently disable the whole handoff feature -- matches
        `working_minutes_between`'s own "not configured -> always open"
        default (and `agent/`'s `is_within_business_hours`, the sibling
        point-in-time check for the same Chatwoot `working_hours` shape).
        """
        inbox_id = self._settings.chatwoot_inbox_id
        if not inbox_id:
            return True
        try:
            inbox = await self._log_port.get_inbox_working_hours(inbox_id)
        except Exception as e:
            _log.error("phone_handoff_hours_check_failed", error=str(e))
            return True
        if inbox is None:
            return True
        now = datetime.now(UTC)
        # A 1-minute probe window: working_minutes_between computes a
        # DURATION, not a point-in-time boolean, so this reuses it (rather
        # than a second parser) by asking "does the next minute overlap the
        # working-hours window at all".
        return working_minutes_between(now, now + timedelta(minutes=1), inbox) > 0


def _parameters_xml(parameters: dict[str, str] | None) -> str:
    """`<Parameter>` children, escaped. Values here include MODEL-GENERATED
    text (the handoff `reason`/`summary`), i.e. untrusted input going into an
    XML attribute -- `quoteattr` is load-bearing, not tidiness. Malformed
    TwiML on a live call drops the caller."""
    if not parameters:
        return ""
    return "".join(
        f"<Parameter name={quoteattr(name)} value={quoteattr(value)}/>"
        for name, value in parameters.items()
    )


def _client_noun(identity: str, parameters: dict[str, str] | None = None) -> str:
    """The LONG form. The shorthand `<Client>id</Client>` accepts no children,
    so it cannot carry the context the ringing browser needs."""
    return f"<Client><Identity>{escape(identity)}</Identity>{_parameters_xml(parameters)}</Client>"


def dial_twiml(
    target: HandoffTarget,
    action_url: str,
    timeout: int,
    caller_id: str,
    parameters: dict[str, str] | None = None,
) -> str:
    """TwiML that dials `target` and posts the outcome to `action_url`
    (see `/webhooks/phone/dial-status`). `timeout` is Twilio's `<Dial>`
    ring timeout in seconds before it gives up and fires `action` with
    `DialCallStatus=no-answer`.

    `caller_id` applies to the PSTN branch ONLY. A `<Number>` `<Dial>` with
    no `callerId` falls back to the parent leg's `From`, which on this
    repo's browser-softphone inbound path is a `client:` identifier Twilio
    rejects for a PSTN caller id (error 13214) -- see `HandoffTargetResolver.
    resolve()`, which refuses to resolve a PSTN target at all when
    `phone_handoff_caller_id` is unconfigured. `<Client>` has no such
    restriction, so the attribute is omitted entirely on that branch rather
    than emitted empty.

    `parameters` are ignored on the PSTN branch: a phone has nowhere to put
    them. They exist for `<Client>`, where they arrive in the browser as
    `call.customParameters`.
    """
    if target.kind in ("client", "clients"):
        # "clients" is the immediate fan-out target: a comma-separated list of
        # identities, emitted as one <Client> noun each so Twilio rings them
        # simultaneously and the first to accept wins. It exists because a
        # freshly created phone conversation has NO assignee, so stage 1 has
        # nobody to ring -- and stage 2's fan-out is only reachable from the
        # dial-status callback of an actual stage-1 dial. Without this, the
        # single most common inbound case rang nobody at all.
        # The CALLER enforces Twilio's 10-noun cap; see AgentClientResolver.
        identities = [i for i in target.value.split(",") if i]
        noun = "".join(_client_noun(i, parameters) for i in identities)
        caller_id_attr = ""
    else:
        noun = f"<Number>{escape(target.value)}</Number>"
        caller_id_attr = f" callerId={quoteattr(caller_id)}" if caller_id else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        # `int(...)` is load-bearing, not decoration: `timeout` reaches here from
        # a settings field, and pydantic will happily hand back a float for a
        # value written `15.0` in a tenant env. Twilio rejects a non-integer
        # `<Dial timeout>` attribute, which fails the dial -- i.e. the handoff
        # silently does not connect.
        f'<Response><Dial action={quoteattr(action_url)} timeout="{int(timeout)}"'
        f"{caller_id_attr}>"
        f"{noun}</Dial></Response>"
    )


def fanout_twiml(
    identities: list[str],
    action_url: str,
    timeout: int,
    parameters: dict[str, str] | None = None,
) -> str:
    """Stage 2: ring every available agent at once, first accept wins.

    Returns `""` when `identities` is empty so callers can branch on "is
    there anyone to ring?" without constructing a `<Dial>` with zero nouns,
    which is a TwiML error on a live call.

    Twilio allows at most 10 nouns per `<Dial>`; enforcing that cap is the
    CALLER's job (`settings.phone_fanout_max_agents`), because silently
    truncating here would hide from the caller that some agents were never
    rung.
    """
    if not identities:
        return ""
    nouns = "".join(_client_noun(i, parameters) for i in identities)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Dial action={quoteattr(action_url)} timeout="{int(timeout)}">'
        f"{nouns}</Dial></Response>"
    )
