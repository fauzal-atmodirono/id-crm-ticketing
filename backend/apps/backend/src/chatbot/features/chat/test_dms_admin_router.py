"""Router, permission, and credential-leak tests for
`/admin/integrations/dms`. The one invariant every test in this file is
ultimately protecting: the credential appears in NO response body from any
of the three endpoints, under any outcome (empty config, saved config,
connection test in any status). That is asserted directly by scanning the
raw serialized response text, not just by checking individual JSON keys, so
a credential smuggled into an unexpected field would still be caught.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.db import build_engine as build_authz_engine
from chatbot.features.authz.db import build_session_maker as build_authz_session_maker
from chatbot.features.authz.db import init_authz_db
from chatbot.features.authz.identity import TokenValidator
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.seed import seed_defaults
from chatbot.features.chat.dms_admin_router import (
    build_dms_admin_router,
    install_credential_safe_error_handler,
)
from chatbot.features.chat.dms_client import ProbeResult
from chatbot.features.chat.dms_config_store import DmsConfigStore
from chatbot.platform.config import get_settings

HEADERS = {
    "x-chatwoot-access-token": "tok-abc",
    "x-chatwoot-client": "client-1",
    "x-chatwoot-uid": "uid-1",
}

CREDENTIAL = "super-secret-dms-token-xyz"

VALID_BODY: dict[str, Any] = {
    "enabled": True,
    "provider_label": "Proton DMS",
    "base_url": "https://dms.example.com/health",
    "auth_type": "api_key_header",
    "extra_header_name": "X-Tenant",
    "extra_header_value": "proton",
    "timeout_seconds": 5.0,
    "retries": 2,
    "credential": CREDENTIAL,
}


async def _build_authz_repo(tmp_path, name: str) -> AuthzRepository:
    authz_engine = build_authz_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}_authz.db")
    await init_authz_db(authz_engine)
    return AuthzRepository(build_authz_session_maker(authz_engine))


def _app_with_router(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    # Mirrors main.py's wiring exactly: the credential-safe 422 handler is
    # app-scoped, so it must be installed here too for these tests to
    # exercise the same behaviour production actually has.
    install_credential_safe_error_handler(app)
    return TestClient(app)


class _FakeDoc:
    def __init__(self, store: dict[str, dict[str, Any]], key: str) -> None:
        self._store = store
        self._key = key

    def get(self) -> MagicMock:
        snap = MagicMock()
        data = self._store.get(self._key)
        snap.exists = data is not None
        snap.to_dict.return_value = data or {}
        return snap

    def set(self, data: dict[str, Any]) -> None:
        self._store[self._key] = data

    def delete(self) -> None:
        self._store.pop(self._key, None)


class _FakeCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, key: str) -> _FakeDoc:
        return _FakeDoc(self._store, key)

    def stream(self) -> list[MagicMock]:
        return [MagicMock(to_dict=MagicMock(return_value=v)) for v in self._store.values()]


class _FakeFirestoreClient:
    """In-memory stand-in for google.cloud.firestore.Client. Same double
    used by test_pic_admin_router.py / test_dms_config_store.py.
    """

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._collections.setdefault(name, {}))


def _build_store(settings) -> DmsConfigStore:
    return DmsConfigStore(settings)


async def _authorized_client(tmp_path, name: str, user_id: int, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, name)
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=user_id, role_id="administrator")
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": user_id})
    )
    validator = TokenValidator(settings)
    store = _build_store(settings)
    router = build_dms_admin_router(store, authz_repo, validator, settings)
    client = _app_with_router(router)
    return client, store


# --- permission gating -------------------------------------------------


@pytest.mark.asyncio
async def test_get_without_permission_returns_403(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "no_perm")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=9, role_id="agent")  # lacks integration.manage

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 9})
    )
    validator = TokenValidator(settings)
    store = _build_store(settings)
    router = build_dms_admin_router(store, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.get("/admin/integrations/dms", headers=HEADERS)
    assert res.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("get", "/admin/integrations/dms", {}),
        ("put", "/admin/integrations/dms", {"json": VALID_BODY}),
        ("post", "/admin/integrations/dms/test", {}),
    ],
)
async def test_all_three_endpoints_require_the_permission(
    tmp_path, respx_mock, method: str, path: str, kwargs: dict
):
    """GET's 403 is covered above; this locks in that PUT and POST /test
    share the exact same `manage_integration` dependency rather than just
    happening to behave the same today -- a future edit that drops
    `dependencies=[...]` from one route would be caught here.
    """
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, f"denied_{method}")
    await seed_defaults(authz_repo)
    await authz_repo.assign_role(chatwoot_user_id=21, role_id="agent")  # lacks integration.manage

    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 21})
    )
    validator = TokenValidator(settings)
    store = _build_store(settings)
    router = build_dms_admin_router(store, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = getattr(client, method)(path, headers=HEADERS, **kwargs)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(tmp_path):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    authz_repo = await _build_authz_repo(tmp_path, "unauth")
    await seed_defaults(authz_repo)
    validator = TokenValidator(settings)
    store = _build_store(settings)
    router = build_dms_admin_router(store, authz_repo, validator, settings)
    client = _app_with_router(router)

    res = client.get("/admin/integrations/dms")
    assert res.status_code == 401


# --- GET -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_empty_config_with_no_credential_key(tmp_path, respx_mock):
    client, _store = await _authorized_client(tmp_path, "get_empty", 10, respx_mock)

    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        res = client.get("/admin/integrations/dms", headers=HEADERS)
        assert res.status_code == 200
        body = res.json()
        assert "credential" not in body
        assert body["enabled"] is False
        assert body["base_url"] == ""


@pytest.mark.asyncio
async def test_get_returns_saved_config_with_no_credential_key(tmp_path, respx_mock):
    client, _store = await _authorized_client(tmp_path, "get_saved", 11, respx_mock)

    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        put_res = client.put("/admin/integrations/dms", json=VALID_BODY, headers=HEADERS)
        assert put_res.status_code == 200

        get_res = client.get("/admin/integrations/dms", headers=HEADERS)
        body = get_res.json()
        assert "credential" not in body
        assert body["provider_label"] == "Proton DMS"
        assert body["base_url"] == "https://dms.example.com/health"
        assert body["extra_header_value"] == "proton"  # known exposure, see report


# --- PUT -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_saves_config(tmp_path, respx_mock):
    client, store = await _authorized_client(tmp_path, "put_saves", 12, respx_mock)

    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        res = client.put("/admin/integrations/dms", json=VALID_BODY, headers=HEADERS)
        assert res.status_code == 200

        saved = await store.get()
        assert saved is not None
        assert saved.enabled is True
        assert saved.base_url == "https://dms.example.com/health"
        assert await store.get_credential() == CREDENTIAL


@pytest.mark.asyncio
async def test_put_without_credential_keeps_the_stored_one(tmp_path, respx_mock):
    client, store = await _authorized_client(tmp_path, "put_keeps_cred", 13, respx_mock)

    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        first = client.put("/admin/integrations/dms", json=VALID_BODY, headers=HEADERS)
        assert first.status_code == 200
        assert await store.get_credential() == CREDENTIAL

        body_no_cred = {**VALID_BODY, "provider_label": "Proton DMS v2"}
        del body_no_cred["credential"]
        second = client.put("/admin/integrations/dms", json=body_no_cred, headers=HEADERS)
        assert second.status_code == 200
        assert second.json()["provider_label"] == "Proton DMS v2"

        # Credential is untouched even though it wasn't resent.
        assert await store.get_credential() == CREDENTIAL


@pytest.mark.asyncio
async def test_put_empty_string_credential_overwrites_the_stored_one(
    tmp_path, respx_mock
):
    """`credential=""` (submitted explicitly, not omitted) is NOT the same as
    omitting the field: DmsConfigStore.save() treats any non-`None` value as
    "replace", so this blanks out a previously stored secret. That's
    consistent with the brief's contract ("None means keep"), not a bug --
    but it means Task 5's form must NEVER send an empty string to mean
    "leave unchanged"; only omitting the field (or sending null) does that.
    Pinning current behaviour so a future change here is deliberate, not
    accidental.
    """
    client, store = await _authorized_client(tmp_path, "put_empty_cred", 22, respx_mock)

    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        first = client.put("/admin/integrations/dms", json=VALID_BODY, headers=HEADERS)
        assert first.status_code == 200
        assert await store.get_credential() == CREDENTIAL

        body_blank_cred = {**VALID_BODY, "credential": ""}
        second = client.put("/admin/integrations/dms", json=body_blank_cred, headers=HEADERS)
        assert second.status_code == 200

        assert await store.get_credential() == ""


@pytest.mark.asyncio
async def test_put_rejects_non_https_base_url(tmp_path, respx_mock):
    client, _store = await _authorized_client(tmp_path, "put_rejects", 14, respx_mock)

    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        bad_body = {**VALID_BODY, "base_url": "http://dms.example.com/health"}
        res = client.put("/admin/integrations/dms", json=bad_body, headers=HEADERS)
        assert res.status_code == 400


# --- POST /test --------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,message",
    [
        ("reachable", "Proton DMS is reachable (HTTP 200)."),
        ("auth_failed", "Proton DMS rejected the credential (HTTP 401)."),
        ("timeout", "Proton DMS did not respond within 5s."),
        ("unexpected_status", "Proton DMS responded with HTTP 500."),
    ],
)
async def test_test_connection_maps_each_probe_outcome(
    tmp_path, respx_mock, status: str, message: str
):
    client, _store = await _authorized_client(tmp_path, f"probe_{status}", 15, respx_mock)

    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        put_res = client.put("/admin/integrations/dms", json=VALID_BODY, headers=HEADERS)
        assert put_res.status_code == 200

        with patch(
            "chatbot.features.chat.dms_admin_router.dms_probe",
            autospec=True,
        ) as mock_probe:
            mock_probe.return_value = ProbeResult(status=status, message=message)
            res = client.post("/admin/integrations/dms/test", headers=HEADERS)

        assert res.status_code == 200
        body = res.json()
        assert body == {"status": status, "message": message}


@pytest.mark.asyncio
async def test_test_connection_distinguishes_wrong_path_from_wrong_key(
    tmp_path, respx_mock
):
    """A 404 (wrong path) and a 401 (wrong key) must never collapse into the
    same status or message -- an operator debugging "test connection failed"
    needs to know which one it is.
    """
    client, _store = await _authorized_client(tmp_path, "probe_distinguish", 16, respx_mock)

    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        put_res = client.put("/admin/integrations/dms", json=VALID_BODY, headers=HEADERS)
        assert put_res.status_code == 200

        with patch(
            "chatbot.features.chat.dms_admin_router.dms_probe",
            autospec=True,
        ) as mock_probe:
            mock_probe.return_value = ProbeResult(
                status="unexpected_status",
                message="Proton DMS responded with HTTP 404.",
            )
            wrong_path_res = client.post("/admin/integrations/dms/test", headers=HEADERS)

            mock_probe.return_value = ProbeResult(
                status="auth_failed",
                message="Proton DMS rejected the credential (HTTP 401).",
            )
            wrong_key_res = client.post("/admin/integrations/dms/test", headers=HEADERS)

    wrong_path_body = wrong_path_res.json()
    wrong_key_body = wrong_key_res.json()
    assert wrong_path_body["status"] != wrong_key_body["status"]
    assert wrong_path_body["message"] != wrong_key_body["message"]
    assert "404" in wrong_path_body["message"]
    assert "401" in wrong_key_body["message"]
    assert "credential" in wrong_key_body["message"]


@pytest.mark.asyncio
async def test_test_connection_reports_not_configured_without_base_url(
    tmp_path, respx_mock
):
    client, _store = await _authorized_client(tmp_path, "probe_unconfigured", 17, respx_mock)

    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        res = client.post("/admin/integrations/dms/test", headers=HEADERS)
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "unexpected_status"
        assert "not configured" in body["message"].lower()


@pytest.mark.asyncio
async def test_test_connection_reports_not_configured_without_credential(
    tmp_path, respx_mock
):
    client, _store = await _authorized_client(tmp_path, "probe_no_cred", 18, respx_mock)

    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        body_no_cred = {k: v for k, v in VALID_BODY.items() if k != "credential"}
        put_res = client.put("/admin/integrations/dms", json=body_no_cred, headers=HEADERS)
        assert put_res.status_code == 200

        res = client.post("/admin/integrations/dms/test", headers=HEADERS)
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "unexpected_status"
        assert "credential" in body["message"].lower()


# --- the credential leaks nowhere, from any endpoint ------------------------


@pytest.mark.asyncio
async def test_credential_appears_in_no_response_body_from_any_endpoint(
    tmp_path, respx_mock
):
    client, _store = await _authorized_client(tmp_path, "no_leak", 19, respx_mock)

    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        put_res = client.put("/admin/integrations/dms", json=VALID_BODY, headers=HEADERS)
        assert CREDENTIAL not in put_res.text

        get_res = client.get("/admin/integrations/dms", headers=HEADERS)
        assert CREDENTIAL not in get_res.text

        with patch(
            "chatbot.features.chat.dms_admin_router.dms_probe",
            autospec=True,
        ) as mock_probe:
            mock_probe.return_value = ProbeResult(status="reachable", message="ok (HTTP 200).")
            test_res = client.post("/admin/integrations/dms/test", headers=HEADERS)
        assert CREDENTIAL not in test_res.text

        # Bad-request path too.
        bad_body = {**VALID_BODY, "base_url": "http://insecure.example.com"}
        bad_res = client.put("/admin/integrations/dms", json=bad_body, headers=HEADERS)
        assert bad_res.status_code == 400
        assert CREDENTIAL not in bad_res.text

        # 422 path: a non-string credential fails Pydantic validation.
        # FastAPI's default handler would echo the raw submitted value back
        # in the error's "input" key -- this must not happen here.
        malformed_res = client.put(
            "/admin/integrations/dms",
            json={**VALID_BODY, "credential": 4111111111111111},
            headers=HEADERS,
        )
        assert malformed_res.status_code == 422
        assert "4111111111111111" not in malformed_res.text


@pytest.mark.asyncio
async def test_malformed_credential_422_never_echoes_the_submitted_value(
    tmp_path, respx_mock
):
    """A non-string credential (e.g. a frontend `v-model.number` mis-bind on
    the eventual admin form, or a bare number pasted into a raw JSON
    request) fails Pydantic's `str` validation and 422s. FastAPI's default
    `RequestValidationError` handler echoes the raw submitted value back in
    each error's `"input"` key -- fine for a department name, never
    acceptable for `credential`. `install_credential_safe_error_handler`
    (wired into the test app by `_app_with_router`) must strip it.
    """
    client, _store = await _authorized_client(tmp_path, "malformed_cred", 23, respx_mock)
    malformed_credential = 4111111111111111

    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        res = client.put(
            "/admin/integrations/dms",
            json={**VALID_BODY, "credential": malformed_credential},
            headers=HEADERS,
        )

    assert res.status_code == 422
    assert str(malformed_credential) not in res.text

    body = res.json()
    credential_errors = [e for e in body["detail"] if "credential" in e.get("loc", [])]
    assert credential_errors, "expected a validation error naming the credential field"
    assert all("input" not in error for error in credential_errors)


@pytest.mark.asyncio
async def test_malformed_non_credential_field_still_echoes_its_input(
    tmp_path, respx_mock
):
    """Sanity check on the handler's precision: a validation error on a
    NON-secret field (retries expects an int) must still carry its "input"
    key as FastAPI does by default -- the handler must only scrub fields
    named in `_SENSITIVE_LOC_FIELDS`, not blanket-strip every error.
    """
    client, _store = await _authorized_client(tmp_path, "malformed_other", 24, respx_mock)

    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()

        res = client.put(
            "/admin/integrations/dms",
            json={**VALID_BODY, "retries": "not-an-int"},
            headers=HEADERS,
        )

    assert res.status_code == 422
    body = res.json()
    retries_errors = [e for e in body["detail"] if "retries" in e.get("loc", [])]
    assert retries_errors
    assert retries_errors[0].get("input") == "not-an-int"


# --- RBAC-disabled fallback --------------------------------------------


@pytest.mark.asyncio
async def test_rbac_disabled_falls_back_to_shared_secret(tmp_path):
    settings = get_settings().model_copy(
        update={"rbac_enabled": False, "faq_admin_api_key": "secret123"}
    )
    authz_repo = await _build_authz_repo(tmp_path, "disabled")
    validator = TokenValidator(settings)
    store = _build_store(settings)
    router = build_dms_admin_router(store, authz_repo, validator, settings)
    client = _app_with_router(router)

    assert client.get("/admin/integrations/dms").status_code == 401
    assert (
        client.get(
            "/admin/integrations/dms", headers={"x-api-key": "wrong"}
        ).status_code
        == 401
    )
    with patch(
        "chatbot.features.chat.dms_config_store.firestore.Client", autospec=True
    ) as MockClient:
        MockClient.return_value = _FakeFirestoreClient()
        assert (
            client.get(
                "/admin/integrations/dms", headers={"x-api-key": "secret123"}
            ).status_code
            == 200
        )
