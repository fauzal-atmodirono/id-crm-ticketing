"""P7 task 11 -- the nine settings, their defaults, and the two files that
have to agree with them.

The defaults are the deliverable. With none of the nine set, a tenant that
redeploys P7 gets byte-identical behaviour to before it: sentiment stays
unclassified, FAQ ranking stays pure semantic search, no media-diagnosis
instruction is appended, nothing is auto-summarised or indexed on resolve, and
the translate endpoint -- mounted, unlike the rest -- answers `{"disabled":
true}` without calling a model.

Two traps this file is written specifically to avoid, both of which have already
caught this repo:

1. **`Settings(_env_file=None)` does not stop pydantic-settings reading
   `os.environ`.** `deploy/scripts/check-suites-both-flag-states.sh` runs this
   suite a second time with eight of the nine exported as `true`, so a
   defaults test that did not clear the environment first would assert the exact
   opposite of its own name on that run -- and pass. Three such tests were found
   and fixed during P6. `_settings()` clears every P7 variable and then
   *asserts* they are gone, so deleting the delenv loop breaks the test rather
   than quietly hollowing it out.
2. **A hardcoded list of names.** It would pass forever while missing the one
   thing that actually goes wrong: a setting added to `config.py` and forgotten
   in the operator-facing `deploy/tenants/example.env`, which is invisible until
   an operator goes looking for a switch no document mentions. The P7 fields are
   read out of `Settings.model_fields` instead (pydantic preserves declaration
   order), sliced between the last pre-P7 field and P7's own last field. Add a
   setting inside that block and this file starts requiring it in `example.env`
   and in the flags-ON gate on its own, and the count assertions fail loudly
   rather than silently covering nothing.

The flags-ON gate is checked here too, because P5's two flags were missing from
that script for an entire package -- their on-paths had never once executed. Its
one deliberate omission is `TRANSLATION_OUTBOUND_TAMIL_ENABLED`, which the plan
excludes even from the all-on run.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chatbot.platform.config import Settings

_REPO_ROOT = Path(__file__).resolve().parents[5]
EXAMPLE_ENV_PATH = _REPO_ROOT / "deploy" / "tenants" / "example.env"
FLAGS_ON_SCRIPT_PATH = _REPO_ROOT / "deploy" / "scripts" / "check-suites-both-flag-states.sh"

# Fail here rather than 30 lines later with an empty-file mystery if this file
# is ever moved to a different depth.
assert EXAMPLE_ENV_PATH.is_file(), f"example.env not found at {EXAMPLE_ENV_PATH}"
assert FLAGS_ON_SCRIPT_PATH.is_file(), f"flag-state script not found at {FLAGS_ON_SCRIPT_PATH}"

EXAMPLE_ENV = EXAMPLE_ENV_PATH.read_text(encoding="utf-8")
FLAGS_ON_SCRIPT = FLAGS_ON_SCRIPT_PATH.read_text(encoding="utf-8")

# The anchors bounding config.py's `# --- P7: AI conversational quality ---`
# block. The opening one is a real field immediately before it. The closing one
# is P7's OWN last field, inclusive -- deliberately not "the first field after
# the block", which is what P6's equivalent test uses: the P7 block sat at the
# end of the Settings class when it landed and a later package has since
# appended its own settings there, so anchoring on the neighbour would have
# silently made this suite assert P8's defaults and demand P8's names in P7's
# documentation.
_LAST_FIELD_BEFORE_P7 = "escalation_reply_to_template"
_LAST_P7_FIELD = "auto_summary_on_resolve_enabled"

EXPECTED_SETTING_COUNT = 9
EXPECTED_FLAG_COUNT = 8  # the nine minus faq_keyword_weight, which is a float

# The one flag deliberately absent from the all-on run: outbound Tamil sends
# unverified machine translation to a customer, and ships disabled pending a
# signed-off evaluation of 30 real Tamil enquiries scored by a Tamil speaker.
OUTBOUND_TAMIL = "TRANSLATION_OUTBOUND_TAMIL_ENABLED"


def _p7_field_names() -> list[str]:
    """Every Settings field declared inside config.py's P7 block, in order."""
    names = list(Settings.model_fields)
    start = names.index(_LAST_FIELD_BEFORE_P7) + 1
    end = names.index(_LAST_P7_FIELD) + 1
    assert start < end, "the P7 block anchors are in the wrong order"
    return names[start:end]


def _p7_flag_names() -> list[str]:
    """The boolean subset of the P7 block -- i.e. the eight feature flags."""
    fields = Settings.model_fields
    return [name for name in _p7_field_names() if fields[name].annotation is bool]


