"""Assistants backing store.

Provides multiple named Assistants per tenant — the backbone of the Captain-parity
multi-assistant console. Each assistant carries its own persona/config (system prompt,
temperature, guardrails, tool enablement, feature flags) while all assistants share
the tenant knowledge base.

Two implementations follow the same Port + InMemory + Firestore pattern as
`live_faq.py` and `handoff_store.py`:

- `InMemoryAssistantsStore`: dict-backed, for tests and dev (lost on restart).
- `FirestoreAssistantsStore`: persists in Firestore collection "assistants" (one doc
  per assistant). Sync SDK calls are dispatched via `asyncio.to_thread`. Degrades
  cleanly on failure — reads return None/empty, never raise.

`build_assistants_store(settings)` picks InMemory when `firestore_project_id` is
unset, Firestore otherwise.

The `get_default()` call is idempotent: if no assistant has `is_default=True`, it
creates and persists a "Default Assistant" with all built-in tools enabled and
feature_faq=True so existing single-assistant behaviour is preserved with no UI step.
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
# Helper to extract built-in tool names without a circular import at module
# load time (copilot_tools is in features/assist/, not adapters/).
# ---------------------------------------------------------------------------


def _default_builtin_tool_names() -> list[str]:
    """Return the names of all COPILOT_TOOLS — lazy import avoids cycles."""
    try:
        from chatbot.features.assist.copilot_tools import COPILOT_TOOLS  # noqa: PLC0415

        return [t["name"] for t in COPILOT_TOOLS]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AssistantConfig:
    """Per-assistant persona configuration.

    All list fields default to empty; booleans default to the sensible
    production value (feature_faq=True, others False). temperature defaults
    to 1.0 (Gemini default). enabled_builtin_tools is populated with all
    COPILOT_TOOLS names by default (see `_default_builtin_tool_names()`).
    """

    instructions: str = ""
    temperature: float = 1.0
    guardrails: list[str] = field(default_factory=list)
    response_guidelines: list[str] = field(default_factory=list)
    welcome_message: str = ""
    handoff_message: str = ""
    resolution_message: str = ""
    feature_faq: bool = True
    feature_memory: bool = True
    feature_citations: bool = True
    feature_contact_attributes: bool = True
    enabled_builtin_tools: list[str] = field(default_factory=_default_builtin_tool_names)
    enabled_custom_tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instructions": self.instructions,
            "temperature": self.temperature,
            "guardrails": list(self.guardrails),
            "response_guidelines": list(self.response_guidelines),
            "welcome_message": self.welcome_message,
            "handoff_message": self.handoff_message,
            "resolution_message": self.resolution_message,
            "feature_faq": self.feature_faq,
            "feature_memory": self.feature_memory,
            "feature_citations": self.feature_citations,
            "feature_contact_attributes": self.feature_contact_attributes,
            "enabled_builtin_tools": list(self.enabled_builtin_tools),
            "enabled_custom_tools": list(self.enabled_custom_tools),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssistantConfig:
        return cls(
            instructions=str(data.get("instructions", "")),
            temperature=float(data.get("temperature", 1.0)),
            guardrails=[str(g) for g in (data.get("guardrails") or [])],
            response_guidelines=[str(r) for r in (data.get("response_guidelines") or [])],
            welcome_message=str(data.get("welcome_message", "")),
            handoff_message=str(data.get("handoff_message", "")),
            resolution_message=str(data.get("resolution_message", "")),
            feature_faq=bool(data.get("feature_faq", True)),
            feature_memory=bool(data.get("feature_memory", True)),
            feature_citations=bool(data.get("feature_citations", True)),
            feature_contact_attributes=bool(data.get("feature_contact_attributes", True)),
            enabled_builtin_tools=[
                str(t) for t in (data.get("enabled_builtin_tools") or _default_builtin_tool_names())
            ],
            enabled_custom_tools=[str(t) for t in (data.get("enabled_custom_tools") or [])],
        )


@dataclass
class Assistant:
    """A named assistant carrying its own persona and tool enablement.

    `config` holds the editable persona (instructions, temperature, guardrails,
    feature flags, tool enablement). `is_default` marks the fallback assistant
    used when no `assistant_id` is supplied to /assist/* endpoints.
    """

    id: str
    name: str
    description: str
    product_name: str
    config: AssistantConfig
    enabled: bool
    is_default: bool
    created_at: str  # ISO-8601

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "product_name": self.product_name,
            "config": self.config.to_dict(),
            "enabled": self.enabled,
            "is_default": self.is_default,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Assistant:
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            product_name=str(data.get("product_name", "")),
            config=AssistantConfig.from_dict(data.get("config") or {}),
            enabled=bool(data.get("enabled", True)),
            is_default=bool(data.get("is_default", False)),
            created_at=str(data.get("created_at", "")),
        )


def _new_id() -> str:
    return "asst_" + uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _make_default_assistant() -> Assistant:
    """Create the default-seeded assistant with all built-in tools enabled."""
    return Assistant(
        id=_new_id(),
        name="Default Assistant",
        description="",
        product_name="",
        config=AssistantConfig(
            instructions="",
            enabled_builtin_tools=_default_builtin_tool_names(),
            feature_faq=True,
        ),
        enabled=True,
        is_default=True,
        created_at=_now(),
    )


# ---------------------------------------------------------------------------
# Port (Protocol)
# ---------------------------------------------------------------------------


@runtime_checkable
class AssistantsStorePort(Protocol):
    """Read-and-write interface for the assistants store.

    All reads (get, get_default, list_all) never raise — they return None or
    empty lists on failure. `get_default()` always returns an Assistant (seeds
    one if none is marked is_default). `delete` refuses the default (409 in the
    router layer — the store itself raises ValueError so callers can translate).
    """

    async def list_all(self) -> list[Assistant]: ...

    async def get(self, assistant_id: str) -> Assistant | None: ...

    async def get_default(self) -> Assistant: ...

    async def create(self, assistant: Assistant) -> str: ...

    async def update(self, assistant_id: str, fields: dict[str, Any]) -> None: ...

    async def delete(self, assistant_id: str) -> None:
        """Raise `ValueError` if the target is the default assistant."""
        ...


# ---------------------------------------------------------------------------
# InMemory implementation
# ---------------------------------------------------------------------------


class InMemoryAssistantsStore:
    """Volatile, single-process assistants store. Used for tests and dev."""

    def __init__(self) -> None:
        self._assistants: dict[str, Assistant] = {}

    async def list_all(self) -> list[Assistant]:
        return list(self._assistants.values())

    async def get(self, assistant_id: str) -> Assistant | None:
        return self._assistants.get(assistant_id)

    async def get_default(self) -> Assistant:
        for a in self._assistants.values():
            if a.is_default:
                return a
        # Seed one.
        default = _make_default_assistant()
        self._assistants[default.id] = default
        _log.info("assistants_store_seeded_default", assistant_id=default.id)
        return default

    async def create(self, assistant: Assistant) -> str:
        self._assistants[assistant.id] = assistant
        return assistant.id

    async def update(self, assistant_id: str, fields: dict[str, Any]) -> None:
        current = self._assistants.get(assistant_id)
        if current is None:
            return
        # Apply top-level scalar fields.
        name = str(fields.get("name", current.name))
        description = str(fields.get("description", current.description))
        product_name = str(fields.get("product_name", current.product_name))
        enabled = bool(fields.get("enabled", current.enabled))
        is_default = bool(fields.get("is_default", current.is_default))
        # Merge nested config if provided.
        config = current.config
        if "config" in fields and isinstance(fields["config"], dict):
            merged = current.config.to_dict()
            merged.update(fields["config"])
            config = AssistantConfig.from_dict(merged)
        self._assistants[assistant_id] = Assistant(
            id=assistant_id,
            name=name,
            description=description,
            product_name=product_name,
            config=config,
            enabled=enabled,
            is_default=is_default,
            created_at=current.created_at,
        )

    async def delete(self, assistant_id: str) -> None:
        target = self._assistants.get(assistant_id)
        if target is None:
            raise KeyError(assistant_id)
        if target.is_default:
            raise ValueError(f"Cannot delete the default assistant: {assistant_id}")
        del self._assistants[assistant_id]


# ---------------------------------------------------------------------------
# Firestore implementation
# ---------------------------------------------------------------------------


class FirestoreAssistantsStore:
    """Firestore-backed assistants store.

    Each assistant is one document in `assistants/<id>`. The Firestore SDK is
    synchronous; all calls run in a worker thread via `asyncio.to_thread`. Every
    read path degrades cleanly on failure (returns None/empty, never raises) so
    the router always responds even if Firestore is unreachable.
    """

    _COLLECTION = "assistants"

    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore  # noqa: PLC0415 — lazy: boot without the SDK

        self._client = firestore.Client(
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )
        _log.info(
            "firestore_assistants_store_initialized",
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )

    def _collection(self) -> Any:
        return self._client.collection(self._COLLECTION)

    @staticmethod
    def _to_assistant(doc_id: str, data: dict[str, Any]) -> Assistant:
        data["id"] = doc_id
        return Assistant.from_dict(data)

    async def list_all(self) -> list[Assistant]:
        def _read() -> list[Assistant]:
            return [
                self._to_assistant(doc.id, doc.to_dict() or {})
                for doc in self._collection().stream()
            ]

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("assistants_store_list_failed", error=str(e))
            return []

    async def get(self, assistant_id: str) -> Assistant | None:
        def _read() -> Assistant | None:
            snap = self._collection().document(assistant_id).get()
            if not snap.exists:
                return None
            data = snap.to_dict()
            return self._to_assistant(assistant_id, data if isinstance(data, dict) else {})

        try:
            return await asyncio.to_thread(_read)
        except Exception as e:
            _log.error("assistants_store_get_failed", assistant_id=assistant_id, error=str(e))
            return None

    async def get_default(self) -> Assistant:
        def _query() -> Assistant | None:
            docs = self._collection().where("is_default", "==", True).limit(1).stream()
            for doc in docs:
                data = doc.to_dict()
                return self._to_assistant(doc.id, data if isinstance(data, dict) else {})
            return None

        try:
            existing = await asyncio.to_thread(_query)
        except Exception as e:
            _log.error("assistants_store_get_default_query_failed", error=str(e))
            existing = None

        if existing is not None:
            return existing

        # Seed one.
        default = _make_default_assistant()
        await self.create(default)
        _log.info("assistants_store_seeded_default", assistant_id=default.id)
        return default

    async def create(self, assistant: Assistant) -> str:
        doc_data = assistant.to_dict()
        doc_data.pop("id", None)  # id is the document key, not a stored field

        def _write() -> None:
            self._collection().document(assistant.id).set(doc_data)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            _log.error("assistants_store_create_failed", assistant_id=assistant.id, error=str(e))
        return assistant.id

    async def update(self, assistant_id: str, fields: dict[str, Any]) -> None:
        # Read current doc to merge config.
        current = await self.get(assistant_id)
        if current is None:
            return

        patch: dict[str, Any] = {}
        for key in ("name", "description", "product_name", "enabled", "is_default"):
            if key in fields:
                patch[key] = fields[key]

        if "config" in fields and isinstance(fields["config"], dict):
            merged = current.config.to_dict()
            merged.update(fields["config"])
            patch["config"] = merged

        if not patch:
            return

        def _write() -> None:
            self._collection().document(assistant_id).update(patch)

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            _log.error("assistants_store_update_failed", assistant_id=assistant_id, error=str(e))

    async def delete(self, assistant_id: str) -> None:
        current = await self.get(assistant_id)
        if current is None:
            raise KeyError(assistant_id)
        if current.is_default:
            raise ValueError(f"Cannot delete the default assistant: {assistant_id}")

        def _del() -> None:
            self._collection().document(assistant_id).delete()

        try:
            await asyncio.to_thread(_del)
        except Exception as e:
            _log.error("assistants_store_delete_failed", assistant_id=assistant_id, error=str(e))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_assistants_store(settings: Settings) -> InMemoryAssistantsStore | FirestoreAssistantsStore:
    """Return a FirestoreAssistantsStore when firestore_project_id is set,
    else InMemoryAssistantsStore (tests / dev with no GCP credentials)."""
    if settings.firestore_project_id:
        try:
            return FirestoreAssistantsStore(settings)
        except Exception as e:
            _log.warning("firestore_assistants_store_init_failed_falling_back", error=str(e))
    return InMemoryAssistantsStore()
