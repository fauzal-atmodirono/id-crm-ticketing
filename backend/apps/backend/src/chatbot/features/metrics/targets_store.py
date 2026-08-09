"""Operator-editable targets for the control-item slide.

Mirrors `PicStore`/`DealerStore`/`SlaPolicyRepository`: one document per key,
`asyncio.to_thread` for I/O, fail-open on every read.

Two behaviours are load-bearing and easy to get wrong:

**An unknown key resolves to `None`, never to a zero target.** A `Target(value=0)`
would make every unconfigured metric render as "missed by everything" -- the
most alarming possible slide, produced entirely by absence of configuration.
`None` becomes `no_target` in `evaluate()`, which is the truth.

**Seeding never overwrites an operator edit.** `RESOLUTION_SLA_TARGETS_JSON`
seeds the store; it does not compete with it. An operator who tightens the
complaint target must not have it silently reverted on the next restart.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_COLLECTION = "report_targets"

# Units the slide knows how to render. Rejected at WRITE time rather than
# silently stored: a target in an unknown unit is a number the slide will
# print with the wrong label, which is worse than a rejected edit.
SUPPORTED_UNITS = frozenset(
    {"minutes", "working_minutes", "hours", "working_hours", "days", "working_days",
     "percent", "count", "score"}
)

SUPPORTED_COMPARATORS = frozenset({"lte", "gte"})


class InvalidTarget(ValueError):
    """A target the slide could not render. Message is shown to the operator."""


@dataclass(frozen=True)
class Target:
    key: str
    comparator: str
    value: float
    unit: str
    # "90% of cases within 2 hours": `value` is the threshold, this is the
    # percentage of cases that must meet it. None means compare the raw value.
    attainment_pct: float | None = None
    # "" = tenant-wide. A scoped target beats the tenant-wide one.
    scope: str = ""

    def validate(self) -> Target:
        if self.comparator not in SUPPORTED_COMPARATORS:
            raise InvalidTarget(
                f"comparator must be one of {', '.join(sorted(SUPPORTED_COMPARATORS))}; "
                f"got {self.comparator!r}."
            )
        if self.unit not in SUPPORTED_UNITS:
            raise InvalidTarget(
                f"unit {self.unit!r} is not one the report can render. "
                f"Supported: {', '.join(sorted(SUPPORTED_UNITS))}."
            )
        return self


def _doc_id(key: str, scope: str) -> str:
    return f"{key}::{scope}" if scope else key


class TargetsStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _doc(self, key: str, scope: str) -> firestore.DocumentReference:
        return self._client().collection(_COLLECTION).document(_doc_id(key, scope))

    async def get(self, key: str, scope: str = "") -> Target | None:
        try:
            snap = await asyncio.to_thread(self._doc(key, scope).get)
            if not snap.exists:
                return None
            return Target(**(snap.to_dict() or {}))
        except Exception as e:
            _log.error("targets_store_get_failed", key=key, scope=scope, error=str(e))
            return None

    async def resolve(self, key: str, scope: str = "") -> Target | None:
        """The most specific target for this key, or None.

        Scoped beats tenant-wide. None -- never a zero target -- when nothing
        is configured, so `evaluate()` reports `no_target` instead of a miss.
        """
        if scope:
            scoped = await self.get(key, scope)
            if scoped is not None:
                return scoped
        return await self.get(key, "")

    async def set(self, target: Target) -> None:
        target.validate()
        try:
            await asyncio.to_thread(
                self._doc(target.key, target.scope).set, asdict(target)
            )
        except Exception as e:
            _log.error("targets_store_set_failed", key=target.key, error=str(e))

    async def list_all(self) -> list[Target]:
        try:
            client = self._client()
            snaps = await asyncio.to_thread(
                lambda: list(client.collection(_COLLECTION).stream())
            )
            out: list[Target] = []
            for snap in snaps:
                try:
                    out.append(Target(**(snap.to_dict() or {})))
                except TypeError:
                    # A document written by a newer build. Skipping one row
                    # beats failing the whole admin page.
                    _log.warning("targets_store_unreadable_document", doc=snap.id)
            return out
        except Exception as e:
            _log.error("targets_store_list_failed", error=str(e))
            return []

    async def seed_from_settings(self) -> int:
        """Seed items 7 and 8 from RESOLUTION_SLA_TARGETS_JSON.

        Returns how many targets were CREATED. Existing keys are left exactly
        as they are -- an operator who tightened a target must not have it
        reverted on the next restart, which is what makes this safe to call
        unconditionally at startup.
        """
        raw = (self._settings.resolution_sla_targets_json or "").strip()
        if not raw:
            return 0
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            _log.warning("targets_seed_unparseable")
            return 0
        if not isinstance(data, dict):
            return 0

        created = 0
        for case_type, spec in data.items():
            if not isinstance(spec, dict):
                continue
            edges = spec.get("buckets_wh")
            if not isinstance(edges, list) or not edges:
                continue
            key = f"resolution_{str(case_type).lower()}"
            if await self.get(key, "") is not None:
                continue  # operator-owned from here on
            try:
                await self.set(
                    Target(
                        key=key,
                        comparator="lte",
                        value=float(edges[0]),
                        unit="working_hours",
                    )
                )
                created += 1
            except (InvalidTarget, TypeError, ValueError):
                _log.warning("targets_seed_skipped_entry", case_type=case_type)
        return created


def build_targets_store(settings: Settings) -> TargetsStore:
    return TargetsStore(settings)
