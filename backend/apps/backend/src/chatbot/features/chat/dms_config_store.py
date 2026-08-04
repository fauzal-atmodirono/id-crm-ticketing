"""Firestore-backed config store for the DMS/TSP integration shell.

Mirrors pic_store.py's access pattern (one document per key, asyncio.to_thread
for I/O, fail-open on Firestore errors) but the "key" here is a singleton —
there is exactly one DMS config document.

The stored credential (an API key/secret for the still-unspecified DMS/TSP
API) is write-only: it can be set and replaced via `save()`, and read back
only via `get_credential()`. `DmsConfig` — the dataclass returned by `get()`
and consumed by every other public path — has no credential field at all, and
`public_dict()` (the API-safe serialization) never touches Firestore or the
credential, so there is no path by which the secret can reach a log line, a
repr, or an HTTP response. Phase 2 (actually calling a DMS/TSP API with this
config) is explicitly out of scope for this shell.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_COLLECTION = "dms_config"
_DOC_ID = "config"
_CREDENTIAL_FIELD = "credential"


@dataclass(frozen=True)
class DmsConfig:
    enabled: bool
    provider_label: str
    base_url: str
    auth_type: str
    extra_header_name: str
    extra_header_value: str
    timeout_seconds: float
    retries: int


def public_dict(config: DmsConfig) -> dict[str, Any]:
    """API-safe serialization of a DmsConfig. The credential is never part of
    DmsConfig, so there is nothing to strip here — it is simply absent.
    """
    return {
        "enabled": config.enabled,
        "provider_label": config.provider_label,
        "base_url": config.base_url,
        "auth_type": config.auth_type,
        "extra_header_name": config.extra_header_name,
        "extra_header_value": config.extra_header_value,
        "timeout_seconds": config.timeout_seconds,
        "retries": config.retries,
    }


class DmsConfigStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    def _doc_ref(self) -> firestore.DocumentReference:
        return self._client().collection(_COLLECTION).document(_DOC_ID)

    async def get(self) -> DmsConfig | None:
        try:
            snap = await asyncio.to_thread(self._doc_ref().get)
            if not snap.exists:
                return None
            data = snap.to_dict() or {}
            return DmsConfig(
                enabled=bool(data.get("enabled", False)),
                provider_label=str(data.get("provider_label", "")),
                base_url=str(data.get("base_url", "")),
                auth_type=str(data.get("auth_type", "")),
                extra_header_name=str(data.get("extra_header_name", "")),
                extra_header_value=str(data.get("extra_header_value", "")),
                timeout_seconds=float(data.get("timeout_seconds", 0.0)),
                retries=int(data.get("retries", 0)),
            )
        except Exception as e:
            _log.error("dms_config_store_get_failed", error=str(e))
            return None

    async def get_credential(self) -> str | None:
        try:
            snap = await asyncio.to_thread(self._doc_ref().get)
            if not snap.exists:
                return None
            data = snap.to_dict() or {}
            credential = data.get(_CREDENTIAL_FIELD)
            return str(credential) if credential is not None else None
        except Exception as e:
            _log.error("dms_config_store_get_credential_failed", error=str(e))
            return None

    async def save(self, config: DmsConfig, credential: str | None = None) -> None:
        """Persist `config`. `credential=None` means "keep whatever credential
        is already stored" (if any) so an operator can edit non-secret fields
        without re-entering the secret. Passing a credential always replaces
        the stored one.
        """
        try:
            doc_ref = self._doc_ref()
            data: dict[str, Any] = {
                "enabled": config.enabled,
                "provider_label": config.provider_label,
                "base_url": config.base_url,
                "auth_type": config.auth_type,
                "extra_header_name": config.extra_header_name,
                "extra_header_value": config.extra_header_value,
                "timeout_seconds": config.timeout_seconds,
                "retries": config.retries,
            }
            if credential is not None:
                data[_CREDENTIAL_FIELD] = credential
            else:
                existing_snap = await asyncio.to_thread(doc_ref.get)
                if existing_snap.exists:
                    existing_data = existing_snap.to_dict() or {}
                    if _CREDENTIAL_FIELD in existing_data:
                        data[_CREDENTIAL_FIELD] = existing_data[_CREDENTIAL_FIELD]
            await asyncio.to_thread(doc_ref.set, data)
        except Exception as e:
            _log.error("dms_config_store_save_failed", error=str(e))
