"""P9 task 1 -- the alert-rule store: who gets alerted about what, how loudly.

This package exists because every alerting primitive already works (the
`my-tasks` app's beep, desktop notification and toast) and is wired to the
wrong event. Once the fork subscribes those primitives to a wider event set
(task 2/3), *something* has to decide which of `sound`/`desktop`/`toast`
fires for which event, for which agent -- an operator-editable rule set
rather than a hard-coded wiring, per §3.1.1/§4.2's "configurable alerts".

Two account-scoped documents wrap one per-agent-overridable rule per event:
an account-level default (operator-editable, `set_account_rule`) and,
layered on top of it, a per-agent override (`set_agent_override`). Tolerance
for interruption genuinely varies by person, and forcing one account-wide
setting on everyone produces a workaround (an agent muting the tab, or a
Chrome notification filter) that this store cannot see or report on.

**`new_inbound` defaults to toast-only. This is a design assertion, not a
detail an "improvement" PR should touch.** Sound and desktop notification
are available for `new_inbound` and are fully configurable per agent -- the
capability is not withheld. But on a tenant where WhatsApp carries most of
the contact volume, an audible alert on every inbound message is a beep
every few seconds, and the very first thing every agent on that shift does
is disable *all* alerting, including `sla_breach`, which is the one alert
in this table that actually has to fire. A quiet, useful default that an
agent chooses to turn up beats a loud default nobody keeps turned on. See
`test_new_inbound_defaults_to_toast_only`, which is written to fail loudly
if this default is ever "fixed" to include sound.

**A store outage must never produce silence, and must never produce
"everything on" either.** Both are wrong in the same way an alerting system
can be wrong: silence hides an SLA breach an agent needed to see, and
"everything on" is indistinguishable, from the agent's chair, from every
account default having been reset to maximum volume by a Firestore hiccup
-- which is exactly the kind of surprise that gets a whole alerting feature
switched off. `resolve()` therefore degrades to `BUILT_IN_DEFAULTS` -- the
same six rules this module ships and seeds -- on ANY read failure at ANY
layer (account rule, per-agent override, or both). It does this for free:
`get_account_rule`/`get_agent_override` already fail open to `None` on an
exception (matching `TargetsStore`/`CustomStatusStore`'s convention), and
`resolve()` already treats "no override" and "no outage-free answer" as the
same shape, so a real outage and a tenant that has simply never customised
its rules take the identical code path to the identical, useful answer.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_COLLECTION = "alert_rules"

# The six events §3.1.1/§3.1.7/§4.2/§4.79 name. A `resolve()` call for
# anything outside this set is treated as unknown -- see `resolve()`.
EVENTS: tuple[str, ...] = (
    "assigned_to_me",
    "new_inbound",
    "sla_warn",
    "sla_breach",
    "escalated",
    "anomaly",
)

SCOPES = frozenset({"mine", "my_inbox", "my_team", "all"})
MODALITIES = frozenset({"sound", "desktop", "toast"})


class InvalidAlertRule(ValueError):
    """A rule the alert surface could not act on. Message is operator-facing."""


@dataclass(frozen=True)
class AlertRule:
    event: str
    scope: str
    modalities: tuple[str, ...]
    enabled: bool = True

    def validate(self) -> AlertRule:
        if self.event not in EVENTS:
            raise InvalidAlertRule(f"event must be one of {', '.join(EVENTS)}; got {self.event!r}.")
        if self.scope not in SCOPES:
            raise InvalidAlertRule(
                f"scope must be one of {', '.join(sorted(SCOPES))}; got {self.scope!r}."
            )
        unknown = set(self.modalities) - MODALITIES
        if unknown:
            raise InvalidAlertRule(
                f"modalities must be a subset of {', '.join(sorted(MODALITIES))}; "
                f"got unknown value(s) {', '.join(sorted(unknown))}."
            )
        return self


# The design table (spec §3.3), verbatim. `new_inbound` is toast-only -- see
# the module docstring. This dict is both the fallback `resolve()` degrades
# to on any outage AND the source `seed()` writes into Firestore, so the two
# can never quietly drift apart.
BUILT_IN_DEFAULTS: dict[str, AlertRule] = {
    "assigned_to_me": AlertRule(
        event="assigned_to_me", scope="mine", modalities=("sound", "desktop", "toast")
    ),
    "new_inbound": AlertRule(event="new_inbound", scope="my_inbox", modalities=("toast",)),
    "sla_warn": AlertRule(event="sla_warn", scope="mine", modalities=("toast",)),
    "sla_breach": AlertRule(
        event="sla_breach", scope="mine", modalities=("sound", "desktop", "toast")
    ),
    "escalated": AlertRule(event="escalated", scope="my_team", modalities=("toast",)),
    "anomaly": AlertRule(event="anomaly", scope="all", modalities=("desktop",)),
}


# The answer for an unknown event and for any rule resolved with
# enabled=False: no modality fires. A dedicated constructor rather than a
# module-level singleton because `event`/`scope` still carry the caller's
# input through, for logging -- see `resolve()`.
def _silent(event: str, scope: str = "none") -> AlertRule:
    return AlertRule(event=event, scope=scope, modalities=(), enabled=False)


def _account_doc_id(event: str) -> str:
    return f"account::{event}"


def _agent_doc_id(agent_id: int, event: str) -> str:
    return f"agent::{agent_id}::{event}"


def _from_dict(data: dict[str, Any]) -> AlertRule:
    return AlertRule(
        event=data["event"],
        scope=data["scope"],
        modalities=tuple(data.get("modalities", ())),
        enabled=bool(data.get("enabled", True)),
    )


class AlertRuleStore:
    """Firestore-backed alert rules: one account-level default per event,
    plus an optional per-agent override layered on top of it.

    Follows the established store shape (`TargetsStore`, `CustomStatusStore`):
    one document per key, `asyncio.to_thread` around every blocking Firestore
    call, and fail-open reads. `resolve()` is the one read path every
    consumer outside this module should use; `get_account_rule`/
    `get_agent_override` exist for the admin/preferences router (task 6) to
    show what is actually stored, as distinct from what an agent will see
    fire, the same distinction `status_router._effective_catalogue` draws
    between a stored document and a shipped default.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _collection(self) -> firestore.CollectionReference:
        return self._client().collection(_COLLECTION)

    def _doc(self, doc_id: str) -> firestore.DocumentReference:
        # Same stub gap `TargetsStore._doc`/`CustomStatusStore._doc` note:
        # the installed firestore stub types `.document()` as returning the
        # base `BaseDocumentReference`; the concrete client always returns
        # the sync `DocumentReference` subtype at runtime.
        return self._collection().document(doc_id)  # type: ignore[return-value]

    async def _read(self, doc_id: str) -> AlertRule | None:
        """The stored document for `doc_id`, or `None` if there isn't one.

        Raises on a Firestore failure, unlike every public read on this
        store -- callers (`get_account_rule`/`get_agent_override`/`seed`)
        decide their own fail-open behaviour from here, the same split
        `CustomStatusStore._stored` draws from `CustomStatusStore.get`.
        """
        snap = await asyncio.to_thread(self._doc(doc_id).get)
        if not snap.exists:
            return None
        return _from_dict(snap.to_dict() or {})

    async def get_account_rule(self, event: str) -> AlertRule | None:
        """The stored account-level rule for `event`.

        `None` for both "never configured" and "could not tell" (a Firestore
        outage) -- fail-open, matching every other store's `get()`. Callers
        that need a real answer regardless of which of those two happened
        should call `resolve()`, not this.
        """
        try:
            return await self._read(_account_doc_id(event))
        except Exception as e:
            _log.error("alert_rules_get_account_failed", alert_event=event, error=str(e))
            return None

    async def get_agent_override(self, agent_id: int, event: str) -> AlertRule | None:
        """The stored per-agent override for `agent_id`/`event`, or `None`
        for "no override" or "could not tell" -- see `get_account_rule`."""
        try:
            return await self._read(_agent_doc_id(agent_id, event))
        except Exception as e:
            _log.error(
                "alert_rules_get_override_failed",
                agent_id=agent_id,
                alert_event=event,
                error=str(e),
            )
            return None

    async def set_account_rule(self, rule: AlertRule) -> bool:
        """Create or overwrite the account-level default for `rule.event`.

        Returns whether the write landed -- the router must not answer 200
        for an edit Firestore refused (same reasoning as
        `CustomStatusStore.add`'s docstring).
        """
        rule.validate()
        try:
            await asyncio.to_thread(self._doc(_account_doc_id(rule.event)).set, asdict(rule))
        except Exception as e:
            _log.error("alert_rules_set_account_failed", alert_event=rule.event, error=str(e))
            return False
        return True

    async def set_agent_override(self, agent_id: int, rule: AlertRule) -> bool:
        """Create or overwrite `agent_id`'s override for `rule.event`."""
        rule.validate()
        try:
            await asyncio.to_thread(
                self._doc(_agent_doc_id(agent_id, rule.event)).set, asdict(rule)
            )
        except Exception as e:
            _log.error(
                "alert_rules_set_override_failed",
                agent_id=agent_id,
                alert_event=rule.event,
                error=str(e),
            )
            return False
        return True

    async def clear_agent_override(self, agent_id: int, event: str) -> bool:
        """Delete `agent_id`'s override for `event`, reverting them to
        whatever the account default resolves to. The preferences router's
        "reset to default" action -- without it, an agent who tries a louder
        setting and dislikes it has no way back except guessing the account
        default and re-entering it by hand.
        """
        try:
            await asyncio.to_thread(self._doc(_agent_doc_id(agent_id, event)).delete)
        except Exception as e:
            _log.error(
                "alert_rules_clear_override_failed",
                agent_id=agent_id,
                alert_event=event,
                error=str(e),
            )
            return False
        return True

    async def resolve(self, agent_id: int, event: str) -> AlertRule:
        """What should actually fire for `agent_id` on `event`, right now.

        Resolution order: the agent's own override, else the account
        default, else `BUILT_IN_DEFAULTS`. A rule resolved with
        `enabled=False` at any of those layers collapses to `_silent()`
        (empty modalities) rather than being returned with its configured
        modalities intact -- a caller that only checked `modalities` and
        forgot to check `enabled` must not alert anyway.

        An `event` outside `EVENTS` resolves to `_silent()` immediately:
        there is no built-in default for a thing that does not exist, and
        alerting on an unrecognised event is worse than not alerting on it.

        **Never raises, and never returns "everything on".** Both
        `get_agent_override` and `get_account_rule` already fail open to
        `None` on any Firestore error, so a total outage takes the exact
        same branch as "this agent/account has never customised this
        event" -- straight to `BUILT_IN_DEFAULTS[event]`. See the module
        docstring for why that -- not silence, not maximum volume -- is the
        only acceptable outage behaviour for an alerting store.
        """
        if event not in BUILT_IN_DEFAULTS:
            return _silent(event)

        override = await self.get_agent_override(agent_id, event)
        if override is not None:
            winning = override
        else:
            account = await self.get_account_rule(event)
            winning = account if account is not None else BUILT_IN_DEFAULTS[event]

        if not winning.enabled:
            return _silent(event, winning.scope)
        return winning

    async def list_account_rules(self) -> dict[str, AlertRule]:
        """The effective account-level rule for every known event: a stored
        document wins, `BUILT_IN_DEFAULTS` fills any event never configured.
        For the admin defaults page -- mirrors the resolution order
        `resolve()` itself uses, so the page an operator reads and the rule
        that will actually fire cannot disagree.
        """
        out: dict[str, AlertRule] = {}
        for event in EVENTS:
            stored = await self.get_account_rule(event)
            out[event] = stored if stored is not None else BUILT_IN_DEFAULTS[event]
        return out

    async def list_agent_overrides(self, agent_id: int) -> dict[str, AlertRule]:
        """Only the overrides `agent_id` has actually set -- events with no
        entry here are inheriting the account default. Distinct from
        `resolve()` for every event, which the preferences page also needs
        (to show what will actually fire), but this is what lets it show
        which rows are an override versus inherited.
        """
        out: dict[str, AlertRule] = {}
        for event in EVENTS:
            override = await self.get_agent_override(agent_id, event)
            if override is not None:
                out[event] = override
        return out

    async def seed(self) -> int:
        """Seed `BUILT_IN_DEFAULTS` into Firestore as account-level rules.

        Create-only, mirroring `CustomStatusStore.seed`/
        `TargetsStore.seed_from_settings`: an operator who has already
        tightened `sla_breach` or loosened `new_inbound` for their account
        must not have that edit silently reverted on the next restart.
        Checks `_read` directly rather than `get_account_rule`, because a
        Firestore read failure during seeding must be skipped (retried on
        the next startup), never treated as "absent" -- treating it as
        absent would let a transient outage overwrite an operator's stored
        default with the built-in one.

        Returns how many rules were newly created.
        """
        created = 0
        for event, rule in BUILT_IN_DEFAULTS.items():
            try:
                existing = await self._read(_account_doc_id(event))
            except Exception as e:
                _log.error("alert_rules_seed_read_failed", alert_event=event, error=str(e))
                continue
            if existing is not None:
                continue
            if await self.set_account_rule(rule):
                created += 1
        return created


def build_alert_rule_store(settings: Settings) -> AlertRuleStore:
    return AlertRuleStore(settings)
