"""P12 task 3 -- the mock-DMS sandbox guard, reached from the real app.

`MockDmsClient.__init__` refuses to construct outside a sandbox environment, and
`test_dms_mock_guard.py` proves that by passing `environment="production"` by
hand. The bug was one layer up, in the only real call site: `main.py` constructed
`MockDmsClient()` with no argument, so `environment` took the class's own default
of `"sandbox"` and **the guard could not fire on any tenant**. A production
deployment that set `DMS_MOCK_CLIENT_ENABLED=true` -- to demo the card, or by
copying a sandbox env -- put "Proton X50 (Demo data)" and a fabricated 20,000 km
service record on a real customer's Customer 360 panel, for an agent to quote
back to them. The plan calls the refusal "the load-bearing guard" because the
per-field "(Demo data)" suffixes can be cropped out of a screenshot.

So these tests boot `bootstrap_application()` rather than the class, which is the
only layer at which that bug was visible. The twelfth instance in this run of
built-tested-unreachable code, every one of which survived because its test
called the inner function with hand-supplied arguments.
"""

from __future__ import annotations

from typing import Any

import pytest


_BASE_ENV = {
    "GOOGLE_GENAI_USE_VERTEXAI": "true",
    "GOOGLE_CLOUD_PROJECT": "test-project",
    "GOOGLE_CLOUD_LOCATION": "us-central1",
    "PROTON_BACKEND_KEY": "test_key",
    "CHATWOOT_WEBHOOK_SECRET": "test_secret",
}


@pytest.fixture(autouse=True)
def _uncached_settings() -> Any:
    """Leave no cached `Settings` behind, in either direction.

    `chatbot.main` ends with a module-level `app = bootstrap_application()`, so
    the first import of it in a process boots the app using whatever `Settings`
    is cached at that moment. A test here that leaves a mock-enabled production
    environment cached would therefore make an unrelated test file's *import*
    raise -- and since a failed import is not kept in `sys.modules`, every
    later importer pays for it too. That is a real cross-file failure this file
    hit while being written, not a hypothetical.
    """
    from chatbot.platform.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _boot(monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    from chatbot.platform.config import get_settings

    # Import `chatbot.main` under a deliberately SAFE environment first, so its
    # module-level boot cannot be the thing that raises. The refusals below must
    # come from the explicit `bootstrap_application()` call, which is the code
    # path a container actually takes -- and an import that succeeds stays in
    # `sys.modules`, so nothing downstream re-runs it.
    for name, value in _BASE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DMS_MOCK_CLIENT_ENABLED", "false")
    get_settings.cache_clear()

    import chatbot.main

    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    return chatbot.main.bootstrap_application()


def test_the_mock_dms_client_refuses_to_boot_a_production_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The headline: enabling the mock on a production-like deployment must
    stop the deployment, not decorate a real customer's panel with demo data.
    Fails if `environment=` is dropped from `main.py`'s construction.
    """
    with pytest.raises(ValueError, match="refused activation outside sandbox environment"):
        _boot(monkeypatch, DMS_MOCK_CLIENT_ENABLED="true", APP_ENVIRONMENT="production")


def test_the_refusal_is_the_default_when_no_environment_is_declared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`APP_ENVIRONMENT` unset must mean refused, not permitted.

    A setting that defaults to the permissive value is the same unreachable
    guard by another name: the tenant most likely to have copied a sandbox env
    is also the one least likely to have declared its environment.
    """
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)
    assert "APP_ENVIRONMENT" not in __import__("os").environ

    with pytest.raises(ValueError, match="refused activation outside sandbox environment"):
        _boot(monkeypatch, DMS_MOCK_CLIENT_ENABLED="true")


def test_a_sandbox_environment_still_gets_the_mock_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard must not make the demo impossible -- that is the whole reason
    `MockDmsClient` exists, and a guard nobody can satisfy gets deleted.
    """
    app = _boot(monkeypatch, DMS_MOCK_CLIENT_ENABLED="true", APP_ENVIRONMENT="sandbox")
    assert app is not None


def test_the_flag_off_boots_on_any_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every tenant today has `DMS_MOCK_CLIENT_ENABLED=false`, so this is the
    assertion that the new setting changes nothing for any of them: with the
    flag off, `MockDmsClient` is never constructed and `app_environment` is
    never read.
    """
    app = _boot(monkeypatch, DMS_MOCK_CLIENT_ENABLED="false", APP_ENVIRONMENT="production")
    assert app is not None


def test_omitting_the_environment_argument_is_a_type_error_not_a_silent_pass() -> None:
    """The property that stops this regressing the way it originally shipped.

    `environment` has no default, so a future edit to `main.py` that drops the
    argument fails loudly at the call site instead of quietly re-selecting
    "sandbox" for a production tenant.
    """
    from chatbot.features.chat.dms_client import MockDmsClient

    with pytest.raises(TypeError):
        MockDmsClient()  # type: ignore[call-arg]