def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings with every P7 variable removed from the process environment.

    See this module's docstring, trap 1: without the delenv loop below, every
    defaults test in this file asserts nothing on the flags-ON run.
    """
    for name in _p7_field_names():
        monkeypatch.delenv(name.upper(), raising=False)
    leaked = [name.upper() for name in _p7_field_names() if name.upper() in os.environ]
    assert not leaked, f"P7 variables still in the environment: {leaked}"
    return Settings(_env_file=None)


def _example_env_keys() -> set[str]:
    """Parse example.env into the set of variable names it actually defines.

    A real parse, not a substring search: a name that only appears inside a
    comment must not count as documented-and-set, or this would pass on a file
    that discusses a flag in prose and never gives it a value.
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

    Again a real parse of the array body, not a substring search over the whole
    file: that script's comments name several flags, including the one that is
    deliberately excluded, so a substring match would report the exclusion as
    covered.
    """
    body = FLAGS_ON_SCRIPT.split("FLAGS_ON=(", 1)[1].split("\n)", 1)[0]
    keys: set[str] = set()
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def test_all_nine_settings_are_present_in_example_env():
    settings_names = _p7_field_names()
    assert len(settings_names) == EXPECTED_SETTING_COUNT, (
        f"expected {EXPECTED_SETTING_COUNT} P7 settings, found {settings_names}"
    )

    documented = _example_env_keys()
    # Tunables included, not just the booleans: an undocumented tunable is
    # exactly as invisible to an operator as an undocumented flag, and
    # FAQ_KEYWORD_WEIGHT is the one whose value has to be understood to be left
    # alone (0.0 is what reproduces today's ranking score for score).
    missing = [name for name in settings_names if name.upper() not in documented]
    assert not missing, (
        f"P7 settings in config.py but missing from {EXAMPLE_ENV_PATH.name}: {missing}"
    )


def test_faq_keyword_weight_defaults_to_zero(monkeypatch):
    """0.0 is not merely "off". It is the value that makes hybrid ranking
    reproduce the pre-P7 ordering AND the pre-P7 scores exactly, which is the
    whole safety argument for shipping it onto a live tenant. The flags-ON gate
    exports 0.5, so this assertion is only worth anything with the variable
    cleared first."""
    settings = _settings(monkeypatch)
    assert settings.faq_keyword_weight == 0.0
    assert Settings.model_fields["faq_keyword_weight"].annotation is float
    assert "FAQ_KEYWORD_WEIGHT=0.0" in EXAMPLE_ENV


def test_every_boolean_setting_defaults_to_false(monkeypatch):
    settings = _settings(monkeypatch)
    flags = _p7_flag_names()
    assert len(flags) == EXPECTED_FLAG_COUNT, (
        f"expected {EXPECTED_FLAG_COUNT} boolean P7 flags, found {flags}"
    )
    on = [name for name in flags if getattr(settings, name) is not False]
    assert not on, f"P7 flags not defaulting to False: {on}"
    # And the operator-facing file agrees, rather than shipping a template that
    # switches on what config.py defaults off.
    enabled_in_template = [f"{name.upper()}=true" for name in flags]
    assert not [line for line in enabled_in_template if line in EXAMPLE_ENV]


def test_the_service_starts_with_none_of_them_set(monkeypatch):
    """The backend half of the boot check.

    The `agent` service is a separate package with its own virtualenv and cannot
    be imported from here, so "the service starts" is one test per suite rather
    than one test that overstates its reach. P7 adds no `agent`-side setting --
    all nine live in the backend -- so unlike P6 there is no companion test
    there to keep in step.
    """
    for name in _p7_field_names():
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
        # Mounted regardless of any P7 flag (see main.py on why this one is not
        # gated on its flag), and inert until TRANSLATION_ENABLED is set.
        assert "/assist/translate" in paths
        assert "/assist/summarize" in paths
        # Nothing P7 builds any infrastructure at defaults: no engine means no
        # table creation at startup and no connection attempt.
        assert getattr(app.state, "resolved_case_engine", None) is None
    finally:
        get_settings.cache_clear()


def test_the_all_flags_on_gate_covers_every_p7_flag_except_outbound_tamil():
    """P5's two flags were missing from this script for an entire package, so
    their on-paths had never once run. Checked rather than assumed."""
    covered = _flags_on_keys()
    expected = {name.upper() for name in _p7_field_names()} - {OUTBOUND_TAMIL}
    missing = sorted(expected - covered)
    assert not missing, (
        f"P7 settings absent from FLAGS_ON in {FLAGS_ON_SCRIPT_PATH.name} "
        f"(their on-path is untested): {missing}"
    )
    assert OUTBOUND_TAMIL not in covered, (
        f"{OUTBOUND_TAMIL} must stay out of FLAGS_ON -- the plan requires the "
        "all-on run to exclude outbound Tamil until a Tamil speaker has scored "
        "30 real enquiries"
    )
