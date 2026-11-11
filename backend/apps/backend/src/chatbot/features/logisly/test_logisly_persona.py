from types import SimpleNamespace

import pytest

from chatbot.features.chat import service as chat_service
from chatbot.features.chat.agents import build_ai_agent
from chatbot.features.chat.prompts import (
    AGENT_INSTRUCTION,
    DEFAULT_MEDIA_DIAGNOSIS_INSTRUCTION,
    LOGISLY_AGENT_INSTRUCTION,
    LOGISLY_MEDIA_INSTRUCTION,
    build_agent_instruction,
    instructions_for_persona,
)
from chatbot.features.logisly import milestones
from chatbot.features.logisly.trips import ACTIVE_SESSIONS, TRIPS, TripStore, describe
from chatbot.platform.config import Settings


def test_proton_is_the_default_persona_and_unchanged():
    assert instructions_for_persona("proton") == (
        AGENT_INSTRUCTION,
        DEFAULT_MEDIA_DIAGNOSIS_INSTRUCTION,
    )
    assert (
        build_agent_instruction(media_diagnosis_prompt_enabled=True, has_image=False)
        == AGENT_INSTRUCTION
    )


def test_logisly_persona_swaps_base_and_media_wording():
    base, media = instructions_for_persona("logisly")
    assert base is LOGISLY_AGENT_INSTRUCTION and media is LOGISLY_MEDIA_INSTRUCTION
    out = build_agent_instruction(
        media_diagnosis_prompt_enabled=True,
        has_image=True,
        base=base,
        default_media_instruction=media,
    )
    assert out == f"{LOGISLY_AGENT_INSTRUCTION}\n\n{LOGISLY_MEDIA_INSTRUCTION}"
    assert "Proton" not in out


def test_logisly_prompt_keeps_ordinary_trip_events_out_of_escalation():
    for phrase in ("Traffic or congestion", "flat or burst tyre", "not a complaint"):
        assert phrase in LOGISLY_AGENT_INSTRUCTION


def test_trip_store_lookup_is_case_insensitive_and_records_events():
    store = TripStore.seeded()
    assert store.get("lgs-24017")["driver_name"] == "Pak Budi Santoso"
    trip = store.update("LGS-24017", "delayed", "Ban pecah, ganti ban di rest area KM 57")
    assert trip["status"] == "delayed"
    assert "Ban pecah" in describe(trip)
    assert store.update("LGS-00000", "delayed", "x") is None


def test_unknown_status_is_logged_but_does_not_change_status():
    store = TripStore.seeded()
    trip = store.update("LGS-24017", "teleported", "x")
    assert trip["status"] == "to_pickup" and trip["events"][-1]["status"] == "teleported"


def test_ambiguous_problems_get_one_question_before_escalation():
    assert "ask before escalating" in LOGISLY_AGENT_INSTRUCTION
    assert "only after the driver says they cannot continue" in LOGISLY_AGENT_INSTRUCTION
    assert "is not an accident" in LOGISLY_MEDIA_INSTRUCTION


def test_driver_eta_replaces_planned_eta_only_when_given():
    store = TripStore.seeded()
    store.update("LGS-24017", "delayed", "Telat 1 jam", "2026-11-06 15:45 WIB")
    assert store.get("LGS-24017")["eta"] == "2026-11-06 15:45 WIB"
    store.update("LGS-24017", "on_trip", "Jalan lagi")
    assert store.get("LGS-24017")["eta"] == "2026-11-06 15:45 WIB"


def test_logisly_prompt_has_checklist_rules():
    text = LOGISLY_AGENT_INSTRUCTION
    assert "## Milestone checklist" in text
    for phrase in ("record_checklist_tool", "two", "install", "belum bisa login"):
        assert phrase in text
    assert "overlay" in LOGISLY_MEDIA_INSTRUCTION


@pytest.fixture
def logisly_tools(monkeypatch):
    # monkeypatch undoes the env change after each test, so no leakage.
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    agent = build_ai_agent(Settings(), None, None, persona="logisly")
    return {t.__name__: t for t in agent.tools}, agent


def test_logisly_tools_include_record_checklist(logisly_tools):
    tools, agent = logisly_tools
    assert "record_checklist_tool" in tools
    assert agent.tools[0].__name__ == "record_checklist_tool"


async def test_record_checklist_tool_records_and_queues_ticket(logisly_tools):
    tools, _ = logisly_tools
    trip = TRIPS.get("LGS-24017")
    ms = "at_pickup"
    item = next(i for i in milestones.CHECKLISTS[ms] if i.team)
    ctx = SimpleNamespace(state={}, session=SimpleNamespace(id="crm-777"))
    ACTIVE_SESSIONS["crm-777"] = {"order_id": "LGS-24017", "milestone": ms}
    before = len(milestones.PENDING_TICKETS)
    try:
        out = await tools["record_checklist_tool"](ctx, "LGS-24017", item.id, False, "B 9 XX")
        assert out == "[internal] not_ok"
        assert ctx.state["order_id"] == "LGS-24017"
        assert milestones.PENDING_TICKETS[-1]["session_id"] == "crm-777"
        assert (
            await tools["record_checklist_tool"](ctx, "LGS-00000", item.id, True, "")
        ).startswith("NOT_FOUND")
        assert "ERROR" in await tools["record_checklist_tool"](ctx, "LGS-24017", "nope", True, "")
    finally:
        ACTIVE_SESSIONS.pop("crm-777", None)
        del milestones.PENDING_TICKETS[before:]
        trip.get("checklists", {}).pop(ms, None)


def _hook_service(monkeypatch, persona):
    calls = {"tickets": 0, "notes": []}

    async def fake_tickets(*_a, **_k):
        calls["tickets"] += 1
        return 0

    async def fake_note(_settings, session_id, order_id, milestone, **_k):
        calls["notes"].append((session_id, order_id, milestone))
        return True

    monkeypatch.setattr(chat_service, "file_checklist_tickets", fake_tickets)
    monkeypatch.setattr(chat_service, "note_checklist_complete", fake_note)
    svc = chat_service.OrchestratorService.__new__(chat_service.OrchestratorService)
    svc._settings = SimpleNamespace(agent_persona=persona)
    return svc, calls


async def test_after_turn_hook_is_a_noop_for_proton(monkeypatch):
    svc, calls = _hook_service(monkeypatch, "proton")
    await svc._after_logisly_turn("crm-1")
    assert calls == {"tickets": 0, "notes": []}


async def test_after_turn_hook_without_session_entry_only_files_tickets(monkeypatch):
    svc, calls = _hook_service(monkeypatch, "logisly")
    await svc._after_logisly_turn("crm-nope")
    assert calls == {"tickets": 1, "notes": []}


async def test_after_turn_hook_notes_a_complete_checklist(monkeypatch):
    svc, calls = _hook_service(monkeypatch, "logisly")
    monkeypatch.setitem(
        ACTIVE_SESSIONS, "crm-2", {"order_id": "LGS-24017", "milestone": "at_pickup"}
    )
    monkeypatch.setattr(milestones, "is_complete", lambda *_a: True)
    await svc._after_logisly_turn("crm-2")
    assert calls == {"tickets": 1, "notes": [("crm-2", "LGS-24017", "at_pickup")]}
    monkeypatch.setattr(milestones, "is_complete", lambda *_a: False)
    calls["notes"].clear()
    await svc._after_logisly_turn("crm-2")
    assert calls["notes"] == []
