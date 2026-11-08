"""FIX-15 (RFP 2026_028 4.6): softphone hold, resume and transfer.

Every Twilio REST call goes to `_FakeTwilio`; nothing here reaches Twilio.
The flow under test: today's handoff bridges caller and agent with a plain
`<Dial><Client>`. The first hold/transfer redirects the AGENT leg into a
conference room, which ends the caller's `<Dial>`; the dial-status callback
then returns hold TwiML (or the conference) for the caller instead of hanging
up. See hold_transfer.py's module docstring for why.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI

from chatbot.features.chat.phone.call_control import CallControl
from chatbot.features.chat.phone.hold_transfer import (
    CallSessionStore,
    HoldTransferController,
    build_hold_transfer_webhooks_router,
)
from chatbot.features.chat.phone.softphone_router import build_softphone_router
from chatbot.features.chat.phone.test_handoff import _client as _handoff_client
from chatbot.features.chat.phone.test_handoff import _DialLog
from chatbot.features.routing.presence import AgentRecord
from chatbot.platform.config import Settings

HOLD_URL = "https://media.example/hold.mp3"
BASE = "https://crm.example"
_AUTH = {
    "x-chatwoot-access-token": "tok",
    "x-chatwoot-client": "cli",
    "x-chatwoot-uid": "agent@proton.local",
}


@pytest.fixture(autouse=True)
def _fake_gemini_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-a-real-credential")


def _sign(token: str, url: str, params: dict[str, str]) -> str:
    s = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), s.encode(), hashlib.sha1).digest()).decode()


# --- fakes ------------------------------------------------------------------


class _Leg:
    def __init__(self, twilio: _FakeTwilio, sid: str) -> None:
        self._t = twilio
        self._sid = sid

    def fetch(self) -> Any:
        if self._sid not in self._t.legs:
            raise RuntimeError("20404 not found")
        return SimpleNamespace(**self._t.legs[self._sid])

    def update(self, **kwargs: Any) -> Any:
        if self._t.fail_updates:
            raise RuntimeError("twilio down")
        self._t.updates.append((self._sid, kwargs))
        if "status" in kwargs and self._sid in self._t.legs:
            self._t.legs[self._sid]["status"] = kwargs["status"]
        return object()


class _FakeCalls:
    def __init__(self, twilio: _FakeTwilio) -> None:
        self._t = twilio

    def __call__(self, sid: str) -> _Leg:
        return _Leg(self._t, sid)

    def create(self, **kwargs: Any) -> Any:
        self._t.created.append(kwargs)
        return SimpleNamespace(sid="CA_TARGET")


class _FakeTwilio:
    def __init__(self) -> None:
        self.legs: dict[str, dict[str, Any]] = {
            "CA_AGENT": {
                "to": "client:agent_17",
                "from_": "+60123456700",
                "parent_call_sid": "CA_CALLER",
                "status": "in-progress",
            },
            "CA_CALLER": {
                "to": "+60300000099",
                "from_": "+60123456700",
                "parent_call_sid": None,
                "status": "in-progress",
            },
        }
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.created: list[dict[str, Any]] = []
        self.fail_updates = False
        self.calls = _FakeCalls(self)

    def twiml_for(self, sid: str) -> list[str]:
        return [kw["twiml"] for s, kw in self.updates if s == sid and "twiml" in kw]

    def statuses_for(self, sid: str) -> list[str]:
        return [kw["status"] for s, kw in self.updates if s == sid and "status" in kw]


class _DictColl:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def get(self, doc_id: str) -> dict[str, Any] | None:
        return self.docs.get(doc_id)

    def set(self, doc_id: str, data: dict[str, Any]) -> None:
        self.docs[doc_id] = dict(data)

    def delete(self, doc_id: str) -> None:
        self.docs.pop(doc_id, None)


def _settings(**over: Any) -> Settings:
    base = {
        "rbac_enabled": True,
        "phone_transcript_live_enabled": True,
        "phone_handoff_enabled": True,
        "phone_agent_softphone_enabled": True,
        "phone_hold_transfer_enabled": True,
        "phone_hold_media_url": HOLD_URL,
        "phone_handoff_caller_id": "+60300000099",
        "twilio_account_sid": "AC1",
        "twilio_auth_token": "test_token",
        "twilio_webhook_base_url": BASE,
        "chatwoot_account_id": 1,
    }
    base.update(over)
    return Settings(_env_file=None, **base)


class _World:
    """One app with the agent endpoints, the Twilio webhooks, and the real
    ChatRouter dial-status handler all sharing one controller."""

    def __init__(self, settings: Settings | None = None, online: tuple[int, ...] = (23,)) -> None:
        self.settings = settings or _settings()
        self.twilio = _FakeTwilio()
        self.store = CallSessionStore(self.settings)
        self.store._sessions = _DictColl()
        self.store._legs = _DictColl()
        self.registry = AsyncMock()
        self.registry.registered_ids.return_value = {17, 23, 31}
        self.presence = AsyncMock()
        self.presence.fetch_agents.return_value = [
            AgentRecord(id=17, name="Aina", availability_status="online"),
            AgentRecord(
                id=23, name="Badrul", availability_status="online" if 23 in online else "busy"
            ),
            AgentRecord(id=31, name="Chong", availability_status="offline"),
        ]
        self.left: list[int] = []

        async def _on_leave(agent_id: int) -> None:
            self.left.append(agent_id)

        self.log = _DialLog()
        self.log.found = "42"
        self.controller = HoldTransferController(
            self.settings,
            call_control=CallControl(self.settings, client=self.twilio),
            store=self.store,
            registry=self.registry,
            presence=self.presence,
            find_ticket=self.log.find_conversation_ticket,
            on_agent_leave=_on_leave,
        )
        self.abandon = _AbandonSpy()
        self.acw = AsyncMock()
        self.http, _ = _handoff_client(
            self.log,
            self.settings,
            hold_transfer=self.controller,
            abandon_store=self.abandon,
            acw_controller=self.acw,
        )
        self.user_id = 17
        self.perms = {"voice.answer"}
        validator = AsyncMock()
        validator.resolve_identity.side_effect = lambda *_a, **_k: (self.user_id, False)
        repo = AsyncMock()
        repo.permissions_for_user.side_effect = lambda *_a, **_k: set(self.perms)
        app: FastAPI = self.http.app
        app.include_router(
            build_softphone_router(
                self.settings,
                self.registry,
                repo=repo,
                validator=validator,
                hold_transfer=self.controller,
            )
        )
        app.include_router(build_hold_transfer_webhooks_router(self.settings, self.controller))

    def agent(self, path: str, body: dict[str, Any], user_id: int = 17, auth: bool = True):
        self.user_id = user_id
        return self.http.post(f"/voice/agent/call/{path}", json=body, headers=_AUTH if auth else {})

    def twilio_post(self, path_and_query: str, params: dict[str, str]):
        sig = _sign(self.settings.twilio_auth_token, f"{BASE}{path_and_query}", params)
        return self.http.post(path_and_query, data=params, headers={"X-Twilio-Signature": sig})

    def dial_status(self, status: str = "completed", sid: str = "CA_CALLER"):
        params = {"CallSid": sid, "DialCallStatus": status, "DialCallSid": "CA_AGENT"}
        return self.twilio_post("/webhooks/phone/dial-status", params)


class _AbandonSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def record(self, call_sid: str, dial_status: str, at: Any = None) -> None:
        self.calls.append((call_sid, dial_status))


# --- hold -------------------------------------------------------------------


def test_hold_moves_caller_to_hold_media() -> None:
    w = _World()
    res = w.agent("hold", {"call_sid": "CA_AGENT"})
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "held"

    # The agent leg (not the caller's) is redirected into the conference room:
    # redirecting the caller's leg would hang the agent up.
    (agent_twiml,) = w.twilio.twiml_for("CA_AGENT")
    assert "<Conference" in agent_twiml and "proton-CA_CALLER" in agent_twiml
    assert 'endConferenceOnExit="false"' in agent_twiml
    assert "/webhooks/phone/conference-status?caller_sid=CA_CALLER" in agent_twiml
    assert w.twilio.twiml_for("CA_CALLER") == []

    # That ends the caller's <Dial>; Twilio asks dial-status what next, and
    # the caller gets the hold media instead of the usual hang-up.
    res = w.dial_status("completed")
    assert res.status_code == 200
    assert f'<Play loop="0">{HOLD_URL}</Play>' in res.text
    assert "<Hangup" not in res.text

    # Task 18: the dial outcome is still recorded exactly once, as answered.
    assert w.abandon.calls == [("CA_CALLER", "completed")]
    # ACW is deferred to the conference leave event, not entered now.
    w.acw.enter.assert_not_awaited()
    w.acw.start_after_call.assert_not_awaited()


def test_hold_media_url_is_configurable_and_has_a_placeholder_default() -> None:
    assert Settings(_env_file=None).phone_hold_media_url.startswith("https://")
    w = _World(_settings(phone_hold_media_url="https://proton.example/our-hold.wav"))
    w.agent("hold", {"call_sid": "CA_AGENT"})
    assert "https://proton.example/our-hold.wav" in w.dial_status().text


def test_dial_status_unchanged_when_call_was_never_held() -> None:
    w = _World()
    res = w.dial_status("completed")
    assert "<Hangup/>" in res.text and "<Play" not in res.text


def test_hold_twice_is_a_conflict() -> None:
    w = _World()
    w.agent("hold", {"call_sid": "CA_AGENT"})
    w.dial_status()
    assert w.agent("hold", {"call_sid": "CA_AGENT"}).status_code == 409


def test_hold_twilio_failure_leaves_call_untouched() -> None:
    w = _World()
    w.twilio.fail_updates = True
    res = w.agent("hold", {"call_sid": "CA_AGENT"})
    assert res.status_code == 502
    # No session left behind, so the eventual dial-status hangs up normally.
    assert "<Hangup/>" in w.dial_status().text


# --- resume -----------------------------------------------------------------


def test_resume_from_hold() -> None:
    w = _World()
    w.agent("hold", {"call_sid": "CA_AGENT"})
    w.dial_status()
    res = w.agent("resume", {"call_sid": "CA_AGENT"})
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "connected"
    (caller_twiml,) = w.twilio.twiml_for("CA_CALLER")
    assert "<Conference" in caller_twiml and "proton-CA_CALLER" in caller_twiml
    assert "<Play" not in caller_twiml
    # Resuming twice is a conflict, not a second redirect.
    assert w.agent("resume", {"call_sid": "CA_AGENT"}).status_code == 409
    assert len(w.twilio.twiml_for("CA_CALLER")) == 1


def test_hold_again_after_resume_redirects_caller_to_hold_media() -> None:
    w = _World()
    w.agent("hold", {"call_sid": "CA_AGENT"})
    w.dial_status()
    w.agent("resume", {"call_sid": "CA_AGENT"})
    assert w.agent("hold", {"call_sid": "CA_AGENT"}).status_code == 200
    assert w.twilio.twiml_for("CA_CALLER")[-1].count(HOLD_URL) == 1
    # Our own redirect out of the room fires a leave event: not a hang-up.
    w.twilio_post(
        "/webhooks/phone/conference-status?caller_sid=CA_CALLER",
        {"CallSid": "CA_CALLER", "StatusCallbackEvent": "participant-leave"},
    )
    assert w.twilio.statuses_for("CA_AGENT") == []


def test_resume_without_hold_is_a_conflict() -> None:
    w = _World()
    assert w.agent("resume", {"call_sid": "CA_AGENT"}).status_code == 409


# --- transfer ---------------------------------------------------------------


def test_transfer_to_agent_rings_target_and_keeps_case() -> None:
    w = _World()
    res = w.agent("transfer", {"call_sid": "CA_AGENT", "target_agent_id": 23})
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "transferring"

    # Caller goes on hold while the target rings.
    assert "<Conference" in w.twilio.twiml_for("CA_AGENT")[0]
    assert HOLD_URL in w.dial_status().text

    (create,) = w.twilio.created
    to = urlsplit(create["to"].replace("client:", "client://", 1))
    assert create["to"].startswith("client:agent_23?")
    q = parse_qs(to.query)
    # The case travels with the call: same conversation, transcript link, caller.
    assert q["conversation_id"] == ["42"]
    assert q["transcript_path"] == ["/app/accounts/1/conversations/42"]
    assert q["caller"] == ["+60123456700"]
    assert q["transferred_by"] == ["17"]
    assert create["url"] == f"{BASE}/webhooks/phone/transfer/answer?caller_sid=CA_CALLER"
    assert (
        create["status_callback"] == f"{BASE}/webhooks/phone/transfer/status?caller_sid=CA_CALLER"
    )
    assert create["from_"] == "+60300000099"
    assert create["timeout"] == w.settings.phone_agent_ring_timeout_seconds

    # Target answers: they join the room, the caller is brought back in, and
    # (cold, the default) the transferring agent's leg is hung up.
    res = w.twilio_post(
        "/webhooks/phone/transfer/answer?caller_sid=CA_CALLER", {"CallSid": "CA_TARGET"}
    )
    assert res.status_code == 200
    assert "<Conference" in res.text and "proton-CA_CALLER" in res.text
    assert "<Conference" in w.twilio.twiml_for("CA_CALLER")[-1]
    assert w.twilio.statuses_for("CA_AGENT") == ["completed"]
    assert w.left == [17]  # the transferring agent goes to ACW

    # The target now owns the call; the old owner does not.
    assert w.agent("hold", {"call_sid": "CA_TARGET"}, user_id=23).status_code == 200
    # Old owner's leg was hung up (cold): nothing left for them to act on.
    assert w.agent("resume", {"call_sid": "CA_AGENT"}, user_id=17).status_code in (403, 409)


def test_warm_transfer_keeps_the_first_agent_on_the_line() -> None:
    w = _World()
    w.agent("transfer", {"call_sid": "CA_AGENT", "target_agent_id": 23, "mode": "warm"})
    w.dial_status()
    w.twilio_post("/webhooks/phone/transfer/answer?caller_sid=CA_CALLER", {"CallSid": "CA_TARGET"})
    assert w.twilio.statuses_for("CA_AGENT") == []
    assert w.left == []
    # Still on the line, but the call is the target's now.
    assert w.agent("hold", {"call_sid": "CA_AGENT"}, user_id=17).status_code == 403
    assert w.agent("hold", {"call_sid": "CA_TARGET"}, user_id=23).status_code == 200


def test_transfer_targets_lists_online_registered_agents_except_me() -> None:
    w = _World()
    w.user_id = 17
    res = w.http.post("/voice/agent/call/transfer-targets", json={}, headers=_AUTH)
    assert res.status_code == 200
    assert res.json()["agents"] == [{"id": 23, "name": "Badrul"}]


def test_transfer_to_offline_agent_is_refused() -> None:
    w = _World(online=())
    res = w.agent("transfer", {"call_sid": "CA_AGENT", "target_agent_id": 23})
    assert res.status_code == 409
    assert w.twilio.created == [] and w.twilio.updates == []


def test_transfer_to_self_is_refused() -> None:
    w = _World()
    assert w.agent("transfer", {"call_sid": "CA_AGENT", "target_agent_id": 17}).status_code == 422


def test_unanswered_transfer_leaves_caller_on_hold_with_owner() -> None:
    w = _World()
    w.agent("transfer", {"call_sid": "CA_AGENT", "target_agent_id": 23})
    w.dial_status()
    w.twilio_post(
        "/webhooks/phone/transfer/status?caller_sid=CA_CALLER",
        {"CallSid": "CA_TARGET", "CallStatus": "no-answer"},
    )
    assert w.twilio.twiml_for("CA_CALLER") == []
    # The owner can still resume.
    assert w.agent("resume", {"call_sid": "CA_AGENT"}).status_code == 200


def test_unanswered_blind_transfer_apologises_to_caller() -> None:
    """Owner transferred then hung up; nobody answered: the caller must not be
    left on hold music forever."""
    w = _World()
    w.agent("transfer", {"call_sid": "CA_AGENT", "target_agent_id": 23})
    w.dial_status()
    w.twilio_post(
        "/webhooks/phone/conference-status?caller_sid=CA_CALLER",
        {"CallSid": "CA_AGENT", "StatusCallbackEvent": "participant-leave"},
    )
    assert w.twilio.twiml_for("CA_CALLER") == []  # still waiting for the target
    w.twilio_post(
        "/webhooks/phone/transfer/status?caller_sid=CA_CALLER",
        {"CallSid": "CA_TARGET", "CallStatus": "busy"},
    )
    (twiml,) = w.twilio.twiml_for("CA_CALLER")
    assert "<Say" in twiml and "<Hangup/>" in twiml


def test_resume_cancels_a_ringing_transfer() -> None:
    w = _World()
    w.agent("transfer", {"call_sid": "CA_AGENT", "target_agent_id": 23})
    w.dial_status()
    assert w.agent("resume", {"call_sid": "CA_AGENT"}).status_code == 200
    assert w.twilio.statuses_for("CA_TARGET") == ["canceled"]
    # A late answer from the cancelled target joins nothing.
    res = w.twilio_post(
        "/webhooks/phone/transfer/answer?caller_sid=CA_CALLER", {"CallSid": "CA_TARGET"}
    )
    assert "<Hangup/>" in res.text and "<Conference" not in res.text


# --- call end ---------------------------------------------------------------


def test_caller_hangup_in_conference_ends_agent_legs() -> None:
    w = _World()
    w.agent("hold", {"call_sid": "CA_AGENT"})
    w.dial_status()
    w.agent("resume", {"call_sid": "CA_AGENT"})
    w.twilio_post(
        "/webhooks/phone/conference-status?caller_sid=CA_CALLER",
        {"CallSid": "CA_CALLER", "StatusCallbackEvent": "participant-leave"},
    )
    assert w.twilio.statuses_for("CA_AGENT") == ["completed"]
    assert w.agent("hold", {"call_sid": "CA_AGENT"}).status_code in (403, 409)


def test_last_agent_leaving_hangs_up_held_caller_and_enters_acw() -> None:
    w = _World()
    w.agent("hold", {"call_sid": "CA_AGENT"})
    w.dial_status()
    w.twilio_post(
        "/webhooks/phone/conference-status?caller_sid=CA_CALLER",
        {"CallSid": "CA_AGENT", "StatusCallbackEvent": "participant-leave"},
    )
    assert w.twilio.statuses_for("CA_CALLER") == ["completed"]
    assert w.left == [17]


# --- auth and ownership -----------------------------------------------------


def test_hold_requires_a_session() -> None:
    w = _World()
    assert w.agent("hold", {"call_sid": "CA_AGENT"}, auth=False).status_code == 401


def test_hold_requires_the_permission() -> None:
    w = _World()
    w.perms = set()
    assert w.agent("hold", {"call_sid": "CA_AGENT"}).status_code == 403
    assert w.twilio.updates == []


def test_agent_cannot_hold_a_colleagues_call() -> None:
    w = _World()
    res = w.agent("hold", {"call_sid": "CA_AGENT"}, user_id=99)
    assert res.status_code == 403
    assert w.twilio.updates == []


def test_agent_cannot_act_on_the_caller_leg_or_unknown_call() -> None:
    w = _World()
    assert w.agent("hold", {"call_sid": "CA_CALLER"}).status_code == 403
    assert w.agent("hold", {"call_sid": "CA_NOPE"}).status_code == 403
    assert w.twilio.updates == []


def test_agent_cannot_transfer_a_colleagues_call() -> None:
    w = _World()
    res = w.agent("transfer", {"call_sid": "CA_AGENT", "target_agent_id": 23}, user_id=99)
    assert res.status_code == 403
    assert w.twilio.created == []


def test_ended_leg_cannot_be_held() -> None:
    w = _World()
    w.twilio.legs["CA_AGENT"]["status"] = "completed"
    assert w.agent("hold", {"call_sid": "CA_AGENT"}).status_code == 409


def test_routes_404_when_hold_transfer_is_off() -> None:
    w = _World(_settings(phone_hold_transfer_enabled=False))
    assert w.agent("hold", {"call_sid": "CA_AGENT"}).status_code == 404
    res = w.http.post("/voice/agent/call/capabilities", json={}, headers=_AUTH)
    assert res.status_code == 200 and res.json() == {"hold_transfer": False}


def test_capabilities_reports_hold_transfer_on() -> None:
    w = _World()
    res = w.http.post("/voice/agent/call/capabilities", json={}, headers=_AUTH)
    assert res.json() == {"hold_transfer": True}


def test_twilio_webhooks_reject_bad_signature() -> None:
    w = _World()
    for path in ("conference-status", "transfer/answer", "transfer/status"):
        res = w.http.post(
            f"/webhooks/phone/{path}?caller_sid=CA_CALLER",
            data={"CallSid": "CA_TARGET"},
            headers={"X-Twilio-Signature": "bad"},
        )
        assert res.status_code == 401, path


# --- config -----------------------------------------------------------------


def test_flag_defaults_off() -> None:
    assert Settings(_env_file=None).phone_hold_transfer_enabled is False


def test_hold_transfer_requires_softphone() -> None:
    with pytest.raises(ValueError, match="PHONE_HOLD_TRANSFER_ENABLED"):
        Settings(_env_file=None, phone_hold_transfer_enabled=True)
