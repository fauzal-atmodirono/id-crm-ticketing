"""Per-assistant Scenario (playbook) store.

Scenarios are named, described instruction blocks injected into the copilot
system prompt as conditional playbooks.  They mirror
``enterprise/app/models/captain/scenario.rb`` (V1 prompt-injection shape).

Two implementations follow the same Port + InMemory + Firestore pattern as
``live_faq.py`` and ``tools_store.py``:

- ``InMemoryScenariosStore``: dict-backed, for tests and local dev.
- ``FirestoreScenariosStore``: Firestore collection ``scenarios``, one doc per
  scenario (keyed by ``id``), with an ``assistant_id`` field for filtering.
  Sync SDK calls run in ``asyncio.to_thread``.  Every read path degrades cleanly
  to empty on failure so the copilot prompt-build never breaks.

``build_scenarios_store(settings)`` picks InMemory when
``firestore_project_id`` is unset, Firestore otherwise.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    """A single playbook attached to an assistant."""

    id: str
    assistant_id: str
    title: str
    description: str
    instruction: str
    tools: list[str] = field(default_factory=list)
    enabled: bool = True
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "assistant_id": self.assistant_id,
            "title": self.title,
            "description": self.description,
            "instruction": self.instruction,
            "tools": list(self.tools),
            "enabled": self.enabled,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


@runtime_checkable
class ScenariosStorePort(Protocol):
    """Async interface for the scenarios (playbook) registry."""

    async def list_for_assistant(self, assistant_id: str) -> list[Scenario]:
        """Return all scenarios for the given assistant (enabled and disabled)."""
        ...

    async def get(self, scenario_id: str) -> Scenario | None:
        """Return the scenario with the given id, or None."""
        ...

    async def create(self, scenario: Scenario) -> str:
        """Persist a new scenario and return its id."""
        ...

    async def update(self, scenario_id: str, fields: dict[str, Any]) -> None:
        """Apply a partial update to an existing scenario (no-op if missing)."""
        ...

    async def delete(self, scenario_id: str) -> None:
        """Remove a scenario (no-op if missing)."""
        ...

    async def delete_for_assistant(self, assistant_id: str) -> None:
        """Remove all scenarios for an assistant (cascade on assistant delete)."""
        ...


# ---------------------------------------------------------------------------
# InMemory implementation (tests / local dev)
# ---------------------------------------------------------------------------


class InMemoryScenariosStore:
    """Volatile scenarios registry for tests/local dev."""

    def __init__(self) -> None:
        self._scenarios: dict[str, Scenario] = {}

    async def list_for_assistant(self, assistant_id: str) -> list[Scenario]:
        return [s for s in self._scenarios.values() if s.assistant_id == assistant_id]

    async def get(self, scenario_id: str) -> Scenario | None:
        return self._scenarios.get(scenario_id)

    async def create(self, scenario: Scenario) -> str:
        scenario_id = scenario.id or uuid.uuid4().hex
        stored = Scenario(
            id=scenario_id,
            assistant_id=scenario.assistant_id,
            title=scenario.title,
            description=scenario.description,
            instruction=scenario.instruction,
            tools=list(scenario.tools),
            enabled=scenario.enabled,
            created_at=scenario.created_at or datetime.now(UTC).isoformat(),
        )
        self._scenarios[scenario_id] = stored
        return scenario_id

    async def update(self, scenario_id: str, fields: dict[str, Any]) -> None:
        current = self._scenarios.get(scenario_id)
        if current is None:
            return
        data = current.to_dict()
        data.update(fields)
        self._scenarios[scenario_id] = Scenario(
            id=scenario_id,
            assistant_id=data["assistant_id"],
            title=data["title"],
            description=data["description"],
            instruction=data["instruction"],
            tools=list(data.get("tools") or []),
            enabled=bool(data.get("enabled", True)),
            created_at=data.get("created_at", ""),
        )

    async def delete(self, scenario_id: str) -> None:
        self._scenarios.pop(scenario_id, None)

    async def delete_for_assistant(self, assistant_id: str) -> None:
        to_delete = [sid for sid, s in self._scenarios.items() if s.assistant_id == assistant_id]
        for sid in to_delete:
            self._scenarios.pop(sid, None)


# ---------------------------------------------------------------------------
# Firestore implementation (production)
# ---------------------------------------------------------------------------


class FirestoreScenariosStore:
    """Firestore-backed scenarios store.

    Each scenario is one document in ``scenarios/<id>`` carrying all fields
    plus ``assistant_id`` for filtering.  Reads by assistant_id use a Firestore
    ``where`` query.  Every read path degrades cleanly to empty on failure.
    """

    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore  # noqa: PLC0415 — lazy: boot without the SDK

        self._client = firestore.Client(
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )
        self._collection_name = "scenarios"
        _log.info(
            "firestore_scenarios_store_initialized",
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
            collection=self._collection_name,
        )

    def _col(self) -> Any:
        return self._client.collection(self._collection_name)

    @staticmethod
    def _to_scenario(doc_id: str, data: dict[str, Any]) -> Scenario:
        return Scenario(
            id=doc_id,
            assistant_id=str(data.get("assistant_id", "")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            instruction=str(data.get("instruction", "")),
            tools=[str(t) for t in (data.get("tools") or [])],
            enabled=bool(data.get("enabled", True)),
            created_at=str(data.get("created_at", "")),
        )

    async def list_for_assistant(self, assistant_id: str) -> list[Scenario]:
        def _read() -> list[Scenario]:
            docs = self._col().where("assistant_id", "==", assistant_id).stream()
            return [self._to_scenario(doc.id, doc.to_dict() or {}) for doc in docs]

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("scenarios_store_list_failed", assistant_id=assistant_id, error=str(e))
            return []

    async def get(self, scenario_id: str) -> Scenario | None:
        def _read() -> Scenario | None:
            snap = self._col().document(scenario_id).get()
            if not snap.exists:
                return None
            data = snap.to_dict()
            return self._to_scenario(scenario_id, data if isinstance(data, dict) else {})

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("scenarios_store_get_failed", scenario_id=scenario_id, error=str(e))
            return None

    async def create(self, scenario: Scenario) -> str:
        scenario_id = scenario.id or uuid.uuid4().hex
        doc_data = {
            "assistant_id": scenario.assistant_id,
            "title": scenario.title,
            "description": scenario.description,
            "instruction": scenario.instruction,
            "tools": list(scenario.tools),
            "enabled": scenario.enabled,
            "created_at": scenario.created_at or datetime.now(UTC).isoformat(),
        }

        def _write() -> None:
            self._col().document(scenario_id).set(doc_data)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            _log.error("scenarios_store_create_failed", scenario_id=scenario_id, error=str(e))
        return scenario_id

    async def update(self, scenario_id: str, fields: dict[str, Any]) -> None:
        def _write() -> None:
            self._col().document(scenario_id).update(fields)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            _log.error("scenarios_store_update_failed", scenario_id=scenario_id, error=str(e))

    async def delete(self, scenario_id: str) -> None:
        def _delete() -> None:
            self._col().document(scenario_id).delete()

        try:
            await asyncio.to_thread(_delete)
        except Exception as e:
            _log.error("scenarios_store_delete_failed", scenario_id=scenario_id, error=str(e))

    async def delete_for_assistant(self, assistant_id: str) -> None:
        def _delete_all() -> None:
            docs = self._col().where("assistant_id", "==", assistant_id).stream()
            for doc in docs:
                doc.reference.delete()

        try:
            await asyncio.to_thread(_delete_all)
        except Exception as e:
            _log.error(
                "scenarios_store_delete_for_assistant_failed",
                assistant_id=assistant_id,
                error=str(e),
            )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_scenarios_store(settings: Settings) -> InMemoryScenariosStore | FirestoreScenariosStore:
    """Return the right scenarios-store implementation for *settings*.

    Falls back to ``InMemoryScenariosStore`` when ``firestore_project_id`` is
    empty or Firestore initialisation fails, so boot never breaks.
    """
    if not settings.firestore_project_id:
        _log.info("scenarios_store_inmemory_no_project_id")
        return InMemoryScenariosStore()
    try:
        return FirestoreScenariosStore(settings)
    except Exception as e:
        _log.warning("scenarios_store_firestore_init_failed", error=str(e))
        return InMemoryScenariosStore()
