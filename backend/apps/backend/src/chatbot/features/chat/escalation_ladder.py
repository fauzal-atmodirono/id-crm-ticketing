"""The periodic sweep that walks escalated cases up the dealer ladder.

Shape borrowed wholesale from `sla.py`: an APScheduler job, a scan over the
Chatwoot conversations in scope, and a decision per conversation. What
differs is where state lives and how carefully a step is allowed to fire.

**State lives in Chatwoot custom attributes, not a new store.** The three
facts the ladder needs -- when step 1 went out (`escalation_notified_at`),
whether the dealer has answered (`escalation_replied_at`), and which rung we
are on -- are already conversation attributes written by `agent/`. Adding a
Firestore collection would mean two stores to keep consistent, an operator
unable to see the ladder position without a console, and a BI pipeline that
already syncs custom attributes learning a second source. The cost is that
ladder state is only as durable as the conversation, which is acceptable:
if the conversation is gone there is nothing left to escalate.

**Stamp before send.** `escalation_step<N>_sent_at` is written first, so a
crash between stamp and send loses a reminder; the reverse ordering would
resend one. Emailing a Dealer Owner twice about the same case is worse than
emailing them late -- it reads as a system out of control to the person the
SOP escalates to precisely because their attention is expensive.

**One rung per sweep.** Enforced in `escalation_policy.due_step`, not here,
so it holds however the sweep is driven.

Dry run is the default whenever the ladder is enabled: it logs what it would
send, to whom, and when, and touches nothing. A ladder firing on wrong
timers damages a real business relationship and is not recoverable with a
hotfix, so the first week of any tenant's ladder is meant to be read, not
sent.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from apscheduler.schedulers.background import BackgroundScheduler

from chatbot.features.chat.escalation_policy import (
    PHONE,
    apply_delay_overrides,
    describe,
    due_step,
    load_steps,
    resolve_recipients,
)
from chatbot.features.chat.ladder_policy_repository import resolve_ladder_config
from chatbot.features.chat.sla_clock import InboxCache, elapsed_minutes
from chatbot.features.metrics.sync import fetch_conversations

if TYPE_CHECKING:
    from chatbot.features.chat.escalation_notifier import EscalationNotifier
    from chatbot.features.chat.escalation_policy import EscalationStep
    from chatbot.features.chat.pic_store import DealerStore, ProtonNetStore
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

ESCALATE_LABEL = "escalate"
DEALER_LABEL_PREFIX = "dealer_"

NOTIFIED_ATTR = "escalation_notified_at"
REPLIED_ATTR = "escalation_replied_at"
ACKNOWLEDGED_ATTR = "escalation_acknowledged_at"
LADDER_STEP_ATTR = "escalation_step"

_MINUTES_PER_HOUR = 60
_PHONE_RESPONSE_WINDOW = timedelta(hours=1)


def step_sent_attr(step_no: int) -> str:
    """`escalation_step3_sent_at` -- the per-step idempotency stamp."""
    return f"escalation_step{step_no}_sent_at"


def _labels(conv: dict[str, Any]) -> list[str]:
    raw = conv.get("labels")
    return [str(t) for t in raw] if isinstance(raw, list) else []


def _attrs(conv: dict[str, Any]) -> dict[str, Any]:
    raw = conv.get("custom_attributes")
    return raw if isinstance(raw, dict) else {}


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _dealer_slug(conv: dict[str, Any]) -> str | None:
    for label in _labels(conv):
        if label.startswith(DEALER_LABEL_PREFIX):
            slug = label[len(DEALER_LABEL_PREFIX) :]
            if slug:
                return slug
    return None


def _current_step(attrs: dict[str, Any]) -> int:
    """The highest rung already sent.

    Reads the counter, but falls back to scanning the per-step stamps: the
    counter is a convenience for humans reading the sidebar, and the stamps
    are the thing that must never be re-fired. If the two ever disagree, the
    stamps win.
    """
    stamped = [n for n in range(1, 10) if attrs.get(step_sent_attr(n))]
    from_stamps = max(stamped) if stamped else 0
    try:
        counter = int(attrs.get(LADDER_STEP_ATTR) or 0)
    except (TypeError, ValueError):
        counter = 0
    # Step 1 is stamped by agent/ as `escalation_notified_at`, not as
    # `escalation_step1_sent_at`, so a case that has only ever been escalated
    # sits at rung 1 with no step stamps at all.
    return max(from_stamps, counter, 1 if attrs.get(NOTIFIED_ATTR) else 0)


def _should_skip(conv: dict[str, Any], attrs: dict[str, Any]) -> str | None:
    """Why this conversation is not a ladder candidate, or None."""
    if ESCALATE_LABEL not in _labels(conv):
        return "not_escalated"
    if not attrs.get(NOTIFIED_ATTR):
        return "no_step_one"
    if attrs.get(REPLIED_ATTR):
        return "dealer_replied"
    if attrs.get(ACKNOWLEDGED_ATTR):
        return "acknowledged_by_agent"
    if str(conv.get("status") or "") == "resolved":
        return "resolved"
    return None


async def sweep_ladder(
    conversations: list[dict[str, Any]],
    *,
    settings: Settings,
    notifier: EscalationNotifier,
    dealer_store: DealerStore | None = None,
    pronet_store: ProtonNetStore | None = None,
    set_attributes: Any = None,
    now: datetime | None = None,
    inbox_cache: InboxCache | None = None,
    policy_repo: Any = None,
) -> list[dict[str, Any]]:
    """Advance every due case by exactly one rung. Returns what it did.

    Never raises: one malformed conversation must not stop the sweep for
    every other case on the tenant.
    """
    clock = now or datetime.now(UTC)
    config = await resolve_ladder_config(policy_repo, settings)
    if not config.enabled:
        # The scheduler registers unconditionally so the admin page's toggle
        # takes effect in a running process; this is where "off" is honoured.
        return []

    steps = apply_delay_overrides(
        load_steps(getattr(settings, "escalation_policy_steps_json", "") or ""),
        config.delay_overrides,
    )
    dry_run = config.dry_run
    working_hours = bool(getattr(settings, "sla_working_hours_enabled", False))
    cache = inbox_cache if inbox_cache is not None else InboxCache(None)

    acted: list[dict[str, Any]] = []
    for conv in conversations:
        outcome = await _advance_one(
            conv,
            steps=steps,
            clock=clock,
            dry_run=dry_run,
            working_hours=working_hours,
            cache=cache,
            notifier=notifier,
            dealer_store=dealer_store,
            pronet_store=pronet_store,
            set_attributes=set_attributes,
        )
        if outcome is not None:
            acted.append(outcome)

    if acted:
        _log.info("escalation_ladder_swept", count=len(acted), dry_run=dry_run)
    return acted


async def describe_in_flight(
    conversations: list[dict[str, Any]],
    *,
    settings: Settings,
    dealer_store: DealerStore | None = None,
    policy_repo: Any = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Every escalated case and what the ladder is doing with it.

    Read-only, and deliberately built on the SAME `_should_skip`,
    `_current_step` and `due_step` the sweep uses. A monitoring panel that
    reimplemented those would eventually disagree with the engine, and a
    panel that confidently shows the wrong reason a case is not climbing is
    worse than no panel -- it is the reason two live cases sat unnoticed
    until someone grepped the container logs.

    Never raises: this backs a page, and a page that 500s tells an operator
    less than a page with one row missing.
    """
    clock = now or datetime.now(UTC)
    config = await resolve_ladder_config(policy_repo, settings)
    steps = apply_delay_overrides(
        load_steps(getattr(settings, "escalation_policy_steps_json", "") or ""),
        config.delay_overrides,
    )
    working_hours = bool(getattr(settings, "sla_working_hours_enabled", False))

    rows: list[dict[str, Any]] = []
    for conv in conversations:
        conv_id = conv.get("id")
        if conv_id is None:
            continue
        attrs = _attrs(conv)
        if ESCALATE_LABEL not in _labels(conv):
            continue

        slug = _dealer_slug(conv)
        row: dict[str, Any] = {
            "conv_id": str(conv_id),
            "dealer": slug,
            "rung": _current_step(attrs) if attrs.get(NOTIFIED_ATTR) else None,
            "escalated_at": attrs.get(NOTIFIED_ATTR),
            "state": "climbing",
            "next_step_no": None,
            "next_due_in_working_hours": None,
        }

        reason = _should_skip(conv, attrs)
        if reason is not None:
            row["state"] = reason
            rows.append(row)
            continue

        if slug is None:
            row["state"] = "no_dealer"
            rows.append(row)
            continue
        if dealer_store is not None:
            try:
                if await dealer_store.get(slug) is None:
                    row["state"] = "no_dealer"
                    rows.append(row)
                    continue
            except Exception:
                row["state"] = "dealer_lookup_failed"
                rows.append(row)
                continue

        started_at = _parse(attrs.get(NOTIFIED_ATTR))
        if started_at is None:
            row["state"] = "no_step_one"
            rows.append(row)
            continue

        elapsed_hours = (
            elapsed_minutes(started_at, clock, {}, working_hours=working_hours)
            / _MINUTES_PER_HOUR
        )
        current = _current_step(attrs)
        upcoming = next(
            (s for s in sorted(steps, key=lambda s: s.step_no) if s.step_no > current), None
        )
        if upcoming is None:
            row["state"] = "ladder_complete"
        else:
            row["next_step_no"] = upcoming.step_no
            row["next_due_in_working_hours"] = round(
                max(0.0, upcoming.delay_working_hours - elapsed_hours), 2
            )
        rows.append(row)

    return rows


