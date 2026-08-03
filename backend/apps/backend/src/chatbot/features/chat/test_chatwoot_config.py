from __future__ import annotations

from chatbot.platform.config import Settings


def test_chatwoot_settings_defaults() -> None:
    s = Settings()
    assert s.chatwoot_inbox_id == 0
    assert s.chatwoot_agent_team_id == 0
    assert s.chatwoot_escalation_label == "ai-escalation"
    assert s.chatwoot_webhook_secret == ""
