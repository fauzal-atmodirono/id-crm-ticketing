"""The custom-feature switchboard: which surfaces a tenant's CRM has.

Two gates exist in this product and they answer different questions. A
FEATURE asks "is this capability part of this tenant's product at all?" and
is owned by the platform superadmin. A PERMISSION asks "which of the enabled
capabilities may this person use?" and is owned by the tenant's own
administrator. A surface renders only when both agree, so a tenant with every
feature off opens blank no matter how permissive its roles are.

An absent key is OFF. There is no seeding, no default-on list and no
first-boot marker: "a new tenant opens empty" is a property of the data model
rather than a value somebody has to remember to set.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# One document, not one per key: this is read on every SPA page load, so a
# single get() beats N reads. The term dictionary (see the term-dictionary
# spec) lands in this same document for the same reason.
_COLLECTION = "platform_config"
_DOCUMENT = "custom_features"


class CustomFeatureStoreUnavailable(RuntimeError):
    """Raised by `CustomFeatureStore.get_all()` when Firestore could not be
    read at all -- as opposed to a real, empty document, which is a tenant
    that genuinely has nothing switched on.

    The two look identical if get_all() just returned `{}` for both: the
    router would answer 200 `{"features": []}` either way, the composable
    would take its success branch, and a Firestore blip would blank two live
    tenants' CRMs for the rest of the page session with no error shown and no
    retry scheduled -- silently indistinguishable from "this tenant bought
    nothing". Raising instead lets the router answer 503 for the outage case
    only, which is what drives the composable's existing `.catch()` self-heal
    (see useCustomFeatures.js) instead of its success path."""


@dataclass(frozen=True)
class CustomFeature:
    key: str
    label: str
    group: str
    permission: str | None
    kind: str = "surface"


def _f(key: str, label: str, group: str, permission: str | None) -> CustomFeature:
    return CustomFeature(key=key, label=label, group=group, permission=permission)


# The closed set of toggleable surfaces. Static rather than store-driven: a
# feature that can be enabled by typing its name is one that can be enabled by
# MIStyping something else.
CUSTOM_FEATURE_REGISTRY: dict[str, CustomFeature] = {
    f.key: f
    for f in (
        _f("ai_assist", "AI reply suggestions", "AI", None),
        _f("copilot", "Ask Copilot panel", "AI", None),
        _f("faq_suggestion_popup", "FAQ suggestion strip", "AI", None),
        _f("translate", "Message translation", "AI", "translation.use"),
        _f("knowledge", "Knowledge Base console", "Knowledge", "knowledge.edit"),
        _f("reports_departments", "Departments report", "Reports", None),
        _f("reports_case_lifecycle", "Case lifecycle report", "Reports", None),
        _f("reports_anomaly", "Anomaly report", "Reports", None),
        _f("reports_weekly", "Weekly report", "Reports", None),
        _f("cases", "Cases list", "Cases", "cases.view"),
        _f("taxonomy", "Case taxonomy admin", "Cases", "taxonomy.manage"),
        _f("rsa_incidents", "Field incident log", "Cases", "sla.manage"),
        _f("workforce", "Workforce dashboard", "Operations", "workforce.view"),
        _f("agent_softphone", "Agent softphone", "Operations", "voice.answer"),
        _f("sla_policies", "SLA policies", "Operations", "sla.manage"),
        _f("escalation_routing", "Escalation routing", "Operations", "escalation.manage"),
        _f("inbound_alerts", "Inbound alerts", "Operations", "alerts.manage"),
        _f("alert_preferences", "Alert preferences", "Operations", "alerts.set_own_preferences"),
        _f("agent_status", "Availability status selector", "Operations", "presence.set_own_status"),
        _f("agent_priorities", "Agent channel priorities", "Operations", "workforce.manage"),
        _f("customer360", "Customer 360", "Data", "customer360.view"),
        _f("integrations", "Business system integration", "Data", "integration.manage"),
        _f("audit_log", "Audit log", "Admin", "audit.view"),
        _f("roles_permissions", "Roles & permissions", "Admin", "roles.manage"),
    )
}

# Backend runtime behaviours with no UI of their own. Phase 1 does NOT make
# these toggleable -- they are read from `Settings` at boot, so moving them
# into the store means runtime-mutable settings with their own caching and
# invalidation story. They are listed here so the switchboard can show them
# read-only: a page that silently omits half a tenant's configuration is worse
# than one that shows it and says who owns it.
# Every attribute below was verified to exist on the backend's `Settings`.
# Note what is ABSENT: LIFECYCLE_ENABLED, KB_GROUNDED_REPLIES and
# CHAT_AGENT_ENABLED are read by the `agent/` service's own Settings, not by
# this one, so they cannot be reported here and are deliberately omitted
# rather than rendered as a permanent "off" the operator cannot explain.
BEHAVIOR_FLAGS: dict[str, str] = {
    "behavior_routing": "routing_enabled",
    "behavior_presence_tracking": "presence_tracking_enabled",
    "behavior_sla_engine": "sla_engine_enabled",
    "behavior_escalation_email": "escalation_email_enabled",
    "behavior_phone_handoff": "phone_handoff_enabled",
    "behavior_phone_recording": "phone_recording_enabled",
    "behavior_phone_transcription": "phone_recording_transcription_enabled",
    "behavior_knowledge_pg": "knowledge_pg_enabled",
    "behavior_rbac": "rbac_enabled",
    "behavior_translation": "translation_enabled",
    "behavior_rsa": "rsa_enabled",
    "behavior_taxonomy_admin": "taxonomy_admin_enabled",
    "behavior_inbound_alerts": "inbound_alerts_enabled",
}


def enabled_features(stored: dict[str, bool]) -> list[str]:
    """Registered keys that the store says are on. Unknown keys are ignored
    rather than raising -- a key left behind by a retired feature must not be
    able to 500 every page load in the tenant that still has it stored."""
    return sorted(k for k, v in stored.items() if v and k in CUSTOM_FEATURE_REGISTRY)


class CustomFeatureStore:
    """Firestore-backed, one document per tenant. Mirrors PicStore/DealerStore:
    lazy client, `asyncio.to_thread` around the blocking SDK, fail-closed."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cached_client: firestore.Client | None = None

    def _client(self) -> firestore.Client:
        # Same project/database pair PicStore uses, but CACHED, which PicStore
        # deliberately is not. PicStore backs admin CRUD that an operator hits
        # a few times a week; this document is read on every SPA page load by
        # every agent, so building a fresh Firestore client per request would
        # be a connection setup on the hot path.
        if self._cached_client is None:
            self._cached_client = firestore.Client(
                project=self._settings.firestore_project_id,
                database=self._settings.firestore_database_id,
            )
        return self._cached_client

    def _doc_ref(self) -> firestore.DocumentReference:
        return self._client().collection(_COLLECTION).document(_DOCUMENT)

    async def get_all(self) -> dict[str, bool]:
        """Fail CLOSED for rendering (a caller that swallows
        `CustomFeatureStoreUnavailable` and treats it as "no features" never
        shows a tenant a surface it does not have), but the failure itself is
        never silent: a genuinely unreachable store raises rather than
        returning `{}`, so it cannot be confused with a real, empty document
        (a tenant that has nothing switched on -- also `{}`, and a completely
        different, valid state). See `CustomFeatureStoreUnavailable` for why
        that distinction matters end to end."""
        try:
            snap = await asyncio.to_thread(self._doc_ref().get)
        except Exception as e:
            _log.error("custom_feature_store_get_failed", error=str(e))
            raise CustomFeatureStoreUnavailable(str(e)) from e
        if not snap.exists:
            return {}
        raw = (snap.to_dict() or {}).get("features") or {}
        return {str(k): bool(v) for k, v in raw.items()}

    async def set(self, key: str, enabled: bool) -> None:
        """Merge-write a single key. A bare set() would drop every other
        feature, which on this document means blanking the tenant's CRM."""
        await asyncio.to_thread(
            self._doc_ref().set, {"features": {key: enabled}}, merge=True
        )
