"""P8 task 10 -- the six settings, their defaults, and the three files that
have to agree with them.

The defaults are the deliverable. With none of the six set, a tenant that
redeploys P8 gets byte-identical behaviour to before it: no Gemini call is
metered, `GET /metrics/ai-cost` and the three `token_usage` views do not exist,
no NPS question is ever asked (so `v_nps_by_agent` stays correctly empty rather
than sparse), CSAT stays channel-level only with `v_csat` untouched, and QA
stays today's channel-agnostic manual rubric.

Three traps this file is written specifically to avoid, all three of which have
already caught this repo:

1. **`Settings(_env_file=None)` does not stop pydantic-settings reading
   `os.environ`.** `deploy/scripts/check-suites-both-flag-states.sh` runs this
   suite a second time with all six exported at their ON values, so a defaults
   test that did not clear the environment first would assert the exact opposite
   of its own name on that run -- and, being an equality assertion, either fail
   there or (worse, for a `!= default` shape) pass while proving nothing. Three
   such tests were found and fixed in P6 and a fourth in already-committed P7
   code. `_settings()` clears every P8 variable and then *asserts* they are
   gone, so deleting the delenv loop breaks the test rather than quietly
   hollowing it out. Every defaults assertion in this file goes through it.
2. **A hardcoded list of names.** It would pass forever while missing the thing
   that actually goes wrong: a setting added to `config.py` and forgotten in the
   operator-facing `deploy/tenants/example.env` or in the flags-ON gate, neither
   of which any other test reads. The P8 fields are read out of
   `Settings.model_fields` (pydantic preserves declaration order), sliced
   between the last pre-P8 field and P8's own last field, so adding a setting
   inside that block makes this file start requiring it in both places on its
   own.
3. **A flag missing from the flags-ON gate.** P5's two flags were absent from
   that script for an entire package -- their on-paths had never once executed
   despite a fully green suite. Checked here rather than trusted, which is the
   only thing that makes the gate meaningful. Adding P8's six lines found five
   genuine failures on the ON run (four pre-existing CSAT-path tests that
   inherited `NPS_SAMPLE_RATE` from the environment, and one bare-`Settings()`
   defaults test in `platform/test_config_qa.py` -- trap 1 exactly).

P8 adds nothing to the `agent` service, so
`test_both_services_start_with_none_of_them_set` checks that claim rather than
assuming it: `agent/app/config.py` must not name any of the six.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chatbot.platform.config import Settings

_REPO_ROOT = Path(__file__).resolve().parents[5]
EXAMPLE_ENV_PATH = _REPO_ROOT / "deploy" / "tenants" / "example.env"
FLAGS_ON_SCRIPT_PATH = _REPO_ROOT / "deploy" / "scripts" / "check-suites-both-flag-states.sh"
AGENT_CONFIG_PATH = _REPO_ROOT / "agent" / "app" / "config.py"

# Fail here rather than 30 lines later with an empty-file mystery if this file
# is ever moved to a different depth.
assert EXAMPLE_ENV_PATH.is_file(), f"example.env not found at {EXAMPLE_ENV_PATH}"
assert FLAGS_ON_SCRIPT_PATH.is_file(), f"flag-state script not found at {FLAGS_ON_SCRIPT_PATH}"
assert AGENT_CONFIG_PATH.is_file(), f"agent config not found at {AGENT_CONFIG_PATH}"

EXAMPLE_ENV = EXAMPLE_ENV_PATH.read_text(encoding="utf-8")
FLAGS_ON_SCRIPT = FLAGS_ON_SCRIPT_PATH.read_text(encoding="utf-8")
AGENT_CONFIG = AGENT_CONFIG_PATH.read_text(encoding="utf-8")

# The anchors bounding config.py's `# --- P8: AI & agent measurement ---` block.
# The opening one is P7's own last field, immediately before it; the closing one
# is P8's own last field, inclusive -- deliberately not "the first field after
# the block", because P8 currently sits at the end of the Settings class and a
# later package will append its own settings there. Anchoring on the neighbour
# is what made P6's equivalent test start asserting P7's defaults.
_LAST_FIELD_BEFORE_P8 = "auto_summary_on_resolve_enabled"
_LAST_P8_FIELD = "call_qa_enabled"

EXPECTED_SETTING_COUNT = 6
EXPECTED_FLAG_COUNT = 4  # the six minus nps_sample_rate (float) and
# csat_ranking_min_samples (int), for which "defaults to false" is not the
# right assertion -- they get one test each below.


def _p8_field_names() -> list[str]:
    """Every Settings field declared inside config.py's P8 block, in order."""
    names = list(Settings.model_fields)
    start = names.index(_LAST_FIELD_BEFORE_P8) + 1
    end = names.index(_LAST_P8_FIELD) + 1
    assert start < end, "the P8 block anchors are in the wrong order"
    return names[start:end]


