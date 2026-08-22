"""The custom-feature switchboard's HTTP surface.

Reads are open to any signed-in Chatwoot session -- there is nothing
sensitive in "what is switched on in the CRM I am already looking at" -- but
the REGISTRY is superadmin-only. A tenant admin who could enumerate the
switched-off surfaces would be reading a product roadmap.

Writes are gated on `require_platform_superadmin`, deliberately outside RBAC.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from chatbot.features.authz.deps import (
    is_platform_superadmin,
    require_platform_superadmin,
)
from chatbot.features.tenant_config.custom_features import (
    BEHAVIOR_FLAGS,
    CUSTOM_FEATURE_REGISTRY,
    CustomFeatureStoreUnavailable,
    enabled_features,
    stored_terms,
)
from chatbot.features.tenant_config.term_dictionary import (
    PROFILES,
    TERM_KEYS,
    resolve_profile,
    resolve_terms,
)

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.tenant_config.custom_features import CustomFeatureStore
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class ToggleBody(BaseModel):
    key: str
    enabled: bool


class TermsBody(BaseModel):
    profile: str | None = None
    overrides: dict | None = None


# Mirrors the fields `term_dictionary.Term` carries and the exact tuple
# `resolve_terms` iterates when applying a patch. Not imported from there --
# there is no public constant for it -- but kept in lockstep by the fact that
# a field this endpoint accepts and `resolve_terms` does not read would be
# the same silent no-op this validation exists to close off.
_OVERRIDE_FIELDS = frozenset({"singular", "plural", "lower"})


def build_custom_features_router(
    store: CustomFeatureStore,
    validator: TokenValidator,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/admin/custom-features", tags=["custom-features"])
    superadmin_only = require_platform_superadmin(validator=validator, settings=settings)

    async def _identity(
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> tuple[int, bool]:
        if not x_chatwoot_access_token or not x_chatwoot_client or not x_chatwoot_uid:
            raise HTTPException(status_code=401, detail="Chatwoot session required")
        identity = await validator.resolve_identity(
            x_chatwoot_access_token, x_chatwoot_client, x_chatwoot_uid
        )
        if identity is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return identity

    @router.get("")
    async def read(identity: tuple[int, bool] = Depends(_identity)) -> dict:
        user_id, is_super_admin_type = identity
        superadmin = is_platform_superadmin(user_id, is_super_admin_type)

        # One document, one read: features and terms live in the same
        # Firestore document precisely so the SPA's single page-load fetch
        # covers both. The 503 handling below MUST survive this rewrite from
        # get_all() to get_document() -- get_document() raises on the same
        # unreachable-store condition get_all() used to, and a 200 with an
        # empty feature list would tell the operator this tenant owns
        # nothing when in fact we could not look: the composable's success
        # branch (`features.value = []`) never schedules a retry, so a
        # transient Firestore blip would blank a live tenant's CRM for the
        # rest of the page session with no error shown. A 503 makes the
        # SPA's adminRequest() throw, which routes into
        # useCustomFeatures.js's existing `.catch()` self-heal instead.
        try:
            document = await store.get_document()
        except CustomFeatureStoreUnavailable as e:
            _log.error("custom_feature_store_read_failed", error=str(e))
            raise HTTPException(status_code=503, detail="Could not load features") from e

        stored = {str(k): bool(v) for k, v in (document.get("features") or {}).items()}
        stored_profile, overrides = stored_terms(document)
        profile = resolve_profile(stored_profile, settings)

        registry: list[dict] = []
        behavior: dict[str, bool] = {}
        if superadmin:
            registry = [
                {
                    "key": f.key,
                    "label": f.label,
                    "group": f.group,
                    "enabled": bool(stored.get(f.key, False)),
                }
                for f in CUSTOM_FEATURE_REGISTRY.values()
            ]
            # Read-only, env-owned. Shown so the page tells the whole truth
            # about the tenant rather than implying these do not exist.
            behavior = {
                key: bool(getattr(settings, attr, False))
                for key, attr in BEHAVIOR_FLAGS.items()
            }

        return {
            "features": enabled_features(stored),
            "is_superadmin": superadmin,
            "registry": registry,
            "behavior": behavior,
            # Vocabulary is served to EVERY session, unlike the registry: the
            # UI cannot render a heading without it.
            "terms": resolve_terms(profile, overrides),
            "term_profile": profile,
            "term_profiles": sorted(PROFILES) if superadmin else [],
        }

    @router.post("")
    async def toggle(
        body: ToggleBody,
        _user_id: int = Depends(superadmin_only),
    ) -> dict:
        if body.key in BEHAVIOR_FLAGS:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{body.key} is env-controlled and not yet switchable here"
                ),
            )
        if body.key not in CUSTOM_FEATURE_REGISTRY:
            raise HTTPException(status_code=400, detail=f"Unknown feature: {body.key}")
        try:
            await store.set(body.key, body.enabled)
        except Exception as e:
            # 503, not a bare 500 and never a 200: reporting success for a
            # write that did not land tells the superadmin this tenant's
            # product changed when it did not.
            _log.error("custom_feature_write_failed", key=body.key, error=str(e))
            raise HTTPException(status_code=503, detail="Could not save") from e
        return {"key": body.key, "enabled": body.enabled, "status": "ok"}

    @router.post("/terms")
    async def set_terms(
        body: TermsBody,
        _user_id: int = Depends(superadmin_only),
    ) -> dict:
        if body.profile is not None and body.profile not in PROFILES:
            raise HTTPException(status_code=400, detail=f"Unknown profile: {body.profile}")
        if body.overrides is not None:
            unknown = sorted(set(body.overrides) - set(TERM_KEYS))
            if unknown:
                # The noun list is closed on purpose. Accepting arbitrary keys
                # is exactly how a capped dictionary stops being capped.
                raise HTTPException(
                    status_code=400, detail=f"Unknown term keys: {', '.join(unknown)}"
                )
            # Key validation alone lets a well-formed noun through with a
            # malformed patch -- `resolve_terms` defends itself against that
            # (`isinstance(patch, dict)`, only ever reading singular/plural/
            # lower, skipping an empty string) so nothing crashes, but the
            # write would still persist and answer 200 while doing nothing.
            # That is the same lie a dropped store write would tell, in a
            # different place, so it is rejected here instead.
            for noun, patch in body.overrides.items():
                if not isinstance(patch, dict):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"{noun}: override must be an object with "
                            f"singular/plural/lower fields, got {type(patch).__name__}"
                        ),
                    )
                bad_fields = sorted(set(patch) - _OVERRIDE_FIELDS)
                if bad_fields:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{noun}: unknown override fields {', '.join(bad_fields)}",
                    )
                for field, value in patch.items():
                    if not isinstance(value, str) or not value:
                        raise HTTPException(
                            status_code=400,
                            detail=f"{noun}.{field}: must be a non-empty string",
                        )
        await store.set_terms(body.profile, body.overrides)
        return {"profile": body.profile, "status": "ok"}

    return router
