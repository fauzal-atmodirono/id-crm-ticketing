"""Who sees personal data unmasked (Wave 2 Task 29, FIX-49).

``build_pii_dependency`` resolves, once per request, whether a backend
response must be masked for the caller:

* ``PII_MASKING_ENABLED`` off -> never masked. The dependency reads no header
  and makes no lookup, and ``PiiPolicy.apply`` returns the SAME object it was
  given, so every response is byte-identical to before this task.
* On -> unmasked only for a caller who resolves to a Chatwoot user holding
  ``pii.view`` (seeded to ``administrator`` only), or a platform superadmin.
  Everyone else is masked: no session, an unresolvable session, a missing
  RBAC store and a failed permission read all FAIL CLOSED to masked. The
  dependency never raises -- whether the caller may use the endpoint at all is
  the endpoint's own permission gate; this only decides how the data looks.

The flag requires ``RBAC_ENABLED`` (``Settings`` refuses to boot otherwise):
without RBAC there is no person, so nobody could ever be shown the real value.

What is masked, and what is deliberately NOT (record of the Task 29 design):
masking applies to backend responses shown to people -- Customer 360, RSA
incidents and their CSV export, case-record fields, AI activity previews.
It does NOT apply to data sent to the LLM or to escalation recipients (a PIC
or dealer needs the number to call the customer back), nor to Chatwoot's own
API responses (the fork's contact views mask on the client, patch 0085).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException

from chatbot.features.authz.deps import is_platform_superadmin
from chatbot.features.privacy.pii import contains_mask, mask_pii

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

PII_VIEW = "pii.view"


@dataclass(frozen=True)
class PiiPolicy:
    enabled: bool
    masked: bool

    def apply(self, value: Any) -> Any:
        """Masked deep copy when this caller is masked; else ``value`` itself."""
        return mask_pii(value) if self.masked else value

    def refuse_masked_write(self, value: Any) -> None:
        """400 when a write carries a masked value (copied back from a masked
        page), so it can never overwrite the real one. Flag off: no-op."""
        if self.enabled and contains_mask(value):
            raise HTTPException(
                status_code=400,
                detail="A masked value cannot be saved. Enter the full value, or leave the field unchanged.",
            )


UNMASKED = PiiPolicy(enabled=False, masked=False)


def build_pii_dependency(
    settings: Settings,
    *,
    repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
):
    async def _policy(  # noqa: PLR0911 -- one early return per fail-closed case
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> PiiPolicy:
        if not getattr(settings, "pii_masking_enabled", False):
            return UNMASKED
        masked = PiiPolicy(enabled=True, masked=True)
        if not (x_chatwoot_access_token and x_chatwoot_client and x_chatwoot_uid):
            return masked
        if validator is None or repo is None:
            return masked
        try:
            identity = await validator.resolve_identity(
                x_chatwoot_access_token, x_chatwoot_client, x_chatwoot_uid
            )
            if identity is None:
                return masked
            user_id, is_super_admin_type = identity
            if is_platform_superadmin(user_id, is_super_admin_type):
                return PiiPolicy(enabled=True, masked=False)
            perms = await repo.permissions_for_user(user_id)
        except Exception as exc:
            _log.warning("pii_policy_lookup_failed", error_type=type(exc).__name__)
            return masked
        return PiiPolicy(enabled=True, masked=PII_VIEW not in perms)

    return _policy


def build_privacy_router(
    settings: Settings,
    *,
    repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
) -> APIRouter:
    """``GET /privacy/me`` -- the fork's contact views ask this whether to mask.

    Mounted on every tenant so the fork can call it unconditionally: flag off
    answers ``masking_enabled: false`` and the fork shows everything, exactly
    as before. Not gated (it reveals only the caller's own masking state).
    """
    router = APIRouter(prefix="/privacy", tags=["privacy"])
    policy = build_pii_dependency(settings, repo=repo, validator=validator)

    @router.get("/me")
    async def me(pii: PiiPolicy = Depends(policy)) -> dict[str, bool]:
        return {"masking_enabled": pii.enabled, "masked": pii.masked}

    return router