def _p8_flag_names() -> list[str]:
    """The boolean subset of the P8 block -- i.e. the four feature flags."""
    fields = Settings.model_fields
    return [name for name in _p8_field_names() if fields[name].annotation is bool]


def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings with every P8 variable removed from the process environment.

    See this module's docstring, trap 1: without the delenv loop below, every
    defaults test in this file asserts nothing (or the opposite of its name) on
    the flags-ON run.
    """
    for name in _p8_field_names():
        monkeypatch.delenv(name.upper(), raising=False)
    leaked = [name.upper() for name in _p8_field_names() if name.upper() in os.environ]
    assert not leaked, f"P8 variables still in the environment: {leaked}"
    return Settings(_env_file=None)


def _example_env_keys() -> set[str]:
    """Parse example.env into the set of variable names it actually defines.

    A real parse, not a substring search: a name that only appears inside a
    comment must not count as documented-and-set, or this would pass on a file
    that discusses a setting in prose and never gives it a value.
    """
    keys: set[str] = set()
    for raw in EXAMPLE_ENV.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def _flags_on_keys() -> set[str]:
    """Parse the FLAGS_ON=( ... ) array in the both-flag-states script.

    Again a real parse of the array body rather than a substring search over the
    whole file: that script's comments name several flags, including one that is
    deliberately excluded from the array, so a substring match would report an
    exclusion as covered.
    """
    body = FLAGS_ON_SCRIPT.split("FLAGS_ON=(", 1)[1].split("\n)", 1)[0]
    keys: set[str] = set()
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def test_all_six_settings_are_present_in_example_env() -> None:
    settings_names = _p8_field_names()
    assert len(settings_names) == EXPECTED_SETTING_COUNT, (
        f"expected {EXPECTED_SETTING_COUNT} P8 settings, found {settings_names}"
    )

    documented = _example_env_keys()
    # Tunables included, not just the booleans: an undocumented tunable is
    # exactly as invisible to an operator as an undocumented flag, and both P8
    # tunables need their value understood rather than copied -- 0.0 is the
    # value that asks no NPS question at all, and 10 is a floor that already
    # suppresses rankings the moment per-agent CSAT is switched on.
    missing = [name for name in settings_names if name.upper() not in documented]
    assert not missing, (
        f"P8 settings in config.py but missing from {EXAMPLE_ENV_PATH.name}: {missing}"
    )


def test_nps_sample_rate_defaults_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """0.0 is not merely "off": `should_survey_nps` short-circuits on any rate
    <= 0.0 *without hashing*, so the default is a structural guarantee that
    every end-of-conversation survey is the pre-P8 CSAT question -- not "no NPS
    most of the time", which is what a threshold comparison would give. The
    flags-ON gate exports 1.0, so this assertion is only worth anything with the
    variable cleared first."""
    settings = _settings(monkeypatch)
    assert settings.nps_sample_rate == 0.0
    assert Settings.model_fields["nps_sample_rate"].annotation is float
    assert "NPS_SAMPLE_RATE=0.0" in EXAMPLE_ENV


def test_csat_ranking_min_samples_defaults_to_ten(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one P8 setting whose default is deliberately NOT inert: 10 is
    non-zero on purpose, so a bare `Settings()` cannot silently produce n=1
    "rankings" the first time per-agent CSAT is switched on. The flags-ON gate
    exports 25 -- a value chosen precisely because it is not this default, so a
    consumer that reads the floor from settings and then asserts a hardcoded 10
    fails there rather than passing forever."""
    settings = _settings(monkeypatch)
    assert settings.csat_ranking_min_samples == 10
    assert Settings.model_fields["csat_ranking_min_samples"].annotation is int
    assert "CSAT_RANKING_MIN_SAMPLES=10" in EXAMPLE_ENV


