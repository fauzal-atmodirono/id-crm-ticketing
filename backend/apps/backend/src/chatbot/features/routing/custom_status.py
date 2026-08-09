"""P6 task 2 -- custom statuses that mirror onto Chatwoot's native enum.

Chatwoot's own `availability_status` is a fixed three-value enum
(`online`/`busy`/`offline`) with no extension point. Requirement 4.17 wants
eight named statuses (Available, Busy, Lunch, Break, Coaching, Training,
Toilet, Prayer). Rather than fork Chatwoot's enum, a `CustomStatus` *mirrors*
onto one of the three native values: picking "Lunch" sets the agent's real
Chatwoot status to `busy` **and** appends a presence event recording "lunch"
specifically. Chatwoot's own UI keeps working unmodified and shows `busy`;
our surfaces (dashboard, alerts) show "Lunch".

Two behaviours here are load-bearing:

**The native write happens first; the event is appended only on success.**
`set_status` writes to Chatwoot, then -- and only if that succeeds -- appends
a `PresenceEvent`. An event claiming an agent is on lunch while Chatwoot
still shows them online would make the dashboard and the router disagree,
which is exactly the class of defect this package exists to prevent. See
`test_a_native_status_write_failure_does_not_append_a_misleading_event`.

**The native status, not this catalogue, remains the real routing gate.**
`pick_agent` filters on Chatwoot's native `availability_status` regardless of
whether this store is reachable. This catalogue only ever adds *extra*
information on top of that (via `routable`/`counts_as_unavailable`) -- it
never replaces the native filter. That is why `get()` must fail open to
`None` on both an unknown key and a store outage, and why callers must treat
`None` as "no extra information available", not as "not routable" (which
would silently halt all routing on an outage) and not as "routable" (which
would silently let an agent parked in an unroutable custom status keep
receiving new chats). See `get()`'s docstring.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from google.cloud import firestore

from chatbot.features.routing.presence_store import (
    PresenceEvent,
    build_presence_event_store,
)

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_COLLECTION = "custom_statuses"


@dataclass(frozen=True)
class CustomStatus:
    key: str  # "lunch"
    label: str  # "Lunch"
    color: str  # "#e8a33d"
    routable: bool  # eligible for new assignments
    native: str  # the Chatwoot status to mirror: online|busy|offline
    counts_as_unavailable: bool  # feeds the 10-min / 1-h thresholds


# The eight §4.17 names, plus a ninth (`acw`) needed by the concurrently
# built After-Call-Work task. `native`/`routable` follow directly from what
# each status means for routing: only "Available" should ever receive a new
# assignment, so it is the sole `routable=True` entry and the sole one
# mirrored to `online`; everything else mirrors to `busy` (still "not idle"
# from Chatwoot's point of view) except -- there is no custom mirror to
# `offline`; an agent who is truly offline just uses Chatwoot's own control.
#
# `counts_as_unavailable` is a SEPARATE axis from `routable`, not a restating
# of it -- this is the distinction the whole threshold-alert design (task 4)
# rests on:
#   - Lunch/Break/Toilet/Prayer: the agent is away from their desk on their
#     own initiative and cannot be relied on to return within a normal
#     working cadence. These are exactly the statuses the 10-minute/1-hour
#     absence alerts exist to catch, so `counts_as_unavailable=True`.
#   - Coaching/Training: also not routable, but this is scheduled,
#     supervisor-visible working time. An unannounced "agent is missing"
#     alert about a coaching session a supervisor booked would be pure
#     noise, so `counts_as_unavailable=False` even though `routable=False`.
#   - Busy: the agent is mid-conversation -- working, not absent -- so it is
#     `routable=False` (don't pile on a new chat) but `counts_as_unavailable
#     =False` (nothing to alert about).
#   - `acw` (After-Call-Work): entered automatically by task 5 when a phone
#     call ends, never chosen by the agent. Short, expected wrap-up work, so
#     it follows the same reasoning as Coaching/Training:
#     `counts_as_unavailable=False`.
SEED_STATUSES: tuple[CustomStatus, ...] = (
    CustomStatus(
        key="available",
        label="Available",
        color="#2ecc71",
        routable=True,
        native="online",
        counts_as_unavailable=False,
    ),
    CustomStatus(
        key="busy",
        label="Busy",
        color="#e74c3c",
        routable=False,
        native="busy",
        counts_as_unavailable=False,
    ),
    CustomStatus(
        key="lunch",
        label="Lunch",
        color="#e8a33d",
        routable=False,
        native="busy",
        counts_as_unavailable=True,
    ),
    CustomStatus(
        key="break",
        label="Break",
        color="#f39c12",
        routable=False,
        native="busy",
        counts_as_unavailable=True,
    ),
    CustomStatus(
        key="toilet",
        label="Toilet",
        color="#95a5a6",
        routable=False,
        native="busy",
        counts_as_unavailable=True,
    ),
    CustomStatus(
        key="prayer",
        label="Prayer",
        color="#8e44ad",
        routable=False,
        native="busy",
        counts_as_unavailable=True,
    ),
    CustomStatus(
        key="coaching",
        label="Coaching",
        color="#3498db",
        routable=False,
        native="busy",
        counts_as_unavailable=False,
    ),
    CustomStatus(
        key="training",
        label="Training",
        color="#2980b9",
        routable=False,
        native="busy",
        counts_as_unavailable=False,
    ),
    CustomStatus(
        key="acw",
        label="After-Call Work",
        color="#9b59b6",
        routable=False,
        native="busy",
        counts_as_unavailable=False,
    ),
)


class _PresenceSink(Protocol):
    """The two `PresenceEventStore` methods `set_status` depends on.

    Kept as a narrow structural protocol (rather than importing the concrete
    class as the parameter type) so tests can inject a lightweight fake
    without subclassing `PresenceEventStore` or mocking Firestore for it.
    """

    async def latest(self, agent_id: int) -> PresenceEvent | None: ...

    async def append(self, event: PresenceEvent) -> None: ...


class _AvailabilityWriter(Protocol):
    async def set_availability(self, agent_id: int, native: str) -> bool: ...


class ChatwootAvailabilityWriter:
    """Writes an agent's availability directly to Chatwoot's account API.

    Follows `PresenceFetcher`'s exact plumbing (same package, same
    conventions): dual auth headers (``api_access_token`` and
    ``Api-Access-Token``), a fresh ``httpx.AsyncClient`` per call, a deferred
    ``import httpx`` inside the method to avoid a circular import between the
    routing package and the chat adapter package, and ``_request`` swallowing
    every error and returning ``None`` on failure.

    **UNVERIFIED against a live Chatwoot instance.** The endpoint used here
    (``PATCH /api/v1/accounts/{account_id}/agents/{agent_id}`` with body
    ``{"availability": "<online|busy|offline>"}``) is inferred from
    Chatwoot's account-scoped agent API convention -- the same base URL
    `PresenceFetcher.fetch_agents` already calls successfully -- but this
    exact write has not been exercised against a real deployment. If native
    status mirroring misbehaves in staging (an agent's status not actually
    changing in Chatwoot, or a 404/422 in the logs), this request shape is
    the first thing to check.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _base(self) -> str:
        return (
            f"{self._settings.chatwoot_api_url.rstrip('/')}"
            f"/api/v1/accounts/{self._settings.chatwoot_account_id}"
        )

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        # Deferred import avoids a circular dependency between the routing
        # package and the chat adapter package -- same reasoning as
        # PresenceFetcher._request.
        import httpx  # noqa: PLC0415

        token = self._settings.chatwoot_api_token
        headers = {
            "Content-Type": "application/json",
            "api_access_token": token,
            "Api-Access-Token": token,
        }
        url = f"{self._base()}{path}"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.request(method, url, json=payload, headers=headers, timeout=10.0)
                res.raise_for_status()
                return res.json() if res.content else {}
        except Exception as e:
            _log.error(
                "custom_status_chatwoot_request_failed", method=method, path=path, error=str(e)
            )
            return None

    async def set_availability(self, agent_id: int, native: str) -> bool:
        """PATCH the agent's availability on the Chatwoot account.

        Returns a plain bool -- not the response body, not `None` -- because
        `set_status` needs a clean success/failure signal to decide whether
        appending a presence event is safe. `_request` already collapses
        every failure mode (network error, non-2xx, timeout) to `None`, so
        `True` here means "Chatwoot accepted the write" and `False` means
        every other case, including "we could not tell".
        """
        res = await self._request("PATCH", f"/agents/{agent_id}", {"availability": native})
        return res is not None


