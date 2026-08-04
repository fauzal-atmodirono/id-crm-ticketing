"""DMS/TSP integration shell admin API -- CRUD for the operator-configured
DMS/TSP connection plus a "Test connection" endpoint, backing the
Integrations "DMS / TSP" card. Gated behind the `integration.manage`
permission via Phase 1's `require_permission`, matching
`pic_admin_router.py`'s wiring exactly.

The credential stays write-only end to end here, same as in
`dms_config_store.py`: PUT accepts an optional `credential` field and hands
it straight to `DmsConfigStore.save()`; GET builds its response from
`public_dict()`, which has no credential field to omit in the first place;
and the connection-test response is built only from `ProbeResult` (a status
plus `probe()`'s own sanitised message) -- never from `DmsConfigBody`, so
nothing the operator just typed can round-trip back to them.

One more leak path closed here: FastAPI's default `RequestValidationError`
handler echoes each error's raw submitted value back in an `"input"` key --
harmless for a department name, catastrophic for `credential` (e.g. a
`v-model.number` mis-bound on the eventual admin form, or a key pasted
unquoted into a raw JSON tool, sends a non-string/non-null value and the
422 response body reflects it verbatim). `install_credential_safe_error_handler`
strips `"input"` from any error whose `loc` names `credential`, leaving every
other field's (and every other router's) validation-error shape untouched.
FastAPI only allows validation-error handlers to be registered app-wide, not
per-router, so this must be called on the app object once (`main.py` does
this right after mounting this router; tests do it in their own app
fixture).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from chatbot.features.authz.deps import require_permission
from chatbot.features.chat.dms_client import probe as dms_probe
from chatbot.features.chat.dms_config_store import DmsConfig, public_dict

if TYPE_CHECKING:
    from fastapi import FastAPI

    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.dms_config_store import DmsConfigStore
    from chatbot.platform.config import Settings

_NOT_CONFIGURED = "unexpected_status"

# Field names whose raw submitted value must never appear in a validation-error
# response. Currently just the one secret this router accepts; kept as a set
# (not a single constant) so a future write-only field can join it without
# changing the handler's shape.
_SENSITIVE_LOC_FIELDS = {"credential"}


async def _credential_safe_validation_handler(
    request: Request,  # noqa: ARG001 -- required by FastAPI's handler signature
    exc: Exception,
) -> JSONResponse:
    # Starlette's add_exception_handler signature is contravariant on the
    # exception type (Callable[[Request, Exception], ...]), so the parameter
    # is typed broadly to satisfy mypy --strict; registration below only
    # ever binds this handler to RequestValidationError, so this branch is
    # unreachable in practice. Not an `assert` (stripped under `-O`) --
    # re-raising preserves "never silently swallow" if it's ever wrong.
    if not isinstance(exc, RequestValidationError):
        raise exc
    sanitized_errors = []
    for raw_error in exc.errors():
        error = dict(raw_error)
        loc = error.get("loc", ())
        if any(str(part) in _SENSITIVE_LOC_FIELDS for part in loc):
            error.pop("input", None)
        sanitized_errors.append(error)
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({"detail": sanitized_errors}),
    )


def install_credential_safe_error_handler(app: FastAPI) -> None:
    """Register `_credential_safe_validation_handler` on `app`. Call once,
    on the actual FastAPI app object -- see module docstring for why this
    can't live on the router itself.
    """
    app.add_exception_handler(RequestValidationError, _credential_safe_validation_handler)


def _empty_config() -> DmsConfig:
    """What GET returns before an operator has ever saved anything -- an
    "off" config, not a 404, so the admin form always has something to
    render.
    """
    return DmsConfig(
        enabled=False,
        provider_label="",
        base_url="",
        auth_type="",
        extra_header_name="",
        extra_header_value="",
        timeout_seconds=10.0,
        retries=0,
    )


class DmsConfigBody(BaseModel):
    enabled: bool = False
    provider_label: str = ""
    base_url: str = ""
    auth_type: str = ""
    extra_header_name: str = ""
    extra_header_value: str = ""
    timeout_seconds: float = 10.0
    retries: int = 0
    # Write-only: accepted here, never returned by GET. `None` (the default,
    # and what a form re-PUTting unrelated fields sends) means "keep
    # whatever credential is already stored" -- see DmsConfigStore.save().
    credential: str | None = None


def build_dms_admin_router(
    config_store: DmsConfigStore,
    authz_repo: AuthzRepository,
    validator: TokenValidator,
    settings: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/admin/integrations/dms", tags=["dms-admin"])
    manage_integration = require_permission(
        "integration.manage", repo=authz_repo, validator=validator, settings=settings
    )

    @router.get("", dependencies=[Depends(manage_integration)])
    async def get_config() -> dict:
        config = await config_store.get()
        return public_dict(config if config is not None else _empty_config())

    @router.put("", dependencies=[Depends(manage_integration)])
    async def put_config(body: DmsConfigBody) -> dict:
        if body.base_url and not body.base_url.startswith("https://"):
            raise HTTPException(status_code=400, detail="base_url must start with https://")
        config = DmsConfig(
            enabled=body.enabled,
            provider_label=body.provider_label,
            base_url=body.base_url,
            auth_type=body.auth_type,
            extra_header_name=body.extra_header_name,
            extra_header_value=body.extra_header_value,
            timeout_seconds=body.timeout_seconds,
            retries=body.retries,
        )
        await config_store.save(config, credential=body.credential)
        return public_dict(config)

    @router.post("/test", dependencies=[Depends(manage_integration)])
    async def test_connection() -> dict:
        config = await config_store.get()
        if config is None or not config.base_url:
            return {
                "status": _NOT_CONFIGURED,
                "message": "DMS/TSP integration is not configured yet.",
            }

        credential = await config_store.get_credential()
        if not credential:
            return {
                "status": _NOT_CONFIGURED,
                "message": "No credential is stored for this integration.",
            }

        async with httpx.AsyncClient() as client:
            result = await dms_probe(config, credential, client)
        return {"status": result.status, "message": result.message}

    return router
