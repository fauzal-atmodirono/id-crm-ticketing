import pytest

from chatbot.features.logisly import escalation, trips


class _FakeChannel:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def add_private_note(self, conversation_id, text):
        if self.fail:
            raise RuntimeError("chatwoot down")
        self.calls.append(("note", conversation_id, text))

    async def add_labels(self, conversation_id, labels):
        self.calls.append(("labels", conversation_id, labels))

    async def set_priority(self, conversation_id, priority):
        self.calls.append(("priority", conversation_id, priority))


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(trips, "TRIPS", trips.TripStore.seeded())
    trips.ACTIVE_SESSIONS.clear()


def _state(**over):
    s = {
        "handoff_reason": "cargo_issue",
        "handoff_summary": "Truk diserempet di Tol Cipali, 3 karton penyok.",
    }
    s.update(over)
    return s


async def test_alert_conversation_files_full_ticket_and_records_trip():
    trips.register_session("crm-22", "LGS-24017", "gps_stop", "Halo Pak, truk berhenti 40 menit?")
    trips.TRIPS.update("LGS-24017", "alert:gps_stop", "GPS: berhenti")
    trips.TRIPS.update("LGS-24017", "issue", "Ban pecah di rest area.")
    ch = _FakeChannel()

    await escalation.file_escalation(None, "crm-22", _state(), channel=ch)

    kind, conv, note = ch.calls[0]
    assert (kind, conv) == ("note", 22)
    assert "Muatan rusak" in note and "HIGH" in note
    assert "3 karton penyok" in note and "LGS-24017" in note and "Pak Budi Santoso" in note
    assert "Alert pelacakan: Truk berhenti lama" in note
    assert "Ban pecah di rest area." in note and "GPS: berhenti" not in note
    assert ch.calls[1] == ("labels", 22, ["logisly-escalation", "cargo-issue"])
    assert ch.calls[2] == ("priority", 22, "high")
    last = trips.TRIPS.get("LGS-24017")["events"][-1]
    assert last["status"] == "issue" and "Eskalasi ke operasional" in last["note"]


async def test_safety_incident_is_urgent_and_unknown_trip_is_stated():
    ch = _FakeChannel()
    await escalation.file_escalation(
        None, "crm-9", _state(handoff_reason="safety_incident"), channel=ch
    )
    assert ch.calls[2] == ("priority", 9, "urgent")
    assert "belum teridentifikasi" in ch.calls[0][2]


async def test_order_from_state_is_used_when_driver_started_the_conversation():
    ch = _FakeChannel()
    await escalation.file_escalation(None, "crm-5", _state(order_id="LGS-24033"), channel=ch)
    assert "LGS-24033" in ch.calls[0][2]
    assert trips.TRIPS.get("LGS-24033")["status"] == "issue"


async def test_chatwoot_failure_is_swallowed_and_trip_still_recorded():
    trips.register_session("crm-22", "LGS-24017", "no_pod", "x")
    await escalation.file_escalation(None, "crm-22", _state(), channel=_FakeChannel(fail=True))
    assert trips.TRIPS.get("LGS-24017")["status"] == "issue"


async def test_non_crm_session_posts_nothing():
    ch = _FakeChannel()
    await escalation.file_escalation(None, "whatsapp-+62", _state(), channel=ch)
    assert ch.calls == []
