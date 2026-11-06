import asyncio

import pytest

from chatbot.features.logisly import ladder, trips


class _Channel:
    def __init__(self, messages=()):
        self._messages = list(messages)
        self.calls = []

    async def messages(self, cid):
        return self._messages

    async def add_private_note(self, cid, text):
        self.calls.append(("note", cid, text))

    async def add_labels(self, cid, labels):
        self.calls.append(("labels", cid, labels))

    async def set_priority(self, cid, p):
        self.calls.append(("priority", cid, p))

    async def set_status(self, cid, s):
        self.calls.append(("status", cid, s))


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(trips, "TRIPS", trips.TripStore.seeded())
    trips.ACTIVE_SESSIONS.clear()
    trips.register_session("crm-22", "LGS-24017", "gps_stop", "Halo Pak?")


def _starter(log):
    async def start_call(_settings, session_id):
        log.append(session_id)
        return "CA1"

    return start_call


async def _drain():
    await asyncio.gather(*list(ladder._TASKS))


async def test_no_reply_calls_then_unanswered_files_unreachable_ticket():
    ch, rang = _Channel([{"message_type": 1}]), []
    await ladder.run_ladder(None, ch, "crm-22", _starter(rang), 0, wait_seconds=0)
    await _drain()
    assert rang == ["crm-22"]
    events = [e["status"] for e in trips.TRIPS.get("LGS-24017")["events"]]
    assert events[-3:] == ["alert:call", "alert:no_answer", "issue"]
    assert ("labels", 22, ["logisly-escalation", "driver-unreachable"]) in ch.calls
    assert ("priority", 22, "high") in ch.calls and ch.calls[-1] == ("status", 22, "open")
    assert "tidak mengangkat" in ch.calls[0][2]


async def test_answered_call_is_not_escalated():
    ch, rang = _Channel([]), []
    await ladder.place_call(None, ch, "crm-22", _starter(rang), wait_seconds=0)
    ladder.mark_connected("crm-22")
    await _drain()
    assert ch.calls == []


async def test_driver_reply_in_chat_stops_the_ladder():
    ch, rang = _Channel([{"message_type": 1}, {"message_type": 0}]), []
    await ladder.run_ladder(None, ch, "crm-22", _starter(rang), 0, wait_seconds=0)
    assert rang == []


async def test_superseded_alert_does_not_call():
    trips.register_session("crm-23", "LGS-24017", "late_milestone", "Halo lagi")
    ch, rang = _Channel([]), []
    await ladder.run_ladder(None, ch, "crm-22", _starter(rang), 0, wait_seconds=0)
    assert rang == []
