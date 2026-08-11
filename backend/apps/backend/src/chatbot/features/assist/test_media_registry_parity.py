"""The two copies of the attachment registry must not drift.

`agent/` and `backend/` are separate services with no shared package — CLAUDE.md
is explicit that they communicate only over HTTP — so `media_registry.py` exists
twice on purpose. What is NOT acceptable is the two copies disagreeing: a voice
note would then be understood differently depending on whether the bot answered
automatically or an agent clicked "Suggest a reply", and nothing would flag it.

This test is the thing that makes the duplication safe. It loads the agent
service's module straight off disk (both copies are stdlib-only specifically so
this import is cheap and side-effect free) and compares the full behavioural
snapshot.

If you are here because this test failed: you edited one copy. Mirror the change
into the other. Do not "fix" it by loosening the comparison.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from chatbot.features.assist import media_registry as backend_registry

_AGENT_RELATIVE_PATH = Path("agent") / "app" / "services" / "media_registry.py"


def _find_agent_registry() -> Path | None:
    """Walk up looking for the sibling `agent/` checkout.

    Returns None when the backend is checked out on its own (it has its own
    upstream repo, `proton-conversational-ai`), in which case there is no second
    copy to disagree with and the test has nothing to assert.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _AGENT_RELATIVE_PATH
        if candidate.is_file():
            return candidate
    return None


def _load(path: Path) -> ModuleType:
    name = "agent_media_registry"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec_module, not after: @dataclass resolves its field
    # annotations via sys.modules[cls.__module__], which is None for a module
    # that is still only half-imported. Without this the import dies inside
    # dataclasses with a bare AttributeError.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def test_agent_and_backend_registries_are_identical() -> None:
    agent_path = _find_agent_registry()
    if agent_path is None:
        pytest.skip(
            "standalone backend checkout: no sibling agent/ copy of "
            "media_registry.py to compare against"
        )
    agent_registry = _load(agent_path)
    assert agent_registry.registry_snapshot() == backend_registry.registry_snapshot()


def test_agent_registry_is_importable_without_third_party_deps() -> None:
    """The parity check above only stays cheap while both copies are stdlib-only.

    If someone adds `import structlog` to either, this loads a whole dependency
    tree inside the other service's test run — and in a standalone agent
    checkout it would fail outright.
    """
    agent_path = _find_agent_registry()
    if agent_path is None:
        pytest.skip("standalone backend checkout")
    source = agent_path.read_text()
    for forbidden in ("import httpx", "import structlog", "from google", "import pydantic"):
        assert forbidden not in source, f"{forbidden!r} makes the registry non-portable"
