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
repr, or an HTTP response. Every `except` here logs `error_type` and never
`str(e)`: in `save()` the credential is a live local inside the `try`, and
Firestore/google-api-core errors do interpolate offending values into their
messages. Phase 2 (actually calling a DMS/TSP API with this config) is
explicitly out of scope for this shell.

Two performance properties matter because `get()` is on Customer 360's
interactive path and is reached on every lookup: one `firestore.Client` is
built lazily per store instance rather than per call, and successful `get()`
results (including "no document") are cached for `_CONFIG_CACHE_TTL_SECONDS`
with `save()` invalidating in-process. `get_credential()` is never cached.
"""

from __future__ import annotations

import asyncio
import time
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

# `get()` sits on Customer 360's interactive path -- an operator is waiting on
# the page -- and is reached on EVERY lookup, for every tenant, including the
# overwhelming majority that have no DMS document at all. Without a cache each
# of those pays a Firestore round trip to learn "still nothing configured".
# 30s is short enough that an operator who saves config in the admin UI sees
# it take effect within one page refresh, and `save()` invalidates in-process
# immediately, so the window only matters across separate worker processes.
# The cached value is a `DmsConfig`, which by construction has no credential
# field -- there is no secret being held in memory here.
_CONFIG_CACHE_TTL_SECONDS = 30.0

# Ceiling on the operator-settable `timeout_seconds`. Lives here, next to the
# dataclass, because it has to be enforced in TWO places that must agree:
# `DmsConfigBody` rejects an out-of-range value on write, and
# `customer360_router` clamps on read so a document written before the
# constraint existed (or by anything that bypasses the admin API) still
# can't hang an interactive lookup. 30s is generous for a health probe and
# short enough that a stuck DMS degrades the page rather than freezing it.
MAX_TIMEOUT_SECONDS = 30.0


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
        # One `firestore.Client` per store instance, built lazily on first
        # actual use. Rebuilding it per call meant every Customer 360 lookup
        # paid an ADC credential resolution plus a fresh gRPC channel setup,
        # on a path a human is waiting on. Lazy (not built in __init__)
        # because main.py constructs this store unconditionally, including on
        # tenants that never configure a DMS -- those must still never touch
        # Firestore or require credentials to exist.
        self._firestore_client: firestore.Client | None = None
        # Guards both the lazy client and the config cache. Without it, two
        # concurrent lookups on a cold store each build their own client.
        self._lock = asyncio.Lock()
        self._cached_config: DmsConfig | None = None
        self._cached_at: float | None = None

    def _client(self) -> firestore.Client:
        if self._firestore_client is None:
            self._firestore_client = firestore.Client(
                project=self._settings.firestore_project_id,
                database=self._settings.firestore_database_id,
            )
        return self._firestore_client

    def _doc_ref(self) -> firestore.DocumentReference:
        return self._client().collection(_COLLECTION).document(_DOC_ID)

    def _read_snapshot(self) -> Any:
        """Runs entirely inside `asyncio.to_thread`. Client construction and
        doc-ref construction are blocking too, so they belong in here rather
        than on the event-loop thread ahead of the `to_thread` call.
        """
        return self._doc_ref().get()

    def _invalidate_cache(self) -> None:
        self._cached_config = None
        self._cached_at = None

    async def get(self) -> DmsConfig | None:
        now = time.monotonic()
        async with self._lock:
            if self._cached_at is not None and now - self._cached_at < _CONFIG_CACHE_TTL_SECONDS:
                return self._cached_config
        try:
            snap = await asyncio.to_thread(self._read_snapshot)
            config: DmsConfig | None = None
            if snap.exists:
                data = snap.to_dict() or {}
                config = DmsConfig(
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
            # Deliberately NOT cached: a Firestore blip must not pin "not
            # configured" for the whole TTL. Only a successful read is cached.
            _log.error("dms_config_store_get_failed", error_type=type(e).__name__)
            return None
        async with self._lock:
            self._cached_config = config
            self._cached_at = time.monotonic()
        return config

    async def get_credential(self) -> str | None:
        # Never cached. It is only read on the admin "Test connection" path
        # (not the interactive Customer 360 one), so there is no latency to
        # win, and holding the secret in a process-lifetime attribute would
        # widen its exposure for no benefit.
        try:
            snap = await asyncio.to_thread(self._read_snapshot)
            if not snap.exists:
                return None
            data = snap.to_dict() or {}
            credential = data.get(_CREDENTIAL_FIELD)
            return str(credential) if credential is not None else None
        except Exception as e:
            _log.error("dms_config_store_get_credential_failed", error_type=type(e).__name__)
            return None

    async def save(self, config: DmsConfig, credential: str | None = None) -> None:
        """Persist `config`. `credential=None` means "keep whatever credential
        is already stored" (if any) so an operator can edit non-secret fields
        without re-entering the secret. Passing a credential always replaces
        the stored one.
        """
        # Invalidate up front, not on the success path only: if the write
        # partially applied before raising, a stale cache would be worse than
        # a re-read.
        self._invalidate_cache()
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
            # `error_type`, never `str(e)`: `credential` is a live local in
            # this function's scope and google-api-core/Firestore errors do
            # interpolate the offending value into their message (a failed
            # write's rejected field, an invalid-argument's payload). The
            # class name carries everything an operator needs to triage and
            # nothing the write-only invariant forbids. Matches
            # customer360_router.py's `error_type=type(exc).__name__`.
            _log.error("dms_config_store_save_failed", error_type=type(e).__name__)
