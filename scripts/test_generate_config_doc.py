"""Tests for the generated configuration document (P14 task 2).

The point of these tests is not the document. It is the standing check in
`test_a_setting_present_in_code_but_missing_from_example_env_is_flagged`: this
repo's own convention (CLAUDE.md) is that a new setting must be added to both
`config.py` and `deploy/tenants/example.env`, and there is no other automated
enforcement of it anywhere. That check currently reports 97 of 256 settings set
in neither `example.env` nor compose, including every Twilio credential and all
sixteen `PHONE_*` settings — a finding, not a formality.

**Nothing here instantiates `Settings()`, deliberately.** pydantic-settings
reads `os.environ` even when handed `_env_file=None`, so a test that built a
`Settings()` to read a default would assert something different under the
all-flags-on gate than under a plain run — while passing in both. Six tests in
this repository have already been found doing exactly that. Every assertion
below reads `Settings.model_fields`, which is class-level metadata and cannot be
influenced by the environment.

Run with the backend's venv, which has pydantic-settings:

    cd backend/apps/backend
    GOOGLE_API_KEY=test-key uv run pytest ../../../scripts/test_generate_config_doc.py -q

These tests live outside `backend/apps/backend/src/` on purpose, so the backend
suite's own count is unaffected by them.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR = REPO_ROOT / "scripts/generate-config-doc.py"


def _load_generator():
    """Import the generator, whose filename is not a valid module name."""
    spec = importlib.util.spec_from_file_location("generate_config_doc", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_config_doc"] = module
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


@pytest.fixture(scope="module")
def document() -> str:
    return gen.build_document()


# --------------------------------------------------------------------------
# The five checks the plan asks for
# --------------------------------------------------------------------------

def test_every_setting_in_both_settings_classes_appears_in_the_generated_document(
    document: str,
) -> None:
    """No setting may be missing. This is what makes the document usable as a
    reference rather than a sample."""
    missing: list[str] = []
    counted = 0
    for _heading, module_name, _source, _scope in gen.SOURCES:
        settings_cls = gen.load_settings_class(module_name)
        for name in settings_cls.model_fields:
            counted += 1
            if f"`{name.upper()}`" not in document:
                missing.append(f"{module_name}.{name}")
    assert counted > 200, f"only {counted} settings introspected; the import broke"
    assert not missing, f"{len(missing)} settings absent from the document: {missing[:20]}"


def test_every_setting_in_example_env_appears(document: str) -> None:
    """Every variable in the tenant template is accounted for somewhere in the
    document — either as a settings row, or in the list of entries that are
    read by compose/Caddy/Chatwoot rather than by either service."""
    names = gen.read_example_env(gen.EXAMPLE_ENV)
    assert len(names) > 100, f"only {len(names)} vars parsed out of example.env"
    missing = [name for name in names if f"`{name}`" not in document]
    assert not missing, f"example.env vars absent from the document: {missing}"


def test_a_setting_present_in_code_but_missing_from_example_env_is_flagged(
    document: str,
) -> None:
    """The standing drift check, and the most valuable test in this file.

    Asserted two ways, because a test that only counted rows would keep passing
    if the classifier broke and reported everything as undiscoverable:

    1. The mechanism: `where_set` distinguishes all three states.
    2. The report: a setting known to be absent from both `example.env` and
       compose appears under the drift heading, and one known to be present in
       `example.env` does not.
    """
    example_set = set(gen.read_example_env(gen.EXAMPLE_ENV))
    compose_set = gen.read_compose_env(gen.COMPOSE_FILES)

    # 1. The mechanism, against a name that exists in neither set.
    assert gen.where_set("NOT_A_REAL_SETTING_AT_ALL", example_set, compose_set) == "**nowhere**"
    assert gen.where_set("AGENT_MODE", example_set, compose_set) == "`example.env`"
    assert gen.where_set("CHATWOOT_URL", example_set, compose_set) == "compose", (
        "CHATWOOT_URL is a required agent setting supplied by compose as an "
        "internal docker hostname; if this stops resolving to compose the drift "
        "table will report a required setting as undiscoverable and be ignored"
    )

    # 2. The report.
    drift = document.split("## Drift:")[1].split("\n## ")[0]
    assert "`TWILIO_ACCOUNT_SID`" in drift, (
        "TWILIO_ACCOUNT_SID is set in neither example.env nor compose and must "
        "be reported; the whole Twilio channel's credentials are undiscoverable"
    )
    assert "`AGENT_MODE`" not in drift, "AGENT_MODE is in example.env and must not be flagged"


def test_the_generator_is_deterministic() -> None:
    """Two runs must produce identical bytes.

    Without this the committed document could not be checked into git with a
    staleness test: the test would fail on the second run and be deleted within
    a week. It is also why the document carries no timestamp.
    """
    first = gen.build_document()
    second = gen.build_document()
    assert first == second
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:", first), (
        "the document contains what looks like a generation timestamp; that "
        "makes --check fail on every run"
    )


def test_each_entry_carries_a_default_and_a_blast_radius(document: str) -> None:
    """Every settings row has all seven columns filled.

    Parsed out of the rendered markdown rather than computed a second way, so a
    row that renders as empty cells fails here even if `classify()` would have
    returned something.
    """
    rows = [
        line
        for line in document.splitlines()
        if line.startswith("| `") and "|" in line[3:]
    ]
    assert len(rows) > 200, f"only {len(rows)} table rows found"
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if len(cells) != 7:
            continue  # the drift and deploy-only tables have their own shapes
        env, type_, default, blast, who, where, desc = cells
        assert default, f"{env}: empty default cell"
        assert blast and blast != "—", f"{env}: empty blast radius"
        assert who and who != "—", f"{env}: empty owner"
        assert where, f"{env}: empty location"
        assert desc, f"{env}: empty description cell"


# --------------------------------------------------------------------------
# The document on disk
# --------------------------------------------------------------------------

def test_the_committed_document_is_current(document: str) -> None:
    """The committed `configuration.md` matches what the generator produces.

    This is the test that makes the generator worth having. A generated document
    whose committed copy has drifted — or been hand-edited — is worse than no
    generator at all, because it carries a "Generated, do not edit" banner that
    invites the reader to trust it.
    """
    assert gen.OUTPUT_PATH.exists(), (
        f"{gen.OUTPUT_PATH} is missing; run python3 scripts/generate-config-doc.py"
    )
    committed = gen.OUTPUT_PATH.read_text(encoding="utf-8")
    assert committed == document, (
        "configuration.md is stale or was hand-edited. Regenerate with: "
        "python3 scripts/generate-config-doc.py"
    )


def test_credential_defaults_are_never_printed() -> None:
    """A secret-shaped setting must not publish a default value.

    Every default in `config.py` today is blank or an obvious placeholder, so
    this proves nothing about today's values. It is a guard on the next edit:
    a real default dropped into the code would otherwise be published into a
    client-facing document by a script nobody re-reads.
    """
    assert gen.is_secret("chatwoot_api_token")
    assert gen.is_secret("twilio_auth_token")
    assert gen.is_secret("rbac_database_url") is False

    class _Field:
        default = "hunter2-a-real-looking-secret"

        def is_required(self) -> bool:
            return False

    rendered = gen.render_default("some_api_token", _Field())
    assert "hunter2" not in rendered
    assert "credential" in rendered


def test_the_caveats_live_in_the_generator_not_the_document(document: str) -> None:
    """The overclaim guards survive regeneration.

    They are a constant in the generator precisely so that re-running it cannot
    quietly drop them, which is what would happen if they were a hand-written
    section of the markdown.
    """
    assert len(gen.FLAG_CAVEATS) >= 8
    for caveat in gen.FLAG_CAVEATS:
        assert caveat in document
    # The two that cost an operator a support conversation each, and the one
    # that is still false on a deployed image.
    assert "FAQ_SUGGESTION_POPUP_ENABLED" in document
    assert "CALL_RECORDING_RETRIEVAL_ENABLED" in document
