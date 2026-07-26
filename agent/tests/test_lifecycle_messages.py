# test_lifecycle_messages.py
from app.services import lifecycle
from app.services.lifecycle import _resolve_message  # helper added in Step 4


def test_resolve_prefers_override() -> None:
    assert _resolve_message({"thanks": "Terima kasih!"}, "thanks", "Thank you!") == "Terima kasih!"


def test_resolve_falls_back_on_empty_or_missing() -> None:
    assert _resolve_message({"thanks": ""}, "thanks", "Thank you!") == "Thank you!"
    assert _resolve_message({}, "thanks", "Thank you!") == "Thank you!"
    assert _resolve_message(None, "thanks", "Thank you!") == "Thank you!"


def test_assign_agent_default_exists() -> None:
    assert lifecycle.ASSIGN_AGENT_DEFAULT.strip() != ""