class CustomStatusStore:
    """Firestore-backed catalogue of `CustomStatus` entries, plus the one
    write path (`set_status`) that mirrors a custom status onto Chatwoot's
    native enum and records the transition.

    One document per status (keyed by `CustomStatus.key`) in the
    ``custom_statuses`` collection -- the same one-document-per-key,
    ``asyncio.to_thread``, fail-open-on-every-read shape as `TargetsStore`
    and `PicStore`/`DealerStore`. Being a store rather than an enum is the
    whole point: adding a new status is a document write, not a code change
    or a deploy (`test_an_operator_can_add_a_ninth_status_without_a_deploy`).
    """

    def __init__(
        self,
        settings: Settings,
        presence_store: _PresenceSink | None = None,
        availability_writer: _AvailabilityWriter | None = None,
    ) -> None:
        self._settings = settings
        self._presence_store: _PresenceSink = presence_store or build_presence_event_store(settings)
        self._availability_writer: _AvailabilityWriter = (
            availability_writer or ChatwootAvailabilityWriter(settings)
        )

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _collection(self) -> firestore.CollectionReference:
        return self._client().collection(_COLLECTION)

    def _doc(self, key: str) -> firestore.DocumentReference:
        # The installed firestore stub types `.document()` as returning the
        # base `BaseDocumentReference`; the concrete client always returns
        # the sync `DocumentReference` subtype at runtime (same stub gap
        # `TargetsStore._doc` has -- this isn't specific to this file).
        return self._collection().document(key)  # type: ignore[return-value]

    async def get(self, key: str) -> CustomStatus | None:
        """Look up one status by key.

        Returns `None` for BOTH an unknown key AND a Firestore outage --
        fail-open matters more here than in most stores. `None` must always
        mean "we know nothing extra about routability" to a caller (e.g.
        task 6's fair-share selection): the caller MUST fall back to
        filtering on the native Chatwoot `availability_status` alone.
        Treating `None` as "not routable" would let a store outage silently
        stop all routing; treating it as "routable" would let an agent
        parked in an unroutable custom status keep receiving new chats
        whenever the store happened to be unreachable. Neither is
        acceptable -- the native status is the real gate, this catalogue
        only ever adds information on top of it.
        """
        try:
            snap = await asyncio.to_thread(self._doc(key).get)
            if not snap.exists:
                return None
            return CustomStatus(**(snap.to_dict() or {}))
        except Exception as e:
            _log.error("custom_status_get_failed", key=key, error=str(e))
            return None

    async def list_all(self) -> list[CustomStatus]:
        """The full catalogue, for admin surfaces. `[]` on any outage."""
        try:
            client = self._client()
            snaps = await asyncio.to_thread(lambda: list(client.collection(_COLLECTION).stream()))
            out: list[CustomStatus] = []
            for snap in snaps:
                try:
                    out.append(CustomStatus(**(snap.to_dict() or {})))
                except TypeError:
                    # A document written by a newer/older build. Skipping
                    # one row beats failing the whole catalogue read.
                    _log.warning("custom_status_unreadable_document", doc=snap.id)
            return out
        except Exception as e:
            _log.error("custom_status_list_failed", error=str(e))
            return []

    async def add(self, status: CustomStatus) -> None:
        """Create or overwrite a status document -- the operator-facing
        add/edit path. Unlike `seed`, this always writes: this is what makes
        the catalogue a store rather than an enum, per the class docstring.
        """
        try:
            await asyncio.to_thread(self._doc(status.key).set, asdict(status))
        except Exception as e:
            _log.error("custom_status_add_failed", key=status.key, error=str(e))

    async def seed(self) -> int:
        """Seed `SEED_STATUSES` into the catalogue. Create-only, mirroring
        `TargetsStore.seed_from_settings`: an operator who edited a seeded
        status (re-tinted "Lunch", flipped a flag) must not have that edit
        silently reverted on the next restart, which is what makes it safe
        to call this unconditionally at startup.

        Returns how many statuses were newly created.
        """
        created = 0
        for status in SEED_STATUSES:
            if await self.get(status.key) is not None:
                continue
            await self.add(status)
            created += 1
        return created

    async def set_status(
        self,
        agent_id: int,
        key: str,
        *,
        source: str = "agent",
        now: datetime | None = None,
    ) -> bool:
        """Set `agent_id`'s status to the custom status named `key`.

        Writes the native Chatwoot status FIRST; the presence event is
        appended only if that write succeeds. This ordering is the single
        most important thing about this method: an event claiming an agent
        is on lunch while Chatwoot still shows them online would make the
        dashboard and the router disagree, and since `pick_agent` reads the
        native status (not this event log), a misleading event with no
        matching native change is worse than no event at all.

        Returns `False` -- and appends nothing -- when `key` is not in the
        catalogue (including when the catalogue can't be read at all; see
        `get()`) or when the native Chatwoot write fails. Returns `True`
        once both steps have completed.

        `now` defaults to the wall clock; pass an explicit value in tests
        for determinism, matching `PresenceEventStore`'s convention of never
        reading the clock itself where the caller can supply it.
        """
        status = await self.get(key)
        if status is None:
            _log.warning("custom_status_set_unknown_key", agent_id=agent_id, key=key)
            return False

        wrote = await self._availability_writer.set_availability(agent_id, status.native)
        if not wrote:
            _log.error("custom_status_native_write_failed", agent_id=agent_id, key=key)
            return False

        previous_event = await self._presence_store.latest(agent_id)
        previous = previous_event.status if previous_event is not None else None

        await self._presence_store.append(
            PresenceEvent(
                agent_id=agent_id,
                status=key,
                at=now or datetime.now(UTC),
                source=source,
                previous=previous,
            )
        )
        return True


def build_custom_status_store(settings: Settings) -> CustomStatusStore:
    return CustomStatusStore(settings)