async def _advance_one(  # noqa: PLR0911 -- each return is one reason a case is not advanced
    conv: dict[str, Any],
    *,
    steps: tuple[EscalationStep, ...],
    clock: datetime,
    dry_run: bool,
    working_hours: bool,
    cache: Any,
    notifier: EscalationNotifier,
    dealer_store: DealerStore | None,
    pronet_store: ProtonNetStore | None,
    set_attributes: Any,
) -> dict[str, Any] | None:
    """One conversation, at most one rung. None when nothing was due."""
    conv_id = conv.get("id")
    if conv_id is None:
        return None
    ticket_id = str(conv_id)
    attrs = _attrs(conv)

    if _should_skip(conv, attrs) is not None:
        return None

    started_at = _parse(attrs.get(NOTIFIED_ATTR))
    if started_at is None:
        return None

    inbox = await cache.get(conv.get("inbox_id")) if working_hours else {}
    elapsed_hours = (
        elapsed_minutes(started_at, clock, inbox, working_hours=working_hours)
        / _MINUTES_PER_HOUR
    )

    step = due_step(steps, elapsed_hours, _current_step(attrs))
    if step is None or attrs.get(step_sent_attr(step.step_no)):
        return None

    dealer, pronet = await _resolve_contacts(conv, dealer_store, pronet_store)

    # No dealer, no ladder. This IS the dealer escalation policy: a case
    # escalated to a department PIC with no `dealer_<slug>` label has nobody
    # to climb to. Without this guard it advanced silently through every rung
    # (each one resolving to no recipients) and then raised a "call the Dealer
    # Principal" task for a dealer that does not exist -- observed on two live
    # proton cases the first time the sweep was armed.
    #
    # Returning rather than stamping also means an unreachable store, or a
    # dealer label added later, resumes the ladder properly instead of finding
    # rungs already marked sent.
    if dealer is None:
        _log.info("escalation_ladder_no_dealer", conv_id=ticket_id, step_no=step.step_no)
        return None

    to, cc = resolve_recipients(step, dealer, pronet)
    plan = {
        **describe(step, to, cc),
        "conv_id": ticket_id,
        "elapsed_working_hours": round(elapsed_hours, 2),
    }

    # Step 2 is the dealer's own acknowledgement window, not something we
    # send. Reaching it means the window closed unanswered; record the rung so
    # step 3 becomes due next sweep, and send nothing.
    if not to and step.channel != PHONE:
        _log.info("escalation_ladder_step_skipped", **plan)
        if not dry_run:
            await _stamp(set_attributes, ticket_id, step, clock)
        return {**plan, "action": "skipped"}

    if dry_run:
        _log.info("escalation_ladder_dry_run", **plan)
        return {**plan, "action": "dry_run"}

    # Stamp first: a crash here loses a reminder, the other order sends one
    # twice.
    await _stamp(set_attributes, ticket_id, step, clock)

    if step.channel == PHONE:
        await notifier.raise_phone_task(
            conv_id=ticket_id,
            step=step,
            contacts=to,
            deadline=clock + _PHONE_RESPONSE_WINDOW,
        )
        return {**plan, "action": "phone_task"}

    ok, error = notifier.send_ladder_step(
        conv_id=ticket_id,
        step=step,
        to=to,
        cc=cc,
        title=_title(conv, ticket_id),
        body=_body(ticket_id),
        elapsed_working_hours=elapsed_hours,
    )
    return {**plan, "action": "sent" if ok else "failed", "error": error}


