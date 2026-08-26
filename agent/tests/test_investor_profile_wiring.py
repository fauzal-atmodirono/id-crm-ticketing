"""Feature B wiring: the contact write merges, and the orchestrator dispatches."""

import json as _json

import httpx
import respx

from app.clients.chatwoot import ChatwootClient


def _client() -> ChatwootClient:
    return ChatwootClient(
        base_url="http://cw.test", api_access_token="t", account_id=1
    )


@respx.mock
async def test_merge_preserves_attributes_it_was_not_given():
    respx.get("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(
            200,
            json={
                "payload": {
                    "id": 7,
                    "custom_attributes": {
                        "risk_profile": "Konservatif",
                        "holdings": "BBCA, BBRI",
                    },
                }
            },
        )
    )
    route = respx.put("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(200, json={})
    )

    client = _client()
    wrote = await client.merge_contact_attributes(7, {"investor_horizon": "> 10 tahun"})
    await client.aclose()

    assert wrote is True
    body = _json.loads(route.calls[0].request.content)["custom_attributes"]
    # The portfolio the contact already carried must survive the write.
    assert body["risk_profile"] == "Konservatif"
    assert body["holdings"] == "BBCA, BBRI"
    assert body["investor_horizon"] == "> 10 tahun"


@respx.mock
async def test_merge_writes_nothing_when_given_nothing():
    get = respx.get("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(200, json={"payload": {"id": 7}})
    )
    put = respx.put("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(200, json={})
    )

    client = _client()
    wrote = await client.merge_contact_attributes(7, {})
    await client.aclose()

    assert wrote is False
    assert not put.called
    assert not get.called


@respx.mock
async def test_merge_survives_a_contact_with_no_attributes_yet():
    respx.get("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(200, json={"payload": {"id": 7}})
    )
    route = respx.put("http://cw.test/api/v1/accounts/1/contacts/7").mock(
        return_value=httpx.Response(200, json={})
    )

    client = _client()
    wrote = await client.merge_contact_attributes(7, {"investor_experience": "Pemula"})
    await client.aclose()

    assert wrote is True
    body = _json.loads(route.calls[0].request.content)["custom_attributes"]
    assert body == {"investor_experience": "Pemula"}


class _FakeChatwoot:
    def __init__(self):
        self.merged = None
        self.labels = []
        self.messages = []

    async def merge_contact_attributes(self, contact_id, attributes):
        self.merged = (contact_id, attributes)
        return bool(attributes)

    async def add_labels(self, conversation_id, labels):
        self.labels.append((conversation_id, labels))

    async def create_message(self, conversation_id, text, **kwargs):
        self.messages.append(text)

    async def toggle_status(self, *args, **kwargs):
        pass


async def test_recording_a_preference_writes_the_contact(monkeypatch):
    from app.ai.gemini import Decision
    from app.services import orchestrator

    monkeypatch.setattr(orchestrator, "_utc_now_iso", lambda: "2026-08-26T10:00:00Z")
    cw = _FakeChatwoot()
    decision = Decision(
        "record_investor_preference",
        {"horizon": "very_long", "experience": "beginner"},
        None,
        None,
    )

    await orchestrator._execute_decision(99, decision, "auto", cw, contact_id=7)

    contact_id, attributes = cw.merged
    assert contact_id == 7
    assert attributes["investor_horizon"] == "> 10 tahun"
    assert attributes["investor_experience"] == "Pemula"
    assert attributes["preference_captured_at"] == "2026-08-26T10:00:00Z"
    assert "risk_profile" not in attributes
    # Recording is silent: the customer already told us this in conversation,
    # and a "noted!" message would be the bot talking about its own bookkeeping.
    assert cw.messages == []


async def test_a_divergent_answer_flags_a_human_and_changes_nothing():
    from app.ai.gemini import Decision
    from app.services import orchestrator

    cw = _FakeChatwoot()
    # Customer says they would buy more at -20%: implies Agresif (tier 3).
    decision = Decision(
        "record_investor_preference", {"drawdown_reaction": "buy_more"}, None, None
    )

    await orchestrator._execute_decision(
        99, decision, "auto", cw, contact_id=7, recorded_risk_profile="Konservatif"
    )

    assert cw.labels == [(99, ["profile_review"])]
    assert "risk_profile" not in cw.merged[1]


async def test_an_answer_within_the_recorded_profile_flags_nobody():
    from app.ai.gemini import Decision
    from app.services import orchestrator

    cw = _FakeChatwoot()
    decision = Decision(
        "record_investor_preference", {"drawdown_reaction": "sell_all"}, None, None
    )

    await orchestrator._execute_decision(
        99, decision, "auto", cw, contact_id=7, recorded_risk_profile="Agresif"
    )

    assert cw.labels == []


async def test_no_contact_id_is_a_skip_not_a_handoff():
    from app.ai.gemini import Decision
    from app.services import orchestrator

    cw = _FakeChatwoot()
    decision = Decision(
        "record_investor_preference", {"experience": "beginner"}, None, None
    )

    await orchestrator._execute_decision(99, decision, "auto", cw, contact_id=None)

    assert cw.merged is None
    assert cw.messages == []


def test_the_action_space_follows_the_flag():
    from app.ai import tools
    from app.services import orchestrator

    # Off is not "TOOLS passed explicitly" -- it is the same call today makes,
    # with no tools argument at all.
    assert orchestrator._decide_kwargs(False) == {}
    assert orchestrator._decide_kwargs(True) == {
        "tools": tools.TOOLS_WITH_PROFILING
    }
