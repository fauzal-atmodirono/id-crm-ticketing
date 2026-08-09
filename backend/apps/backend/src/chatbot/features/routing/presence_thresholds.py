"""P6 task 4 -- 10-minute / 1-hour agent-unavailability threshold alerts.

Requirements 4.13/4.14 ask for two alerts once an agent has been
"unavailable" for a while: a 10-minute warn to the agent AND the admin
(4.13), and a 1-hour escalate to the admin, WITH a review of the agent's
work in progress (4.14). Nothing in the codebase tracked elapsed
non-online time before task 1's ``PresenceEventStore`` -- this module is
the sweeper that reads that store and fires the two alerts.

Design load-bearing points
---------------------------
**"Unavailable" is task 2's ``CustomStatus.counts_as_unavailable``, not a
raw native status.** A ``busy`` agent mid-conversation is working, not
absent; ``counts_as_unavailable`` is exactly the flag task 2 built so this
task doesn't have to re-litigate which statuses are "away" (see
``custom_status.py``'s ``SEED_STATUSES`` comment). An unknown/unreadable
status key (``CustomStatusStore.get`` returning ``None``, which it does on
BOTH an unknown key and a store outage) is treated as "do not alert" --
the same fail-open direction ``get()``'s own contract recommends for
routing, applied here: a threshold-alert false negative during a store
outage is far cheaper than an alert storm for a key nobody recognises.

**Anti-noise, via the event-store's own ``stamp_alert``/``alerts_sent``.**
Each threshold (``WARN_ALERT_KEY``, ``ESCALATE_ALERT_KEY``) fires at most
once per continuous unavailable period: before sending, this module checks
whether the key is already in the *latest* presence event's
``alerts_sent``; after sending (or failing to -- see below) it calls
``stamp_alert`` to record it. A three-hour lunch sweeps dozens of times but
only ever produces two alerts. Re-arming for a *second* absence the same
day needs no extra bookkeeping: it falls out of ``PresenceEventStore``
being append-only -- the moment an agent returns to an available status and
leaves again, that is a brand-new ``PresenceEvent`` with a fresh, empty
``alerts_sent`` (see ``PresenceEvent``'s default), so both thresholds are
armed again automatically.

**Stamp anyway on transport failure.** If every alert leg raises, the
threshold is still stamped. This looks like swallowing a bug to a reader
who hasn't thought about it: the alternative -- leaving the key unstamped
so the next sweep retries -- means a down mail/WhatsApp transport turns one
missed alert into an alert-storm the moment it recovers (every sweep in
between would have kept retrying and failing). A single missed
notification is judged the safer failure mode than that storm.

**The race the store's author flagged -- now closed.** The original
``PresenceEventStore.stamp_alert`` resolved "the latest event" a second
time at write time, so a transition landing between this sweep's
``latest()`` read and its ``stamp_alert()`` call could land the stamp on a
newer event than the one the alert was actually about (old period's stamp
lost and re-fires; new period wrongly pre-armed). ``stamp_alert`` now takes
the exact event this module decided about (``expected_event``) and is a
no-op, logged, if the store's current latest event no longer matches it.
``_check_agent`` reads ``latest`` once and passes that same object into
both the warn and escalate ``stamp_alert`` calls below, which is exactly
the identity the guard checks against.

**Who "the agent" and "the admin" are, and which setting drives which
leg** (mirrors ``chatbot.features.chat.sla._build_pic_alert``'s
independent-legs, best-effort, return-``None``-when-unconfigured shape --
reusing that pattern and this repo's existing ``TwilioChannelAdapter``/
``SmtpEmailSender`` transports rather than adding a second notification
channel):

* **Agent leg (warn only)** -- email to ``AgentRecord.email``, via the
  injected ``email_sender``. There is no agent phone number anywhere in
  this codebase, so there is deliberately no WhatsApp leg to the agent.
  4.13 names the agent; 4.14 does not, so the escalate alert skips this
  leg.
* **Admin WhatsApp leg** -- ``settings.sla_pic_whatsapp`` (reused verbatim
  from the SLA alert fan-out) + ``twilio_adapter``. Both warn and escalate.
* **Admin email leg** -- ``settings.report_recipient_list()`` (reused from
  the scheduled bot-metrics report) + ``email_sender``. Both warn and
  escalate. No new recipient setting was added for this task; per the task
  brief, config is owned by a different wave and reusing the existing
  operator-contact settings is the honest reading of "the admin".

Every leg is independent and best-effort -- one failing must never
suppress another, since these are the only signals anyone gets that an
agent has gone quiet.

**The §4.14 WIP review -- honesty about what is delivered.**
``PresenceFetcher.fetch_agent_open_counts()`` gives an unscoped (whole
Chatwoot account) open-conversation *count* per agent, cheaply, with no
new API surface. ``fetch_conversations`` (the SLA engine's own plumbing)
gives full conversation dicts -- including real conversation ids -- but
only across whatever inbox scope ``settings.sla_inbox_ids`` covers
(``chatwoot_inbox_id`` alone by default). This module chooses
``fetch_conversations`` for ``WipSummary``, deliberately trading an
unscoped count for an itemised list of REAL case ids (never fabricated):
4.14 asks for a "review of the agent's work in progress", and a supervisor
handed a handful of actual conversation ids can go look at them; a bare
number cannot be reviewed. **This means the escalate alert's WIP list is
scoped to the same inboxes the SLA engine watches, not a full
account-wide audit** -- if a tenant routes agent chats through inboxes
outside that scope, this undercounts. That caveat is the honest reading of
"how much of 4.14 was delivered": an itemised, inbox-scoped WIP list, not
an exhaustive one.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol

import structlog
from apscheduler.schedulers.background import BackgroundScheduler

from chatbot.features.metrics.sync import fetch_conversations
from chatbot.features.routing.custom_status import build_custom_status_store
from chatbot.features.routing.presence import PresenceFetcher
from chatbot.features.routing.presence_store import build_presence_event_store

if TYPE_CHECKING:
    from collections.abc import Callable

    from chatbot.features.chat.adapters.twilio_channel import TwilioChannelAdapter
    from chatbot.features.metrics.email_sender import SmtpEmailSender
    from chatbot.features.routing.custom_status import CustomStatus
    from chatbot.features.routing.presence import AgentRecord
    from chatbot.features.routing.presence_store import PresenceEvent
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Alert keys stamped on the presence event's `alerts_sent` set (task 1's
# `stamp_alert`). These are the dedup keys, not display text.
WARN_ALERT_KEY = "unavailable_warn_10min"
ESCALATE_ALERT_KEY = "unavailable_escalate_1h"

# Alert-body cap on how many case ids to list -- the count field always
# reflects the true total even when the itemised list is truncated.
_MAX_CASE_IDS = 10

AlertLevel = Literal["warn", "escalate"]


@dataclass(frozen=True)
class WipSummary:
    """An agent's open-case snapshot attached to the escalate (1-hour) alert.

    See the module docstring's "§4.14 WIP review" section for exactly what
    this does and does not deliver: real, non-fabricated conversation ids,
    scoped to ``settings.sla_inbox_ids`` (the same scope the SLA engine
    uses), not an unscoped account-wide audit.
    """

    count: int
    case_ids: tuple[str, ...]


class _AgentDirectory(Protocol):
    """The one `PresenceFetcher` method this module depends on."""

    async def fetch_agents(self) -> list[AgentRecord]: ...


class _PresenceLog(Protocol):
    """The narrow slice of `PresenceEventStore`'s contract this module
    depends on -- kept structural so tests can inject a small in-memory
    fake instead of mocking Firestore, the same shape `custom_status.py`
    uses for its own injected collaborators."""

    async def latest(self, agent_id: int) -> PresenceEvent | None: ...

    async def elapsed_in_current_status(self, agent_id: int, now: datetime) -> timedelta | None: ...

    async def stamp_alert(
        self, agent_id: int, alert_key: str, expected_event: PresenceEvent
    ) -> None: ...


class _StatusCatalogue(Protocol):
    """The one `CustomStatusStore` method this module depends on."""

    async def get(self, key: str) -> CustomStatus | None: ...


class ThresholdAlert(Protocol):
    """The alert callback shape: fires one leg-set for one agent at one
    threshold level. `wip` is only ever populated for `level="escalate"`.
    """

    async def __call__(
        self,
        agent: AgentRecord,
        level: AlertLevel,
        elapsed_minutes: float,
        wip: WipSummary | None,
    ) -> None: ...


def _wip_summary_for_agent(agent_id: int, conversations: list[Any]) -> WipSummary:
    """Filter an already-fetched conversation list down to `agent_id`'s
    currently-open cases. Pure/sync -- the I/O lives in the caller.

    ``conversations`` is typed ``list[Any]``, not ``list[dict[str, Any]]``,
    even though ``fetch_conversations`` promises the latter -- these are
    parsed-JSON payloads from an external service, and the defensive
    ``isinstance`` check below is precisely the same guard
    ``PresenceFetcher.fetch_agent_open_counts`` already applies to the
    identical Chatwoot response shape.
    """
    case_ids: list[str] = []
    for conv in conversations:
        if not isinstance(conv, dict):
            continue
        if str(conv.get("status") or "") != "open":
            continue
        assignee = (conv.get("meta") or {}).get("assignee") or {}
        if not isinstance(assignee, dict) or assignee.get("id") != agent_id:
            continue
        conv_id = conv.get("id")
        if conv_id is not None:
            case_ids.append(str(conv_id))
    return WipSummary(count=len(case_ids), case_ids=tuple(case_ids[:_MAX_CASE_IDS]))


async def _fetch_wip_summary(
    agent_id: int, open_case_fetcher: Callable[[], list[dict[str, Any]]]
) -> WipSummary:
    """Run the (synchronous, HTTP-bound) conversation fetch off the event
    loop, and fail open to an empty summary -- a WIP-lookup failure must
    never prevent the escalate alert itself from firing."""
    try:
        conversations = await asyncio.to_thread(open_case_fetcher)
    except Exception as e:
        _log.warning("presence_wip_fetch_failed", agent_id=agent_id, error=str(e))
        return WipSummary(count=0, case_ids=())
    return _wip_summary_for_agent(agent_id, conversations)


def _build_threshold_alert(
    settings: Settings,
    *,
    twilio_adapter: TwilioChannelAdapter | None = None,
    email_sender: SmtpEmailSender | None = None,
) -> ThresholdAlert | None:
    """Build the presence-threshold alert callback: an email to the agent
    (warn only), a WhatsApp ping to the admin, and an email to the admin.

    See the module docstring for which setting/collaborator drives each
    leg. Returns `None` when nothing at all is configured (no
    `sla_pic_whatsapp` + adapter, no `report_recipient_list()` +
    `email_sender`, AND no `email_sender` for the agent leg), mirroring
    `chatbot.features.chat.sla._build_pic_alert` exactly: an unconfigured
    tenant attempts nothing. Every leg below is independent and
    best-effort -- one failing must never suppress another.
    """
    pic_number = settings.sla_pic_whatsapp
    wa_to = "whatsapp:" + pic_number.removeprefix("whatsapp:") if pic_number else ""
    want_admin_wa = bool(wa_to) and twilio_adapter is not None
    admin_recipients = settings.report_recipient_list()
    want_admin_email = bool(admin_recipients) and email_sender is not None
    want_agent_email = email_sender is not None
    if not (want_admin_wa or want_admin_email or want_agent_email):
        return None

    async def _alert(
        agent: AgentRecord,
        level: AlertLevel,
        elapsed_minutes: float,
        wip: WipSummary | None,
    ) -> None:
        mins = int(elapsed_minutes)
        who = agent.name or str(agent.id)
        if level == "escalate":
            text = f"\U0001f534 {who} has been unavailable for over {mins} minutes."
            if wip is not None:
                text += (
                    f" Open cases ({wip.count} total): {', '.join(wip.case_ids)}."
                    if wip.case_ids
                    else f" Open case count: {wip.count}."
                )
        else:
            text = f"⚠️ {who} has been unavailable for over {mins} minutes."

        if level == "warn" and want_agent_email and agent.email:
            try:
                assert email_sender is not None  # want_agent_email implies this  # noqa: S101
                email_sender.send(
                    to=[agent.email],
                    cc=[],
                    subject="You have been away for a while",
                    body=text,
                    attachments=[],
                )
            except Exception as e:
                _log.warning("presence_alert_agent_email_failed", agent_id=agent.id, error=str(e))

        if want_admin_wa:
            try:
                assert twilio_adapter is not None  # want_admin_wa implies this  # noqa: S101
                await twilio_adapter.send_message(conversation_id=wa_to, text=text)
            except Exception as e:
                _log.warning("presence_alert_admin_wa_failed", agent_id=agent.id, error=str(e))

        if want_admin_email:
            try:
                assert email_sender is not None  # want_admin_email implies this  # noqa: S101
                email_sender.send(
                    to=admin_recipients,
                    cc=[],
                    subject=f"Agent unavailable: {who}",
                    body=text,
                    attachments=[],
                )
            except Exception as e:
                _log.warning("presence_alert_admin_email_failed", agent_id=agent.id, error=str(e))

    return _alert


async def _check_agent(
    agent: AgentRecord,
    *,
    now: datetime,
    presence_store: _PresenceLog,
    status_store: _StatusCatalogue,
    settings: Settings,
    alert: ThresholdAlert | None,
    open_case_fetcher: Callable[[], list[dict[str, Any]]],
) -> None:
    """Check one agent against both thresholds and fire/stamp as needed."""
    latest = await presence_store.latest(agent.id)
    if latest is None:
        return  # never seen this agent -- nothing to measure elapsed time from

    status = await status_store.get(latest.status)
    if status is None or not status.counts_as_unavailable:
        # Fail-open in the same direction `CustomStatusStore.get`'s own
        # contract recommends: an unknown status key (or a catalogue
        # outage) means "no extra information", never "treat as absent".
        return

    elapsed = await presence_store.elapsed_in_current_status(agent.id, now)
    if elapsed is None:
        return
    elapsed_minutes = elapsed.total_seconds() / 60

    # See the module docstring's "race the store's author flagged -- now
    # closed" section: `latest` was read once above and is reused for both
    # the warn and escalate checks below -- as the `alerts_sent` dedup guard
    # AND as the `expected_event` passed into `stamp_alert`, which is what
    # lets the store detect a transition that lands underneath this sweep
    # and refuse to mis-stamp it. Checked in warn-then-escalate order so
    # that a sweep landing after both thresholds have already elapsed (e.g.
    # the very first sweep after a long, uninterrupted absence) still
    # reports them in the order they would chronologically have fired.
    if (
        elapsed_minutes >= settings.presence_warn_minutes
        and WARN_ALERT_KEY not in latest.alerts_sent
    ):
        if alert is not None:
            try:
                await alert(agent, "warn", elapsed_minutes, None)
            except Exception as e:
                # Judgement call, not a bug: stamp anyway. Retrying a failed
                # transport on every future sweep would turn one missed
                # alert into an alert-storm the moment it recovers -- a
                # single missed notification is the safer failure mode.
                _log.warning("presence_warn_alert_failed", agent_id=agent.id, error=str(e))
        await presence_store.stamp_alert(agent.id, WARN_ALERT_KEY, latest)

    if (
        elapsed_minutes >= settings.presence_escalate_minutes
        and ESCALATE_ALERT_KEY not in latest.alerts_sent
    ):
        wip = await _fetch_wip_summary(agent.id, open_case_fetcher)
        if alert is not None:
            try:
                await alert(agent, "escalate", elapsed_minutes, wip)
            except Exception as e:
                # Same reasoning as the warn branch above.
                _log.warning("presence_escalate_alert_failed", agent_id=agent.id, error=str(e))
        await presence_store.stamp_alert(agent.id, ESCALATE_ALERT_KEY, latest)


async def sweep_presence_thresholds(
    settings: Settings,
    *,
    now: datetime | None = None,
    presence_fetcher: _AgentDirectory | None = None,
    presence_store: _PresenceLog | None = None,
    status_store: _StatusCatalogue | None = None,
    alert: ThresholdAlert | None = None,
    open_case_fetcher: Callable[[], list[dict[str, Any]]] | None = None,
    twilio_adapter: TwilioChannelAdapter | None = None,
    email_sender: SmtpEmailSender | None = None,
) -> dict[str, int]:
    """The async sweep core: check every Chatwoot agent once, firing warn/
    escalate alerts as thresholds are crossed. This is the function tests
    call directly (`await`-able); `run_presence_threshold_job` below is the
    synchronous wrapper APScheduler actually invokes.

    Returns `{"agents_checked": 0}` and does nothing else when
    `settings.presence_threshold_alerts_enabled` is off -- the documented
    "off = no alerts at all, regardless of `presence_tracking_enabled`"
    contract, enforced here too (not just in the scheduler starter) so a
    direct caller gets the same guarantee.

    `alert`, if not supplied, is built via `_build_threshold_alert` from
    `twilio_adapter`/`email_sender` (both otherwise unused when `alert` is
    injected directly, e.g. by tests).
    """
    if not settings.presence_threshold_alerts_enabled:
        return {"agents_checked": 0}

    clock = now or datetime.now(UTC)
    fetcher: _AgentDirectory = presence_fetcher or PresenceFetcher(settings)
    store: _PresenceLog = presence_store or build_presence_event_store(settings)
    statuses: _StatusCatalogue = status_store or build_custom_status_store(settings)
    alert_fn = (
        alert
        if alert is not None
        else _build_threshold_alert(
            settings, twilio_adapter=twilio_adapter, email_sender=email_sender
        )
    )
    fetch_cases: Callable[[], list[dict[str, Any]]] = open_case_fetcher or (
        lambda: fetch_conversations(settings)
    )

    try:
        agents = await fetcher.fetch_agents()
    except Exception as e:
        _log.error("presence_threshold_sweep_fetch_agents_failed", error=str(e))
        return {"agents_checked": 0}

    checked = 0
    for agent in agents:
        try:
            await _check_agent(
                agent,
                now=clock,
                presence_store=store,
                status_store=statuses,
                settings=settings,
                alert=alert_fn,
                open_case_fetcher=fetch_cases,
            )
        except Exception as e:  # one agent's failure must never skip the rest
            _log.error("presence_threshold_sweep_agent_failed", agent_id=agent.id, error=str(e))
        checked += 1
    return {"agents_checked": checked}


def run_presence_threshold_job(settings: Settings) -> dict[str, int]:
    """Synchronous entrypoint APScheduler calls each tick. Best-effort:
    never raises, matching `run_sync_job`/`run_report_job` in
    `features/metrics/scheduler.py` and `run_sla_scan_job` in
    `features/chat/sla.py` (whose own sync-wraps-async shape this mirrors
    exactly: the whole per-sweep unit of work is one `asyncio.run` call).
    """
    try:
        return asyncio.run(sweep_presence_thresholds(settings))
    except Exception as e:
        _log.error("presence_threshold_job_failed", error=str(e))
        return {"agents_checked": 0}


def start_presence_threshold_sweeper(
    settings: Settings,
    *,
    scheduler: Any | None = None,
    job: Callable[[], object] | None = None,
) -> Any | None:
    """Start the in-app sweeper that fires the §4.13/4.14 presence
    threshold alerts, when `settings.presence_threshold_alerts_enabled` is
    on; else return `None` without creating a scheduler (no polling, no
    alerts -- the same off-by-default shape as
    `start_metrics_scheduler`/`start_report_scheduler`).

    Ticks every `settings.presence_poll_seconds` -- the same cadence the
    sibling presence poller (`presence_poller.py`, built independently by
    another P6 task) uses, so a sweep never meaningfully outruns the
    freshest presence data available; tighter polling would only multiply
    Chatwoot API calls for no decision-relevant precision at 10-minute/
    1-hour thresholds.

    This is the function a later wiring wave calls from `main.py` --
    signature intentionally mirrors `start_metrics_scheduler`/
    `start_report_scheduler` (`scheduler`/`job` overrides for tests).
    """
    if not settings.presence_threshold_alerts_enabled:
        return None
    sched = scheduler or BackgroundScheduler()
    run = job or (lambda: run_presence_threshold_job(settings))
    sched.add_job(
        run,
        trigger="interval",
        seconds=settings.presence_poll_seconds,
        id="presence_threshold_sweeper",
        next_run_time=datetime.now(UTC),  # first sweep shortly after startup
        replace_existing=True,
    )
    sched.start()
    _log.info(
        "presence_threshold_sweeper_started",
        interval_seconds=settings.presence_poll_seconds,
    )
    return sched
