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

Writes from a masked caller (``PiiPolicy.reconcile_write``): the SPA edit
forms re-send every field of the row they loaded, masked fields included
(the RSA page's ``buildPayload`` does). A field re-sent EXACTLY as it was
shown (equal to the masked rendering of the stored value) is unchanged and is
dropped from the write; any other value still carrying a mask shape is
refused (400), so a masked value can never overwrite the real one. Callers
who are not masked are never checked: their text may contain bullets freely.

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

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Header, HTTPException

from chatbot.features.authz.deps import is_platform_superadmin
from chatbot.features.privacy.pii import contains_mask, mask_pii

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

PII_VIEW = "pii.view"
# GET /authz/me/pii answers per session triplet from a short cache: the fork
# asks it from every contact view mount. Short, so a granted/revoked pii.view
# takes effect within the TTL; bounded, so it cannot grow without limit.
ME_CACHE_SECONDS = 30.0
_ME_CACHE_MAX = 1000


@dataclass(frozen=True)
class PiiPolicy:
    enabled: bool
    masked: bool

    def apply(self, value: Any) -> Any:
        """Masked deep copy when this caller is masked; else ``value`` itself."""
        return mask_pii(value) if self.masked else value

    def refuse_masked_write(self, value: Any) -> None:
        """400 when a masked caller's write carries a mask shape. Callers who
        are not masked (and flag off) are never checked."""
        if self.masked and contains_mask(value):
            raise HTTPException(
                status_code=400,
                detail="A masked value cannot be saved. Enter the full value, or leave the field unchanged.",
            )

    def reconcile_write(
        self, incoming: dict[str, Any], current: dict[str, Any] | None
    ) -> dict[str, Any]:
        """For a masked caller: drop fields re-sent unchanged in their masked
        form, then refuse any remaining mask shape. Otherwise ``incoming``
        itself, untouched."""
        if not self.masked:
            return incoming
        out = dict(incoming)
        if current:
            shown = mask_pii(current)
            for key, value in incoming.items():
                if (
                    isinstance(value, str)
                    and key in current
                    and value == shown.get(key)
                    and value != current.get(key)
                ):
                    del out[key]
        self.refuse_masked_write(out)
        return out


UNMASKED = PiiPolicy(enabled=False, masked=False)
_MASKED = PiiPolicy(enabled=True, masked=True)


async def resolve_pii_policy(
    settings: Settings,
    repo: AuthzRepository | None,
    validator: TokenValidator | None,
    token: str | None,
    client: str | None,
    uid: str | None,
) -> PiiPolicy:
    """The caller's policy. Never raises; every doubt resolves to masked."""
    if not getattr(settings, "pii_masking_enabled", False):
        return UNMASKED
    if not (token and client and uid) or validator is None or repo is None:
        return _MASKED
    try:
        identity = await validator.resolve_identity(token, client, uid)
        if identity is None:
            return _MASKED
        user_id, is_super_admin_type = identity
        if is_platform_superadmin(user_id, is_super_admin_type):
            return PiiPolicy(enabled=True, masked=False)
        perms = await repo.permissions_for_user(user_id)
    except Exception as exc:
        _log.warning("pii_policy_lookup_failed", error_type=type(exc).__name__)
        return _MASKED
    return PiiPolicy(enabled=True, masked=PII_VIEW not in perms)


def build_pii_dependency(
    settings: Settings,
    *,
    repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
):
    async def _policy(
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> PiiPolicy:
        return await resolve_pii_policy(
            settings, repo, validator, x_chatwoot_access_token, x_chatwoot_client, x_chatwoot_uid
        )

    return _policy


def build_privacy_router(
    settings: Settings,
    *,
    repo: AuthzRepository | None = None,
    validator: TokenValidator | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> APIRouter:
    """``GET /authz/me/pii`` -- the fork's contact views ask this whether to mask.

    Under ``/authz/`` because every tenant's Caddy vhost already routes that
    prefix to the backend (a new prefix would fall through to Chatwoot's 404,
    which the fork reads as "older backend, do not mask"). Mounted on every
    tenant, separately from the RBAC-only /authz router, so the fork can call
    it unconditionally: flag off answers ``masking_enabled: false``. Not gated
    (it reveals only the caller's own masking state).
    """
    router = APIRouter(prefix="/authz", tags=["privacy"])
    cache: dict[tuple[str, str, str], tuple[float, PiiPolicy]] = {}

    @router.get("/me/pii")
    async def me(
        x_chatwoot_access_token: str | None = Header(default=None),
        x_chatwoot_client: str | None = Header(default=None),
        x_chatwoot_uid: str | None = Header(default=None),
    ) -> dict[str, bool]:
        triplet = (x_chatwoot_access_token or "", x_chatwoot_client or "", x_chatwoot_uid or "")
        now = clock()
        hit = cache.get(triplet)
        if hit is not None and now - hit[0] < ME_CACHE_SECONDS:
            pii = hit[1]
        else:
            pii = await resolve_pii_policy(settings, repo, validator, *triplet)
            if len(cache) >= _ME_CACHE_MAX:
                cache.clear()
            cache[triplet] = (now, pii)
        return {"masking_enabled": pii.enabled, "masked": pii.masked}

    return router


__all__ = [
    "PII_VIEW",
    "UNMASKED",
    "PiiPolicy",
    "build_pii_dependency",
    "build_privacy_router",
    "resolve_pii_policy",
]
