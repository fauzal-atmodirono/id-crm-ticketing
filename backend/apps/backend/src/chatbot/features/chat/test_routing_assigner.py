from unittest.mock import AsyncMock

from chatbot.features.routing.assigner import RoutingAssigner
from chatbot.platform.config import Settings


def _settings():
    return Settings(chatwoot_api_url="http://cw", chatwoot_account_id=1, chatwoot_api_token="t")


async def test_resolve_channel_conv_to_inbox_to_canonical():
    a = RoutingAssigner(_settings())
    a._request = AsyncMock(side_effect=[
        {"id": 5, "inbox_id": 3},                       # GET /conversations/5
        {"id": 3, "channel_type": "Channel::TwilioSms"},  # GET /inboxes/3
    ])
    assert await a.resolve_channel(5) == "whatsapp"


async def test_resolve_channel_failopen_web():
    a = RoutingAssigner(_settings())
    a._request = AsyncMock(return_value=None)  # conv fetch fails
    assert await a.resolve_channel(5) == "web"


async def test_assign_posts_assignment():
    a = RoutingAssigner(_settings())
    a._request = AsyncMock(return_value={})
    await a.assign(5, 9)
    a._request.assert_awaited_once_with(
        "POST", "/conversations/5/assignments", {"assignee_id": 9}
    )
