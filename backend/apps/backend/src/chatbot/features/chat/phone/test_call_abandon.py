"""FIX-16 (RFP 2026_028 4.66): abandoned-call rate.

A call is "abandoned" when the caller hung up before any agent answered the
handoff `<Dial>` (Twilio posts `DialCallStatus=canceled` to the dial-status
action). Covers the store (idempotent, per-day rate), the dial-status
webhook recording, and the `/metrics/callcenter` exposure.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chatbot.features.chat.phone.abandon_store import (
    AbandonStore,
    build_abandon_store,
    classify_dial_status,
)
from chatbot.features.chat.phone.test_handoff import _client as _handoff_client
from chatbot.features.chat.phone.test_handoff import _DialLog
from chatbot.platform.config import Settings


def _sign(token: str, url: str, params: dict[str, str]) -> str:
    s = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), s.encode(), hashlib.sha1).digest()).decode()


@pytest.fixture(autouse=True)
def _fake_gemini_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-a-real-credential")


@pytest.fixture
async def store(tmp_path) -> AbandonStore:
    s = build_abandon_store(f"sqlite:///{tmp_path}/abandon.db", timezone="UTC")
    await s.init()
    return s


def test_classify_dial_status() -> None:
    assert classify_dial_status("completed") == "answered"
    assert classify_dial_status("canceled") == "abandoned"
    for other in ("no-answer", "busy", "failed", "", "weird"):
        assert classify_dial_status(other) == "unanswered"


async def test_queue_hangup_counts_as_abandoned(store: AbandonStore) -> None:
    await store.record("CA1", "canceled", datetime(2026, 11, 10, 3, 0, tzinfo=UTC))
    rows = await store.daily_rates(date(2026, 11, 10), date(2026, 11, 10))
    assert rows == [
        {"day": "2026-11-10", "offered": 1, "answered": 0, "abandoned": 1,
         "unanswered": 0, "abandon_rate": 1.0}
    ]


async def test_answered_call_not_abandoned(store: AbandonStore) -> None:
    await store.record("CA1", "completed", datetime(2026, 11, 10, 3, 0, tzinfo=UTC))
    rows = await store.daily_rates(date(2026, 11, 10), date(2026, 11, 10))
    assert rows[0]["abandoned"] == 0
    assert rows[0]["answered"] == 1
    assert rows[0]["abandon_rate"] == 0.0


async def test_abandon_rate_by_day(store: AbandonStore) -> None:
    d1 = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)
    d2 = datetime(2026, 11, 11, 3, 0, tzinfo=UTC)
    for sid, st, at in [
        ("A", "completed", d1), ("B", "completed", d1), ("C", "completed", d1),
        ("D", "canceled", d1), ("E", "no-answer", d1),
        ("F", "canceled", d2), ("G", "completed", d2),
    ]:
        await store.record(sid, st, at)
    rows = await store.daily_rates(date(2026, 11, 10), date(2026, 11, 12))
    assert [r["day"] for r in rows] == ["2026-11-10", "2026-11-11"]
    assert rows[0]["offered"] == 5 and rows[0]["abandoned"] == 1
    assert rows[0]["unanswered"] == 1
    assert rows[0]["abandon_rate"] == 0.2
    assert rows[1]["abandon_rate"] == 0.5


async def test_record_is_idempotent_per_call_sid(store: AbandonStore) -> None:
    at = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)
    await store.record("CA1", "canceled", at)
    await store.record("CA1", "canceled", at)
    rows = await store.daily_rates(date(2026, 11, 10), date(2026, 11, 10))
    assert rows[0]["offered"] == 1


async def test_day_bucket_uses_reporting_timezone(tmp_path) -> None:
    s = build_abandon_store(f"sqlite:///{tmp_path}/tz.db", timezone="Asia/Kuala_Lumpur")
    await s.init()
    # 17:00 UTC on the 10th is 01:00 on the 11th in KL.
    await s.record("CA1", "canceled", datetime(2026, 11, 10, 17, 0, tzinfo=UTC))
    rows = await s.daily_rates(date(2026, 11, 11), date(2026, 11, 11))
    assert rows[0]["day"] == "2026-11-11"


async def test_store_fails_open(tmp_path) -> None:
    s = build_abandon_store("sqlite:////nonexistent_dir_xyz/a.db", timezone="UTC")
    await s.record("CA1", "canceled", datetime(2026, 11, 10, tzinfo=UTC))  # no raise
    assert await s.daily_rates(date(2026, 11, 10), date(2026, 11, 10)) == []


def test_flag_defaults_off() -> None:
    s = Settings(_env_file=None)
    assert s.call_abandon_enabled is False
    assert s.call_abandon_database_url == ""


# --- webhook wiring ---------------------------------------------------------


class _Spy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def record(self, call_sid: str, dial_status: str, at: Any = None) -> None:
        self.calls.append((call_sid, dial_status))


def _post(client: TestClient, settings: Settings, status: str, sid: str = "CA9") -> None:
    params = {"CallSid": sid, "DialCallStatus": status}
    sig = _sign(settings.twilio_auth_token, "http://testserver/webhooks/phone/dial-status", params)
    res = client.post(
        "/webhooks/phone/dial-status", data=params, headers={"X-Twilio-Signature": sig}
    )
    assert res.status_code == 200


@pytest.mark.parametrize("status", ["canceled", "completed", "no-answer"])
def test_dial_status_webhook_records_outcome(status: str) -> None:
    spy = _Spy()
    client, settings = _handoff_client(_DialLog(), abandon_store=spy)
    _post(client, settings, status)
    assert spy.calls == [("CA9", status)]


def test_dial_status_webhook_survives_recorder_failure() -> None:
    class _Boom:
        async def record(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("db down")

    client, settings = _handoff_client(_DialLog(), abandon_store=_Boom())
    _post(client, settings, "canceled")  # still 200 + TwiML


# --- /metrics/callcenter exposure -------------------------------------------


def _metrics_client(reader: Any | None) -> TestClient:
    from fastapi import FastAPI

    from chatbot.features.metrics.insights_router import build_metrics_insights_router
    from chatbot.features.metrics.query_port import MockMetricsQuery

    settings = Settings(_env_file=None, metrics_api_key="k")
    app = FastAPI()
    app.include_router(
        build_metrics_insights_router(MockMetricsQuery(), settings, abandon_reader=reader)
    )
    return TestClient(app)


class _Reader:
    async def daily_rates(self, start: date, end: date) -> list[dict[str, Any]]:
        return [{"day": "2026-11-10", "offered": 5, "answered": 3, "abandoned": 1,
                 "unanswered": 1, "abandon_rate": 0.2}]


def test_callcenter_includes_abandoned_calls_when_reader_wired() -> None:
    res = _metrics_client(_Reader()).get(
        "/metrics/callcenter?from=2026-11-10&to=2026-11-10&granularity=day",
        headers={"x-api-key": "k"},
    )
    assert res.status_code == 200
    assert res.json()["abandoned_calls"]["daily"][0]["abandon_rate"] == 0.2


def test_callcenter_unchanged_without_reader() -> None:
    res = _metrics_client(None).get("/metrics/callcenter", headers={"x-api-key": "k"})
    assert res.status_code == 200
    assert "abandoned_calls" not in res.json()


def test_callcenter_survives_reader_failure() -> None:
    class _Bad:
        async def daily_rates(self, *a: Any) -> list[dict[str, Any]]:
            raise RuntimeError("db down")

    res = _metrics_client(_Bad()).get(
        "/metrics/callcenter?from=2026-11-10&to=2026-11-10&granularity=day",
        headers={"x-api-key": "k"},
    )
    assert res.status_code == 200
    assert res.json()["abandoned_calls"] == {"daily": [], "ok": False}
