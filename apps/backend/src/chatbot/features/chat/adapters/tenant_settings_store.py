"""Tenant-level editable settings store.

Holds a flat dict of override values that take precedence over env defaults.
A key set to None or "" clears the override (reverts to env default).

Two implementations:
- `InMemoryTenantSettingsStore` — volatile, used in tests/dev.
- `FirestoreTenantSettingsStore` — persists to `tenant_settings/current` doc.
  Degrades to {} on failure (read errors return empty; write errors are logged
  and swallowed) so a Firestore outage never breaks the GET endpoint.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_FIRESTORE_COLLECTION = "tenant_settings"
_FIRESTORE_DOC = "current"


@runtime_checkable
class TenantSettingsStorePort(Protocol):
    """Port for the tenant-level settings override store."""

    async def get_overrides(self) -> dict[str, Any]:
        """Return the current override dict (only keys explicitly set)."""
        ...

    async def set_overrides(self, partial: dict[str, Any]) -> None:
        """Merge `partial` into the stored overrides.

        A key mapped to None or "" clears that override from the store.
        Unknown keys are accepted (the facade ignores them).
        """
        ...


class InMemoryTenantSettingsStore:
    """Volatile override store — for tests and local dev."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def get_overrides(self) -> dict[str, Any]:
        return dict(self._data)

    async def set_overrides(self, partial: dict[str, Any]) -> None:
        for key, value in partial.items():
            if value is None or value == "":
                self._data.pop(key, None)
            else:
                self._data[key] = value


class FirestoreTenantSettingsStore:
    """Firestore-backed override store.

    Persists all overrides as a single document at
    `<_FIRESTORE_COLLECTION>/<_FIRESTORE_DOC>`. The Firestore SDK is
    synchronous; all calls run via `asyncio.to_thread`. Every read degrades to
    {} on failure; writes log and swallow exceptions so a Firestore outage never
    breaks the GET /kb/settings endpoint.
    """

    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore  # noqa: PLC0415 — lazy: boot without the SDK

        self._client = firestore.Client(
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )
        _log.info(
            "firestore_tenant_settings_store_initialized",
            project=settings.firestore_project_id,
            database=settings.firestore_database_id,
        )

    def _doc_ref(self) -> Any:
        return self._client.collection(_FIRESTORE_COLLECTION).document(_FIRESTORE_DOC)

    async def get_overrides(self) -> dict[str, Any]:
        def _read() -> dict[str, Any]:
            snap = self._doc_ref().get()
            if not snap.exists:
                return {}
            data = snap.to_dict()
            return data if isinstance(data, dict) else {}

        try:
            return await asyncio.to_thread(_read)
        except Exception as exc:
            _log.error("tenant_settings_get_failed", error=str(exc))
            return {}

    async def set_overrides(self, partial: dict[str, Any]) -> None:
        # Merge: clear keys mapped to None/"", set the rest.
        to_set: dict[str, Any] = {}
        to_delete: list[str] = []

        for key, value in partial.items():
            if value is None or value == "":
                to_delete.append(key)
            else:
                to_set[key] = value

        def _write() -> None:
            from google.cloud.firestore_v1 import DELETE_FIELD  # noqa: PLC0415

            patch: dict[str, Any] = dict(to_set)
            for k in to_delete:
                patch[k] = DELETE_FIELD
            if patch:
                self._doc_ref().set(patch, merge=True)

        try:
            await asyncio.to_thread(_write)
        except Exception as exc:
            _log.error("tenant_settings_set_failed", error=str(exc))


def build_tenant_settings_store(settings: Settings) -> InMemoryTenantSettingsStore | FirestoreTenantSettingsStore:
    """Construct InMemory or Firestore store depending on firestore_project_id."""
    if not settings.firestore_project_id:
        return InMemoryTenantSettingsStore()
    try:
        return FirestoreTenantSettingsStore(settings)
    except Exception as exc:
        _log.warning("firestore_tenant_settings_store_init_failed", error=str(exc))
        return InMemoryTenantSettingsStore()
