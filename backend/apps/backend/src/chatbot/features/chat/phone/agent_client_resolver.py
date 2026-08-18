"""Stage 1 of the handoff: the conversation's ASSIGNED agent, in their browser.

The second implementation of the `resolve() -> HandoffTarget | None` interface
that `handoff_target.py`'s module docstring anticipated. `None` from `resolve()`
means "not this resolver" and is the normal, expected answer -- `ChainedResolver`
then falls through to the PSTN hunt group, so a tenant that never enables the
softphone is unaffected and a tenant that does still has the old behaviour as a
floor.

Deliberately NOT copied from `HandoffTargetResolver`: its caller-id guard
(Twilio error 13214) is a `<Number>` restriction and would wrongly disable this
resolver for any tenant without a PSTN caller id configured.

Takes a `ticket_id_provider` callable rather than a `PhoneBridge` so the phone
package has no import cycle, and so the resolver reads the bridge's CURRENT
ticket id at resolve time -- the ticket is created by a detached task at call
start and may not exist yet when the resolver is constructed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from chatbot.features.chat.phone.agent_token import agent_identity
from chatbot.features.chat.phone.handoff_target import HandoffTarget

if TYPE_CHECKING:
    from chatbot.features.chat.phone.softphone_registry import SoftphoneRegistry
    from chatbot.features.chat.ports import ConversationLogPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class _Resolver(Protocol):
    async def resolve(self) -> HandoffTarget | None: ...


class AgentClientResolver:
    def __init__(
        self,
        settings: Settings,
        log_port: ConversationLogPort,
        registry: SoftphoneRegistry,
        ticket_id_provider: Callable[[], str | None],
    ) -> None:
        self._settings = settings
        self._log_port = log_port
        self._registry = registry
        self._ticket_id_provider = ticket_id_provider
        # Warmed by prefetch(); None = cold, in which case resolve() does the
        # lookups inline (bounded by the caller's asyncio.wait_for).
        self._target: HandoffTarget | None = None
        self._warm = False

    async def prefetch(self) -> None:
        """Warm the answer off the audio path. Fire-and-forget from call
        start; never raises.

        A failed lookup (Chatwoot/registry error) is deliberately NOT cached:
        `_warm` stays False so `resolve()` retries live instead of being
        stuck replaying a transient failure for the rest of the call.
        """
        try:
            self._target = await self._lookup()
            self._warm = True
        except Exception as e:
            _log.error("agent_client_prefetch_failed", error=str(e))

    async def resolve(self) -> HandoffTarget | None:
        if not self._settings.phone_agent_softphone_enabled:
            return None
        if self._warm:
            return self._target
        try:
            return await self._lookup()
        except Exception as e:  # never raise into the audio pump
            _log.error("agent_client_lookup_failed", error=str(e))
            return None

    async def _lookup(self) -> HandoffTarget | None:
        """Returns None for every legitimate "not this resolver" outcome
        (no ticket yet, no assignee, non-numeric assignee, assignee not
        registered). RAISES for actual I/O failures (Chatwoot/registry
        errors) so callers can tell "no answer" from "couldn't find out" --
        the former is cacheable by `prefetch()`, the latter is not.
        """
        if not self._settings.phone_agent_softphone_enabled:
            return None
        ticket_id = self._ticket_id_provider()
        if not ticket_id:
            # The call-start create failed, or chatwoot_enabled is False.
            # Returning here costs zero round trips -- see the test.
            return None
        try:
            assignee = await self._log_port.get_conversation_assignee(ticket_id)
        except Exception as e:
            _log.error("agent_client_assignee_lookup_failed", ticket_id=ticket_id, error=str(e))
            raise
        if not assignee:
            return None
        agent_id = _as_agent_id(assignee)
        if agent_id is None:
            _log.warning("agent_client_assignee_not_numeric", assignee=str(assignee))
            return None
        try:
            registered = await self._registry.registered_ids()
        except Exception as e:
            _log.error("agent_client_registry_failed", error=str(e))
            raise
        if agent_id not in registered:
            _log.info("agent_client_assignee_not_registered", agent_id=agent_id)
            return None
        return HandoffTarget(kind="client", value=agent_identity(agent_id))


def _as_agent_id(assignee: Any) -> int | None:
    try:
        return int(str(assignee).strip())
    except (TypeError, ValueError):
        return None


class ChainedResolver:
    """Try each resolver in order; first non-None wins.

    A raising resolver is skipped rather than allowed to propagate: one broken
    resolver must not deny the caller the fallback sitting behind it. This runs
    inline in the audio pump.
    """

    def __init__(self, resolvers: list[_Resolver]) -> None:
        self._resolvers = resolvers

    async def prefetch(self) -> None:
        for resolver in self._resolvers:
            prefetch = getattr(resolver, "prefetch", None)
            if prefetch is None:
                continue
            try:
                await prefetch()
            except Exception as e:  # pragma: no cover -- prefetches never raise
                _log.error("chained_resolver_prefetch_failed", error=str(e))

    async def resolve(self) -> HandoffTarget | None:
        for resolver in self._resolvers:
            try:
                target = await resolver.resolve()
            except Exception as e:
                _log.error("chained_resolver_failed", error=str(e))
                continue
            if target is not None:
                return target
        return None
