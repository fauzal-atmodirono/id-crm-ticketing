"""P11 task 10 -- the seven settings, their defaults, and example.env agreement.

Written in the shape `test_p6_flags.py` / `test_p8_flags.py` established, and for
the same reason: a setting added to `config.py` and forgotten in the
operator-facing `deploy/tenants/example.env` is invisible to every other test.
P11's seven were exactly that -- declared in `config.py`, absent from
`example.env`, so an operator had no documented switch for any of them.

Three traps this file is written to avoid:

1. **`Settings()` reads `os.environ`.** A bare-`Settings()` defaults assertion
   proves nothing when the variable is exported, and
   `check-suites-both-flag-states.sh` exists to export things. Nine such vacuous
   tests have been found in this run; one of them was P11's own
   `test_the_bypass_flag_defaults_to_on`. `_settings()` below deletes every P11
   variable and **asserts the delete worked**, so removing the loop breaks the
   test rather than hollowing it out.
2. **A hardcoded list of names.** The field names are read out of
   `Settings.model_fields` (pydantic preserves declaration order) and sliced
   between the last pre-P11 field and P11's own last field, so a setting added
   inside that block makes this file start requiring it in `example.env` on its
   own.
3. **Asserting the count and nothing else.** `test_every_other_new_setting_...`
   derives its list from the block rather than naming six flags, so it cannot
   silently stop covering one.

**Deliberately NOT asserted here:** membership of
`deploy/scripts/check-suites-both-flag-states.sh`'s `FLAGS_ON` array. None of the
seven is in it, so P11's on-paths have never once executed under the gate -- the
same hole P5 had for a whole package. That file is owned by a concurrent worker in
this run, so the gap is recorded in
`docs/analysis/2026-08-09-blocked-work-register.md` and in the P11 ledger instead
of being asserted by a test that would fail for a reason this module cannot fix
(`.superpowers/sdd/DISPATCH-RULES.md`).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chatbot.platform.config import Settings

_REPO_ROOT = Path(__file__).resolve().parents[5]
EXAMPLE_ENV_PATH = _REPO_ROOT / "deploy" / "tenants" / "example.env"

# Fail here rather than 30 lines later with an empty-file mystery if this file is
# ever moved to a different depth.
assert EXAMPLE_ENV_PATH.is_file(), f"example.env not found at {EXAMPLE_ENV_PATH}"

EXAMPLE_ENV = EXAMPLE_ENV_PATH.read_text(encoding="utf-8")

# The anchors bounding config.py's `# --- P11: voice partials ---` block. The
# opening one is P10's own last field, immediately before it; the closing one is
# P11's own last field, inclusive.
_LAST_FIELD_BEFORE_P11 = "data_scoped_rbac_enabled"
_LAST_P11_FIELD = "phone_retention_job_enabled"

EXPECTED_SETTING_COUNT = 7

# The one deliberate default-on flag in this programme. §8.1.6 requires roadside
# assistance 24/7.
_DEFAULT_ON = "phone_rsa_after_hours_bypass"


def _p11_field_names() -> list[str]:
    """Every Settings field declared inside config.py's P11 block, in order."""
    names = list(Settings.model_fields)
    start = names.index(_LAST_FIELD_BEFORE_P11) + 1
    end = names.index(_LAST_P11_FIELD) + 1
    assert start < end, "the P11 block anchors are in the wrong order"
    return names[start:end]


def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings with every P11 variable removed from the process environment."""
    for name in _p11_field_names():
        monkeypatch.delenv(name.upper(), raising=False)
    leaked = [name.upper() for name in _p11_field_names() if name.upper() in os.environ]
    assert not leaked, f"P11 variables still in the environment: {leaked}"
    return Settings(_env_file=None)


def _example_env_keys() -> set[str]:
    """Parse example.env into the set of variable names it actually assigns.

    A real parse, not a substring search: a name that only appears inside a
    comment must not count as documented-and-set. Several of P11's comments name
    neighbouring settings, so a substring match would pass on a file that
    discusses a setting in prose and never gives it a value.
    """
    keys: set[str] = set()
    for raw in EXAMPLE_ENV.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def test_the_p11_block_holds_exactly_seven_settings() -> None:
    fields = _p11_field_names()
    assert len(fields) == EXPECTED_SETTING_COUNT, fields


def test_the_seven_settings_are_present_in_example_env() -> None:
    assigned = _example_env_keys()
    missing = [name.upper() for name in _p11_field_names() if name.upper() not in assigned]
    assert not missing, f"P11 settings missing an assignment in example.env: {missing}"


def test_phone_rsa_after_hours_bypass_defaults_to_true(monkeypatch: pytest.MonkeyPatch) -> None:
    assert getattr(_settings(monkeypatch), _DEFAULT_ON) is True


def test_every_other_new_setting_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    for name in _p11_field_names():
        if name == _DEFAULT_ON:
            continue
        assert getattr(settings, name) is False, f"{name} does not default to False"


def test_example_env_documents_the_bypass_as_the_one_default_on_flag() -> None:
    """The default-on flag is the only one an operator must not "tidy" to false.

    Its env entry has to say so, or the next person normalising this file for
    consistency turns off the RSA 24/7 guarantee. Checks the assignment, not just
    the prose: `PHONE_RSA_AFTER_HOURS_BYPASS=false` in the template would ship
    every new tenant with the bypass off.
    """
    assigned: dict[str, str] = {}
    for raw in EXAMPLE_ENV.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        assigned[key.strip()] = value.strip()

    assert assigned["PHONE_RSA_AFTER_HOURS_BYPASS"] == "true"