async def _resolve_contacts(
    conv: dict[str, Any],
    dealer_store: DealerStore | None,
    pronet_store: ProtonNetStore | None,
) -> tuple[Any, Any]:
    """(dealer, pronet) for this conversation; either may be None.

    A store outage yields None rather than an exception: the rung then skips
    and logs, which is a missed reminder. Letting it raise would abort the
    sweep for every OTHER case on the tenant, which is many missed reminders.
    """
    dealer = None
    slug = _dealer_slug(conv)
    if slug and dealer_store is not None:
        try:
            dealer = await dealer_store.get(slug)
        except Exception as exc:
            _log.warning("escalation_ladder_dealer_lookup_failed", dealer=slug, error=str(exc))

    pronet = None
    if dealer is not None and dealer.region and pronet_store is not None:
        try:
            pronet = await pronet_store.get(dealer.region)
        except Exception as exc:
            _log.warning(
                "escalation_ladder_pronet_lookup_failed", region=dealer.region, error=str(exc)
            )
    return dealer, pronet


def _title(conv: dict[str, Any], ticket_id: str) -> str:
    meta = conv.get("meta")
    if isinstance(meta, dict):
        sender = meta.get("sender")
        if isinstance(sender, dict) and sender.get("name"):
            return f"Escalated case for {sender['name']} (#{ticket_id})"
    return f"Escalated case #{ticket_id}"


