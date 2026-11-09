"""Round 3: the caller-context keywords exist on every ConversationLogPort
implementation, so a tenant on Zendesk (or none) never TypeErrors when the
phone bridge or a dial-status lookup passes them. They are ignored there."""

from __future__ import annotations

import inspect

from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.chat.adapters.noop_conversation_log import NoOpConversationLog
from chatbot.features.chat.adapters.zendesk import ZendeskAdapter
from chatbot.features.chat.ports import ConversationLogPort
from chatbot.platform.config import Settings


def _kwonly(cls: type, method: str) -> set[str]:
    sig = inspect.signature(getattr(cls, method))
    return {n for n, p in sig.parameters.items() if p.kind is inspect.Parameter.KEYWORD_ONLY}


def test_every_port_accepts_the_caller_keywords() -> None:
    for cls in (ConversationLogPort, NoOpConversationLog, ZendeskAdapter, ChatwootAdapter):
        assert "attach_by_phone" in _kwonly(cls, "ensure_conversation_ticket"), cls
        assert "phone" in _kwonly(cls, "find_conversation_ticket"), cls


async def test_noop_and_zendesk_ignore_them() -> None:
    noop = NoOpConversationLog()
    assert (
        await noop.ensure_conversation_ticket("phone-CA1", "s", None, None, attach_by_phone=True)
        == ""
    )
    assert await noop.find_conversation_ticket("phone-CA1", phone="+60000000101") is None
    zen = ZendeskAdapter(Settings())
    zen._conv_tickets["phone-CA1"] = "Z-1"
    assert await zen.find_conversation_ticket("phone-CA1", phone="+60000000101") == "Z-1"
    assert (
        await zen.ensure_conversation_ticket("phone-CA1", "s", None, None, attach_by_phone=True)
        == "Z-1"
    )
