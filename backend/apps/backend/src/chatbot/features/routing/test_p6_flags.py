"""P6 task 11 -- the seven flags, their defaults, and the env template.

The defaults are the deliverable. Every P6 flag is off, so a tenant that
redeploys this package sees no poller, no presence events, no alerts, no ACW,
no sweeper and no dashboard until somebody decides otherwise.

`test_all_seven_settings_are_present_in_example_env` deliberately does **not**
assert against a list of names written here. A hardcoded list would pass
forever while quietly failing to notice the thing that actually goes wrong: a
setting added to `config.py` and forgotten in the operator-facing
`deploy/tenants/example.env`, which is invisible until an operator goes looking
for a switch that no document mentions. Instead the P6 fields are read out of
`Settings.model_fields` -- pydantic preserves declaration order -- by slicing
between the last Phase-5 field and the first field after the P6 block. Add a
setting anywhere inside that block and this test starts requiring it in
`example.env` on its own. Rename either anchor and it fails loudly rather than
silently covering nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chatbot.platform.config import Settings

_REPO_ROOT = Path(__file__).resolve().parents[7]
EXAMPLE_ENV_PATH = _REPO_ROOT / "deploy" / "tenants" / "example.env"
EXAMPLE_ENV = EXAMPLE_ENV_PATH.read_text(encoding="utf-8")

# The two anchors bounding config.py's `# --- P6: ... ---` block. Both are
# real fields immediately outside it, not P6 settings themselves.
_LAST_PHASE_5_FIELD = "routing_max_concurrent_per_agent"
_FIRST_FIELD_AFTER_P6 = "live_faq_collection"

EXPECTED_FLAG_COUNT = 7


def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings with every P6 var removed from the process environment.

    `_env_file=None` is not enough: pydantic-settings reads `os.environ`
    regardless, and the both-flag-states gate
    (`deploy/scripts/check-suites-both-flag-states.sh`) runs this suite a second
    time with all seven flags exported as `true`. A test that asserted a default
    without clearing the environment first would assert the opposite of its own
    name on that run -- which is worse than no test, because the flags-ON run is
    the one that exists to find defects.
    """
    for name in _p6_field_names():
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.delenv("ROUTING_ENABLED", raising=False)
    return Settings(_env_file=None)


def _p6_field_names() -> list[str]:
    """Every Settings field declared inside config.py's P6 block, in order."""
    names = list(Settings.model_fields)
    start = names.index(_LAST_PHASE_5_FIELD) + 1
    end = names.index(_FIRST_FIELD_AFTER_P6)
    assert start < end, "the P6 block anchors are in the wrong order"
    return names[start:end]


def _p6_flag_names() -> list[str]:
    """The boolean subset of the P6 block -- i.e. the seven feature flags."""
    fields = Settings.model_fields
    return [name for name in _p6_field_names() if fields[name].annotation is bool]


def _example_env_keys() -> set[str]:
    """Parse example.env into the set of variable names it actually defines.

    A real parse, not a substring search: a name that only appears inside a
    comment must not count as documented-and-set, or the test would pass on a
    file that mentions a flag in prose and never gives it a value.
    """
    keys: set[str] = set()
    for raw in EXAMPLE_ENV.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def test_all_seven_settings_are_present_in_example_env():
    flags = _p6_flag_names()
    assert len(flags) == EXPECTED_FLAG_COUNT, (
        f"expected {EXPECTED_FLAG_COUNT} boolean P6 flags, found {flags}"
    )

    documented = _example_env_keys()
    # Every P6 setting, tunables included -- an undocumented tunable is just as
    # invisible to an operator as an undocumented flag.
    missing = [name for name in _p6_field_names() if name.upper() not in documented]
    assert not missing, (
        f"P6 settings in config.py but missing from {EXAMPLE_ENV_PATH.name}: {missing}"
    )


def test_all_seven_default_to_false(monkeypatch):
    settings = _settings(monkeypatch)
    flags = _p6_flag_names()
    assert len(flags) == EXPECTED_FLAG_COUNT
    on = [name for name in flags if getattr(settings, name) is not False]
    assert not on, f"P6 flags not defaulting to False: {on}"


def test_routing_enabled_still_defaults_to_false(monkeypatch):
    """P6 neither turns the Phase-5 routing engine on nor claims it as one of
    its own flags. A routing engine is switched on deliberately, per tenant.
    """
    assert _settings(monkeypatch).routing_enabled is False
    assert "routing_enabled" not in _p6_field_names()
    assert "ROUTING_ENABLED=false" in EXAMPLE_ENV


def test_both_services_start_with_none_of_the_new_vars_set(monkeypatch):
    """The backend half of the two-service boot check.

    The `agent` service's half lives in `agent/tests/test_p6_flags.py` under
    the same test name: the two services are separate Python packages with
    separate virtualenvs and neither can import the other, so "both services
    start" is a pair of tests, one per suite, rather than one test that lies
    about its coverage.
    """
    for name in _p6_field_names():
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    from chatbot.main import bootstrap_application  # noqa: PLC0415
    from chatbot.platform.config import get_settings  # noqa: PLC0415

    get_settings.cache_clear()
    try:
        app = bootstrap_application()
        paths = app.openapi()["paths"]
        # Mounted regardless of any P6 flag, exactly as before this package.
        assert "/routing/assign" in paths
        # Gated on presence tracking, which is off.
        assert "/admin/workforce" not in paths
    finally:
        get_settings.cache_clear()