def test_every_boolean_setting_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    flags = _p8_flag_names()
    assert len(flags) == EXPECTED_FLAG_COUNT, (
        f"expected {EXPECTED_FLAG_COUNT} boolean P8 flags, found {flags}"
    )
    on = [name for name in flags if getattr(settings, name) is not False]
    assert not on, f"P8 flags not defaulting to False: {on}"
    # And the operator-facing template agrees, rather than shipping a file that
    # switches on what config.py defaults off.
    enabled_in_template = [f"{name.upper()}=true" for name in flags]
    assert not [line for line in enabled_in_template if line in EXAMPLE_ENV]


def test_both_services_start_with_none_of_them_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both halves of the boot check, and the second half is a real check.

    The `agent` service is a separate package with its own virtualenv and cannot
    be imported from here, so its half is asserted as the property P8 actually
    claims: **P8 adds no `agent`-side setting at all**. NPS and CSAT live
    entirely in the backend, and the `agent`-side token capture (task 1 + the D4
    fix) is unconditional -- nullable columns, no gating flag. If a future task
    gives the `agent` service a P8 setting, this assertion fails and whoever
    adds it has to document it in `example.env` and the gate script too, rather
    than the omission being invisible from this suite.
    """
    for name in _p8_field_names():
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    leaked = [name.upper() for name in _p8_field_names() if name.upper() in os.environ]
    assert not leaked, f"P8 variables still in the environment: {leaked}"

    named_in_agent = [name for name in _p8_field_names() if name in AGENT_CONFIG]
    assert not named_in_agent, (
        f"P8 settings named in {AGENT_CONFIG_PATH}: {named_in_agent}. P8 is "
        "backend-only; if that has changed, they need documenting in "
        "example.env and adding to the flags-ON gate as well."
    )

    from chatbot.main import _build_genai_client, bootstrap_application  # noqa: PLC0415
    from chatbot.platform.config import get_settings  # noqa: PLC0415
    from chatbot.platform.metered_genai import MeteredGenaiClient  # noqa: PLC0415

    get_settings.cache_clear()
    try:
        app = bootstrap_application()
        paths = app.openapi()["paths"]
        # The cost endpoint is P8's only new HTTP surface. It is *mounted*
        # regardless (mounting it conditionally would make "not enabled" and
        # "wrong URL" indistinguishable to an operator) and answers 404 while
        # the flag is off -- see insights_router.ai_cost.
        assert "/metrics/ai-cost" in paths
        settings = get_settings()
        assert settings.ai_cost_reporting_enabled is False
        # And the model path is byte-identical to pre-P8: with metering off,
        # `_build_genai_client` returns the raw SDK client, so no proxy and no
        # `TokenUsageSink` exist to add latency or I/O.
        assert not isinstance(_build_genai_client(settings), MeteredGenaiClient)
    finally:
        get_settings.cache_clear()


def test_the_all_flags_on_gate_covers_every_p8_setting() -> None:
    """P5's two flags were missing from this script for an entire package, so
    their on-paths had never once run. Checked rather than assumed -- and every
    one of the six is required, tunables included, because a tunable left at its
    default in the ON run exercises only the same path the OFF run already
    did."""
    covered = _flags_on_keys()
    expected = {name.upper() for name in _p8_field_names()}
    missing = sorted(expected - covered)
    assert not missing, (
        f"P8 settings absent from FLAGS_ON in {FLAGS_ON_SCRIPT_PATH.name} "
        f"(their on-path is untested): {missing}"
    )


def test_the_gate_exercises_the_two_tunables_at_non_default_values() -> None:
    """A tunable listed in FLAGS_ON at its own default value is a line that
    looks like coverage and is not: the ON run would take the identical path to
    the OFF run. 1.0 forces the NPS question on every sampled survey (the
    other short-circuit, so no hash-boundary luck is involved), and 25 is not
    the default floor of 10."""
    body = FLAGS_ON_SCRIPT.split("FLAGS_ON=(", 1)[1].split("\n)", 1)[0]
    values: dict[str, str] = {}
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    assert float(values["NPS_SAMPLE_RATE"]) >= 1.0
    assert int(values["CSAT_RANKING_MIN_SAMPLES"]) != 10