def _body(ticket_id: str) -> str:
    """What the reminder says the case is about.

    The conversation list payload does not carry the transcript, and fetching
    one per case would turn a sweep into N API calls. The reminder's job is
    to point at a case the recipient has already been mailed the detail of,
    so the reference is enough.
    """
    return (
        f"This case (Chatwoot conversation #{ticket_id}) was escalated to your "
        "dealership and has not been answered. The full case detail is in the "
        "original escalation email on this thread."
    )


async def _stamp(
    set_attributes: Any, ticket_id: str, step: EscalationStep, clock: datetime
) -> None:
    """Record the rung before acting on it. Best-effort but loud.

    A failed stamp means the same rung fires again next sweep, so it is
    logged at warning rather than swallowed silently -- a ladder that cannot
    write its own state is a ladder that will repeat itself.
    """
    if set_attributes is None:
        _log.warning("escalation_ladder_unstamped", conv_id=ticket_id, step_no=step.step_no)
        return
    try:
        await set_attributes(
            ticket_id,
            {
                step_sent_attr(step.step_no): clock.isoformat(),
                LADDER_STEP_ATTR: step.step_no,
            },
        )
    except Exception as exc:
        _log.warning(
            "escalation_ladder_stamp_failed",
            conv_id=ticket_id,
            step_no=step.step_no,
            error=str(exc),
        )


def run_ladder_scan_job(
    settings: Settings,
    *,
    notifier: EscalationNotifier,
    dealer_store: DealerStore | None = None,
    pronet_store: ProtonNetStore | None = None,
    set_attributes: Any = None,
    conversation_log: Any = None,
    fetch: Any = None,
    policy_repo: Any = None,
) -> None:
    """Synchronous entry point for the scheduler. Never raises.

    Resolves the policy BEFORE fetching conversations. The job is registered
    unconditionally so the admin page's Enabled toggle takes effect in a
    running process, and a disabled ladder must therefore cost one small
    query against its own table -- not a paged walk of every conversation on
    the tenant, every interval, forever.
    """
    try:
        config = asyncio.run(resolve_ladder_config(policy_repo, settings))
        if not config.enabled:
            return
        conversations = (fetch or fetch_conversations)(settings)
        asyncio.run(
            sweep_ladder(
                conversations,
                settings=settings,
                notifier=notifier,
                dealer_store=dealer_store,
                pronet_store=pronet_store,
                set_attributes=set_attributes,
                inbox_cache=InboxCache(conversation_log),
                policy_repo=policy_repo,
            )
        )
    except Exception as exc:
        _log.warning("escalation_ladder_scan_failed", error=str(exc))


def start_ladder_scheduler(
    settings: Settings,
    *,
    notifier: EscalationNotifier,
    dealer_store: DealerStore | None = None,
    pronet_store: ProtonNetStore | None = None,
    set_attributes: Any = None,
    conversation_log: Any = None,
    scheduler: Any | None = None,
    job: Any | None = None,
    policy_repo: Any = None,
) -> Any | None:
    """Start the ladder sweep. Returns the scheduler, or None when unwired.

    Mirrors `start_sla_scheduler`, including the injectable scheduler/job for
    tests, with one deliberate difference: the job is registered even when the
    ladder is disabled. Without that, the admin page's Enabled toggle could
    only take effect at the next deploy -- which is the thing the page exists
    to avoid. The job itself returns immediately while disabled, before
    fetching anything, so an unused ladder costs one query per interval.

    `policy_repo=None` (no store wired) keeps the pure-env behaviour, and in
    that case a disabled ladder is skipped here rather than every interval.
    """
    if policy_repo is None and not getattr(settings, "escalation_policy_enabled", False):
        return None

    sched = scheduler or BackgroundScheduler()
    interval = int(getattr(settings, "escalation_policy_scan_interval_seconds", 300))
    sched.add_job(
        job
        or (
            lambda: run_ladder_scan_job(
                settings,
                notifier=notifier,
                dealer_store=dealer_store,
                pronet_store=pronet_store,
                set_attributes=set_attributes,
                conversation_log=conversation_log,
                policy_repo=policy_repo,
            )
        ),
        "interval",
        seconds=interval,
        id="escalation_ladder_scan",
        replace_existing=True,
    )
    sched.start()
    _log.info(
        "escalation_ladder_scheduler_started",
        interval_seconds=interval,
        dry_run=bool(getattr(settings, "escalation_policy_dry_run", True)),
    )
    return sched
