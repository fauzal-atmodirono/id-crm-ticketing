"""Task 31 (FIX-31, FIX-56 step 7): the Proton SOP DTMF IVR.

End to end through the real app (`create_app` + `build_chat_router`), with
Twilio's side simulated by signed form posts and the Chatwoot side by a fake
`ConversationLogPort`. Nothing here reaches Twilio, Chatwoot or Gemini.

The SOP drift tests at the bottom read `docs/client-materials/CRM Process Flow
(1).xlsx` itself -- the sheet cells AND the flowchart shapes in
`xl/drawings/drawing4.xml` (openpyxl drops shapes) -- so a wording edit in
`ivr_prompts.py` that departs from Proton's SOP fails here.
"""

from __future__ import annotations

import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from xml.sax.saxutils import unescape

import pytest

from chatbot.features.chat.phone import ivr_prompts as p
from chatbot.features.chat.phone.test_handoff import _client, _FakeLog, _sign
from chatbot.features.chat.phone.twiml import connect_stream_twiml
from chatbot.features.chat.ports import ConversationLogResult
from chatbot.platform.config import Settings

_BASE = "https://example.ngrok.app"
_TOKEN = "test_token"
_RSA = "+60311110001"
_AGENT = "+60311110002"
_CALLER_ID = "+60311119999"

# 2026-11-10 is a Tuesday. KL is UTC+8, no DST.
_TUE_1000_KL = datetime(2026, 11, 10, 2, 0, tzinfo=UTC)
_SUN_2000_KL = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fake_gemini_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-a-real-credential")


class _IvrLog(_FakeLog):
    def __init__(self) -> None:
        super().__init__()
        self.ensured: list[tuple[str, str, str | None]] = []
        self.comments: list[tuple[str, str, str | None]] = []
        self.recordings: list[tuple[str, dict[str, Any]]] = []

    async def ensure_conversation_ticket(
        self, session_id: str, subject: str, customer_name: str | None, customer_phone: str | None
    ) -> str:
        self.ensured.append((session_id, subject, customer_phone))
        return "9"

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> ConversationLogResult:
        self.comments.append((ticket_id, text, status))
        return ConversationLogResult.OK

    async def set_call_recording(self, ticket_id: str, **kwargs: Any) -> None:
        self.recordings.append((ticket_id, kwargs))


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "phone_dtmf_menu_enabled": True,
        "twilio_auth_token": _TOKEN,
        "twilio_account_sid": "AC1",
        "twilio_webhook_base_url": _BASE,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    from chatbot.features.chat.phone import ivr_router

    state = {"now": _TUE_1000_KL}
    monkeypatch.setattr(ivr_router, "_now", lambda: state["now"])
    return state


def _post(client, path: str, query: dict[str, Any], form: dict[str, str], token: str = _TOKEN):
    qs = urlencode(query)
    url = f"{_BASE}{path}?{qs}" if qs else f"{_BASE}{path}"
    params = {"CallSid": "CA1", "From": "+60129998888", **form}
    return client.post(
        f"{path}?{qs}" if qs else path,
        data=params,
        headers={"X-Twilio-Signature": _sign(token, url, params)},
    )


def _text(xml: str) -> str:
    return unescape(xml)


# --- flags off --------------------------------------------------------------


def test_flags_off_unchanged() -> None:
    settings = Settings(_env_file=None, twilio_webhook_base_url=_BASE)
    client, _ = _client(_IvrLog(), settings=settings)
    res = client.post("/voice/phone/incoming")
    assert res.status_code == 200
    assert res.text == connect_stream_twiml("wss://example.ngrok.app/voice/phone/stream", None)
    for path in (
        "/voice/phone/ivr/language",
        "/voice/phone/ivr/menu",
        "/voice/phone/ivr/ring",
        "/voice/phone/ivr/voicemail",
    ):
        assert client.post(path).status_code == 404, path


def test_new_flags_default_off() -> None:
    s = Settings(_env_file=None)
    assert s.phone_dtmf_menu_enabled is False
    assert s.phone_after_hours_enabled is False
    assert s.phone_voicemail_ingest_enabled is False
    assert s.phone_rsa_after_hours_bypass is True
    assert s.phone_ivr_non_rsa_route == "ai"
    assert s.phone_ivr_queue_busy_after_seconds == 10
    assert s.phone_ivr_agent_attend_seconds == 20
    assert s.phone_ivr_timezone == "Asia/Kuala_Lumpur"
    assert s.phone_tts_voice_en == "Google.en-US-Standard-C"
    assert s.phone_tts_voice_ms == "Google.ms-MY-Standard-A"


def test_menu_on_without_base_url_falls_back_to_the_bridge() -> None:
    settings = _settings(twilio_webhook_base_url="", public_wss_base_url="wss://x")
    client, _ = _client(_IvrLog(), settings=settings)
    res = client.post("/voice/phone/incoming")
    assert "<Gather" not in res.text
    assert "<Connect><Stream" in res.text


# --- language menu ----------------------------------------------------------


def test_incoming_plays_bilingual_welcome_with_a_female_voice_per_language() -> None:
    client, _ = _client(_IvrLog(), settings=_settings())
    xml = client.post("/voice/phone/incoming").text
    assert '<Gather input="dtmf" numDigits="1"' in xml
    assert f"{_BASE}/voice/phone/ivr/language?attempt=0" in _text(xml)
    assert (
        '<Say language="en-US" voice="Google.en-US-Standard-C">Welcome to Proton e.MAS centre.</Say>'
        in xml
    )
    assert (
        '<Say language="ms-MY" voice="Google.ms-MY-Standard-A">'
        "Selamat datang ke Pusat Perkhidmatan Proton e.MAS.</Say>" in xml
    )
    assert "For English, press 1." in xml and "Untuk Bahasa Melayu, tekan 2." in xml
    # A silent timeout must reach the decision code, not fall off the end.
    assert "<Redirect" in xml


def test_tts_voice_is_configurable() -> None:
    client, _ = _client(
        _IvrLog(), settings=_settings(phone_tts_voice_en="Polly.Joanna", phone_tts_voice_ms="Google.ms-MY-Wavenet-A")
    )
    xml = client.post("/voice/phone/incoming").text
    assert 'voice="Polly.Joanna"' in xml and 'voice="Google.ms-MY-Wavenet-A"' in xml


def test_dtmf_menu_bm_branch() -> None:
    client, _ = _client(_IvrLog(), settings=_settings())
    res = _post(client, "/voice/phone/ivr/language", {"attempt": 0}, {"Digits": "2"})
    assert res.status_code == 200
    xml = _text(res.text)
    assert f'<Say language="ms-MY" voice="Google.ms-MY-Standard-A">{p.MENU_MS}</Say>' in xml
    assert "Untuk Ulangan, sila tekan 0." in xml
    assert f"{_BASE}/voice/phone/ivr/menu?lang=ms&attempt=0" in xml
    en = _text(_post(client, "/voice/phone/ivr/language", {"attempt": 0}, {"Digits": "1"}).text)
    assert p.MENU_EN in en and "lang=en" in en


def test_language_timeout_repeats_once_then_routes_on() -> None:
    client, _ = _client(_IvrLog(), settings=_settings())
    first = _text(_post(client, "/voice/phone/ivr/language", {"attempt": 0}, {}).text)
    assert p.WELCOME_EN in first and "attempt=1" in first
    second = _post(client, "/voice/phone/ivr/language", {"attempt": 1}, {}).text
    assert "<Gather" not in second
    assert "<Connect><Stream" in second  # non-RSA route, "ai" by default


# --- category menu ----------------------------------------------------------


def test_dtmf_1_routes_rsa() -> None:
    client, _ = _client(
        _IvrLog(), settings=_settings(phone_ivr_rsa_number=_RSA, phone_handoff_caller_id=_CALLER_ID)
    )
    xml = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "1"}).text)
    assert f"<Number>{_RSA}</Number>" in xml
    assert 'timeout="20"' in xml
    assert f'callerId="{_CALLER_ID}"' in xml
    assert f"{_BASE}/voice/phone/ivr/ring?lang=en&cat=rsa&stage=final" in xml
    assert p.QUEUE_BUSY_EN not in xml  # the busy prompt is for non-RSA only


def test_rsa_without_a_number_goes_to_the_ai_bridge_24_7() -> None:
    client, _ = _client(_IvrLog(), settings=_settings())
    xml = _post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "1"}).text
    assert "<Connect><Stream" in xml


def test_unanswered_rsa_line_falls_back_to_the_ai_bridge() -> None:
    client, _ = _client(_IvrLog(), settings=_settings(phone_ivr_rsa_number=_RSA))
    xml = _post(
        client, "/voice/phone/ivr/ring", {"lang": "en", "cat": "rsa", "stage": "final"},
        {"DialCallStatus": "no-answer"},
    ).text
    assert "<Connect><Stream" in xml


def test_inquiry_and_complaint_take_the_ai_route_by_default() -> None:
    client, _ = _client(_IvrLog(), settings=_settings())
    for digit in ("2", "3"):
        xml = _post(client, "/voice/phone/ivr/menu", {"lang": "ms", "attempt": 0}, {"Digits": digit}).text
        assert "<Connect><Stream" in xml


def test_repeat_on_zero() -> None:
    client, _ = _client(_IvrLog(), settings=_settings())
    again = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "0"}).text)
    assert p.MENU_EN in again and "attempt=0" in again
    bm = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "ms", "attempt": 0}, {"Digits": "0"}).text)
    assert p.MENU_MS in bm
    # A first timeout repeats and counts; a second routes on to a person/AI.
    timeout = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {}).text)
    assert p.MENU_EN in timeout and "attempt=1" in timeout
    invalid = _post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 1}, {"Digits": "9"}).text
    assert "<Gather" not in invalid and "<Connect><Stream" in invalid


# --- ring stage -------------------------------------------------------------


def _agent_settings(**overrides: Any) -> Settings:
    return _settings(
        phone_ivr_non_rsa_route="agent",
        phone_handoff_target_number=_AGENT,
        phone_handoff_caller_id=_CALLER_ID,
        **overrides,
    )


@pytest.mark.usefixtures("clock")
def test_queue_busy_prompt_after_10s() -> None:
    client, _ = _client(_IvrLog(), settings=_agent_settings(phone_voicemail_ingest_enabled=True))
    first = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "2"}).text)
    assert f"<Number>{_AGENT}</Number>" in first
    assert 'timeout="10"' in first
    assert "cat=inquiry&stage=1" in first
    assert p.QUEUE_BUSY_EN not in first  # not before the 10 s of ringing

    busy = _text(
        _post(
            client, "/voice/phone/ivr/ring", {"lang": "en", "cat": "inquiry", "stage": "1"},
            {"DialCallStatus": "no-answer"},
        ).text
    )
    assert busy.index(p.QUEUE_BUSY_EN) < busy.index("<Dial")
    assert 'timeout="10"' in busy  # the rest of the 20 s attend window
    assert "stage=2" in busy

    bm = _text(
        _post(
            client, "/voice/phone/ivr/ring", {"lang": "ms", "cat": "complaint", "stage": "1"},
            {"DialCallStatus": "no-answer"},
        ).text
    )
    assert p.QUEUE_BUSY_MS in bm and "pelanggan" in bm and "pelanngan" not in bm

    final = _text(
        _post(
            client, "/voice/phone/ivr/ring", {"lang": "en", "cat": "inquiry", "stage": "2"},
            {"DialCallStatus": "no-answer"},
        ).text
    )
    assert "<Record" in final and "reason=no_answer" in final


@pytest.mark.usefixtures("clock")
def test_answered_ring_hangs_up_when_the_agent_leg_ends() -> None:
    client, _ = _client(_IvrLog(), settings=_agent_settings())
    xml = _post(
        client, "/voice/phone/ivr/ring", {"lang": "en", "cat": "inquiry", "stage": "1"},
        {"DialCallStatus": "completed"},
    ).text
    assert "<Hangup/>" in xml and "<Dial" not in xml


@pytest.mark.usefixtures("clock")
def test_ring_out_without_voicemail_apologises_and_hangs_up() -> None:
    client, _ = _client(_IvrLog(), settings=_agent_settings())
    xml = _text(
        _post(
            client, "/voice/phone/ivr/ring", {"lang": "ms", "cat": "inquiry", "stage": "2"},
            {"DialCallStatus": "busy"},
        ).text
    )
    assert p.NO_AGENT_MS in xml and "<Record" not in xml and "<Hangup/>" in xml


@pytest.mark.usefixtures("clock")
def test_agent_route_with_no_target_falls_back_to_the_ai_bridge() -> None:
    client, _ = _client(_IvrLog(), settings=_settings(phone_ivr_non_rsa_route="agent"))
    xml = _post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "3"}).text
    assert "<Connect><Stream" in xml


# --- after hours, voicemail -------------------------------------------------


def test_after_hours_plays_message_and_records_voicemail(clock) -> None:
    clock["now"] = _SUN_2000_KL
    log = _IvrLog()
    client, _ = _client(
        log,
        settings=_agent_settings(phone_after_hours_enabled=True, phone_voicemail_ingest_enabled=True),
    )
    xml = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "ms", "attempt": 0}, {"Digits": "3"}).text)
    assert xml.index(p.AFTER_HOURS_MS) < xml.index(p.AFTER_HOURS_EN)  # caller's language first
    assert "<Dial" not in xml
    assert "<Record" in xml
    assert f"{_BASE}/voice/phone/ivr/voicemail?lang=ms&cat=complaint&reason=after_hours" in xml

    res = _post(
        client,
        "/voice/phone/ivr/voicemail",
        {"lang": "ms", "cat": "complaint", "reason": "after_hours"},
        {
            "RecordingSid": "RE77",
            "RecordingUrl": "https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE77",
            "RecordingDuration": "14",
        },
    )
    assert res.status_code == 200
    assert p.GOODBYE_MS in _text(res.text) and "<Hangup/>" in res.text
    assert log.ensured and log.ensured[0][0] == "phone-CA1"
    assert log.ensured[0][2] == "+60129998888"
    assert "Voicemail" in log.ensured[0][1]
    note = log.comments[0][1]
    assert "https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE77.mp3" in note
    assert "Complaint" in note and "after hours" in note.lower()
    assert log.recordings[0][1]["recording_sid"] == "RE77"
    assert ("9", "voicemail") in log.tags


@pytest.mark.usefixtures("clock")
def test_voicemail_redelivery_does_not_create_a_second_note() -> None:
    from chatbot.features.chat.phone.voicemail_ingest import reset_processed_voicemails

    reset_processed_voicemails()
    log = _IvrLog()
    client, _ = _client(log, settings=_agent_settings(phone_voicemail_ingest_enabled=True))
    form = {"RecordingSid": "RE78", "RecordingUrl": "https://api.twilio.com/RE78", "RecordingDuration": "3"}
    q = {"lang": "en", "cat": "inquiry", "reason": "no_answer"}
    _post(client, "/voice/phone/ivr/voicemail", q, form)
    _post(client, "/voice/phone/ivr/voicemail", q, form)
    assert len(log.comments) == 1


def test_after_hours_without_voicemail_plays_message_then_goodbye(clock) -> None:
    clock["now"] = _SUN_2000_KL
    client, _ = _client(_IvrLog(), settings=_settings(phone_after_hours_enabled=True))
    xml = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "2"}).text)
    assert p.AFTER_HOURS_EN in xml and p.AFTER_HOURS_MS in xml
    assert "<Record" not in xml and "<Hangup/>" in xml
    assert client.post("/voice/phone/ivr/voicemail").status_code == 404


@pytest.mark.usefixtures("clock")
def test_in_hours_non_rsa_is_not_sent_to_after_hours() -> None:
    client, _ = _client(_IvrLog(), settings=_settings(phone_after_hours_enabled=True))
    xml = _post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "2"}).text
    assert p.AFTER_HOURS_EN not in _text(xml) and "<Connect><Stream" in xml


def test_rsa_bypasses_after_hours(clock) -> None:
    clock["now"] = _SUN_2000_KL
    client, _ = _client(
        _IvrLog(),
        settings=_settings(
            phone_after_hours_enabled=True, phone_voicemail_ingest_enabled=True, phone_ivr_rsa_number=_RSA
        ),
    )
    xml = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "1"}).text)
    assert f"<Number>{_RSA}</Number>" in xml
    assert p.AFTER_HOURS_EN not in xml and "<Record" not in xml


def test_rsa_after_hours_bypass_off_is_honoured(clock) -> None:
    clock["now"] = _SUN_2000_KL
    client, _ = _client(
        _IvrLog(),
        settings=_settings(
            phone_after_hours_enabled=True, phone_rsa_after_hours_bypass=False, phone_ivr_rsa_number=_RSA
        ),
    )
    xml = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "1"}).text)
    assert p.AFTER_HOURS_EN in xml


# --- office hours -----------------------------------------------------------


@pytest.mark.parametrize(
    ("utc", "open_"),
    [
        (datetime(2026, 11, 10, 0, 29, tzinfo=UTC), False),  # Tue 08:29
        (datetime(2026, 11, 10, 0, 30, tzinfo=UTC), True),  # Tue 08:30
        (datetime(2026, 11, 10, 9, 29, tzinfo=UTC), True),  # Tue 17:29
        (datetime(2026, 11, 10, 9, 30, tzinfo=UTC), False),  # Tue 17:30
        (datetime(2026, 11, 14, 0, 59, tzinfo=UTC), False),  # Sat 08:59
        (datetime(2026, 11, 14, 1, 0, tzinfo=UTC), True),  # Sat 09:00
        (datetime(2026, 11, 15, 8, 59, tzinfo=UTC), True),  # Sun 16:59
        (datetime(2026, 11, 15, 9, 0, tzinfo=UTC), False),  # Sun 17:00
        (datetime(2026, 12, 25, 0, 45, tzinfo=UTC), False),  # Fri holiday 08:45
        (datetime(2026, 12, 25, 1, 15, tzinfo=UTC), True),  # Fri holiday 09:15
        (datetime(2026, 12, 25, 9, 15, tzinfo=UTC), False),  # Fri holiday 17:15
    ],
)
def test_sop_office_hours(utc: datetime, open_: bool) -> None:
    from chatbot.features.chat.phone.ivr import within_office_hours

    assert within_office_hours(Settings(_env_file=None), utc) is open_


def test_malformed_office_hours_fail_open() -> None:
    from chatbot.features.chat.phone.ivr import within_office_hours

    s = Settings(_env_file=None, phone_ivr_weekday_hours="nonsense")
    assert within_office_hours(s, datetime(2026, 11, 10, 15, 0, tzinfo=UTC)) is True


# --- signature validation on every new webhook -------------------------------


_ROUTES = [
    ("/voice/phone/ivr/language", {"attempt": 0}, {"Digits": "1"}),
    ("/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "1"}),
    ("/voice/phone/ivr/ring", {"lang": "en", "cat": "inquiry", "stage": "1"}, {"DialCallStatus": "no-answer"}),
    (
        "/voice/phone/ivr/voicemail",
        {"lang": "en", "cat": "inquiry", "reason": "no_answer"},
        {"RecordingSid": "RE1", "RecordingUrl": "https://api.twilio.com/RE1"},
    ),
]


@pytest.mark.parametrize(("path", "query", "form"), _ROUTES)
def test_every_ivr_webhook_rejects_a_bad_signature(path, query, form) -> None:
    log = _IvrLog()
    client, _ = _client(log, settings=_agent_settings(phone_voicemail_ingest_enabled=True))
    res = _post(client, path, query, form, token="wrong-token")
    assert res.status_code == 401
    assert log.comments == [] and log.ensured == []


@pytest.mark.parametrize(("path", "query", "form"), _ROUTES)
def test_every_ivr_webhook_rejects_a_tampered_query(path, query, form) -> None:
    client, _ = _client(_IvrLog(), settings=_agent_settings(phone_voicemail_ingest_enabled=True))
    qs = urlencode(query)
    params = {"CallSid": "CA1", **form}
    sig = _sign(_TOKEN, f"{_BASE}{path}?{qs}", params)
    res = client.post(f"{path}?{qs}&x=1", data=params, headers={"X-Twilio-Signature": sig})
    assert res.status_code == 401


@pytest.mark.parametrize(("path", "query", "form"), _ROUTES)
def test_every_ivr_webhook_refuses_without_an_auth_token(path, query, form) -> None:
    client, _ = _client(
        _IvrLog(), settings=_agent_settings(phone_voicemail_ingest_enabled=True, twilio_auth_token="")
    )
    res = _post(client, path, query, form)
    assert res.status_code == 401


def test_menu_without_auth_token_keeps_todays_bridge_on_incoming() -> None:
    client, _ = _client(_IvrLog(), settings=_settings(twilio_auth_token=""))
    xml = client.post("/voice/phone/incoming").text
    assert "<Gather" not in xml and "<Connect><Stream" in xml


def test_placeholder_rsa_number_refused_at_startup() -> None:
    from chatbot.features.chat.phone.handoff_target import validate_handoff_target_settings

    with pytest.raises(ValueError, match="phone_ivr_rsa_number"):
        validate_handoff_target_settings(_settings(phone_ivr_rsa_number="+60300000001"))


# --- SOP drift: read the xlsx itself ----------------------------------------

_XLSX = Path(__file__).resolve().parents[8] / "docs" / "client-materials" / "CRM Process Flow (1).xlsx"
assert _XLSX.is_file(), f"SOP workbook not found at {_XLSX}"

# The only departures from the SOP's literal text; see ivr_prompts.py.
_SOP_CORRECTIONS = {"Asistance": "Assistance", "pelanngan": "pelanggan", "terdekat.Sila": "terdekat. Sila"}


def _norm(text: str) -> str:
    for wrong, right in _SOP_CORRECTIONS.items():
        text = text.replace(wrong, right)
    return re.sub(r"\s+", " ", text).strip()


def _flowchart_lines() -> list[str]:
    with zipfile.ZipFile(_XLSX) as z:
        xml = z.read("xl/drawings/drawing4.xml").decode("utf-8")
    lines = []
    for para in re.findall(r"<a:p>(.*?)</a:p>", xml, re.S):
        line = _norm(unescape("".join(re.findall(r"<a:t>([^<]*)</a:t>", para))))
        if line:
            lines.append(line)
    return lines


def _sheet_cell(ref: str) -> str:
    import openpyxl

    ws = openpyxl.load_workbook(_XLSX)["IVR Call"]
    return _norm(str(ws[ref].value))


def test_welcome_and_language_choice_match_the_sop_flowchart() -> None:
    lines = _flowchart_lines()
    for prompt in (p.WELCOME_EN, p.WELCOME_MS, p.LANGUAGE_CHOICE_EN, p.LANGUAGE_CHOICE_MS):
        assert prompt.rstrip(".") in lines, prompt


@pytest.mark.parametrize("menu", [p.MENU_EN, p.MENU_MS])
def test_category_menus_match_the_sop_flowchart(menu: str) -> None:
    lines = _flowchart_lines()
    for sentence in (s.strip() for s in menu.split(".") if s.strip()):
        assert sentence in lines, sentence


def test_queue_busy_prompts_match_sheet_b29_b30() -> None:
    assert _sheet_cell("B29") == p.QUEUE_BUSY_EN
    assert _sheet_cell("B30") == p.QUEUE_BUSY_MS
    assert "pelanggan" in p.QUEUE_BUSY_MS


def test_sheet_asks_for_a_female_voice_and_both_defaults_are_female() -> None:
    assert "female" in _sheet_cell("B12").lower()
    s = Settings(_env_file=None)
    # Google.en-US-Standard-C and Google.ms-MY-Standard-A are Google's female voices.
    assert (s.phone_tts_voice_en, s.phone_tts_voice_ms) == (
        "Google.en-US-Standard-C",
        "Google.ms-MY-Standard-A",
    )


def test_agent_route_rings_online_softphones_without_a_pstn_caller_id() -> None:
    from chatbot.features.chat.phone.ivr import client_nouns, dial_twiml

    xml = dial_twiml(client_nouns(["agent-7"]), "https://x/ring", 10, _CALLER_ID)
    assert "<Client><Identity>agent-7</Identity>" in xml and "callerId" not in xml
    xml = dial_twiml("<Number>+60311110002</Number>", "https://x/ring", 10, _CALLER_ID)
    assert f'callerId="{_CALLER_ID}"' in xml


# --- review round 1 -----------------------------------------------------------


def _client_with_softphones(log, settings, identities: list[str]):
    import chatbot.features.chat.router as router_mod

    async def _ids(self, exclude_agent_id=None):  # noqa: ARG001
        return identities

    orig = router_mod.ChatRouter._fanout_identities
    router_mod.ChatRouter._fanout_identities = _ids
    try:
        return _client(log, settings=settings)
    finally:
        router_mod.ChatRouter._fanout_identities = orig


def test_voicemail_logs_never_carry_the_raw_caller_number(clock) -> None:
    from structlog.testing import capture_logs

    from chatbot.features.chat.phone.voicemail_ingest import reset_processed_voicemails

    reset_processed_voicemails()
    client, _ = _client(_IvrLog(), settings=_agent_settings(phone_voicemail_ingest_enabled=True))
    with capture_logs() as logs:
        _post(
            client, "/voice/phone/ivr/voicemail", {"lang": "en", "cat": "inquiry", "reason": "no_answer"},
            {"RecordingSid": "RE90", "RecordingUrl": "https://api.twilio.com/RE90", "RecordingDuration": "4"},
        )
    text = repr(logs)
    assert any(e.get("event") == "voicemail_ingested" for e in logs)
    assert "+60129998888" not in text
    assert "8888" in text  # masked tail kept for support


def test_mask_phone() -> None:
    from chatbot.features.chat.phone.voicemail_ingest import mask_phone

    assert mask_phone("+60129998888") == "+60•••8888"
    assert mask_phone(None) == "unknown"
    assert mask_phone("123") == "•••"


def test_voicemail_note_says_playback_needs_a_twilio_login(clock) -> None:
    from chatbot.features.chat.phone.voicemail_ingest import reset_processed_voicemails

    reset_processed_voicemails()
    log = _IvrLog()
    client, _ = _client(log, settings=_agent_settings(phone_voicemail_ingest_enabled=True))
    _post(
        client, "/voice/phone/ivr/voicemail", {"lang": "en", "cat": "inquiry", "reason": "no_answer"},
        {"RecordingSid": "RE91", "RecordingUrl": "https://api.twilio.com/RE91", "RecordingDuration": "4"},
    )
    assert "https://api.twilio.com/RE91.mp3 (playing the recording needs a Twilio console login)" in log.comments[0][1]


def test_agent_route_opens_the_case_and_passes_conversation_id_to_softphones(clock) -> None:
    log = _IvrLog()
    client, _ = _client_with_softphones(log, _agent_settings(), ["agent-3", "agent-5"])
    xml = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "2"}).text)
    assert log.ensured and log.ensured[0][0] == "phone-CA1"
    assert log.ensured[0][2] == "+60129998888"
    assert "Inquiry" in log.ensured[0][1]
    assert xml.count('<Parameter name="conversation_id" value="9"/>') == 2
    # The stage-2 redial carries it too.
    busy = _text(
        _post(
            client, "/voice/phone/ivr/ring", {"lang": "en", "cat": "inquiry", "stage": "1"},
            {"DialCallStatus": "no-answer", "CallStatus": "in-progress"},
        ).text
    )
    assert '<Parameter name="conversation_id" value="9"/>' in busy


def test_rsa_number_route_opens_the_case(clock) -> None:
    log = _IvrLog()
    client, _ = _client(log, settings=_settings(phone_ivr_rsa_number=_RSA))
    xml = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "1"}).text)
    assert f"<Number>{_RSA}</Number>" in xml
    assert log.ensured and log.ensured[0][0] == "phone-CA1"
    assert "Roadside Assistance" in log.ensured[0][1]


class _BrokenLog(_IvrLog):
    async def ensure_conversation_ticket(self, *a: Any, **k: Any) -> str:
        raise RuntimeError("chatwoot down")


def test_case_creation_failure_still_dials(clock) -> None:
    client, _ = _client_with_softphones(_BrokenLog(), _agent_settings(), ["agent-3"])
    xml = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "3"}).text)
    assert "<Client><Identity>agent-3</Identity>" in xml
    assert "conversation_id" not in xml
    client, _ = _client(_BrokenLog(), settings=_settings(phone_ivr_rsa_number=_RSA))
    xml = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "1"}).text)
    assert f"<Number>{_RSA}</Number>" in xml


def test_ai_route_opens_no_case_at_route_time(clock) -> None:
    log = _IvrLog()
    client, _ = _client(log, settings=_settings())
    _post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "2"})
    assert log.ensured == []  # the AI bridge opens its own, as today


def test_menu_off_with_after_hours_and_voicemail_on_leaves_incoming_unchanged(clock) -> None:
    clock["now"] = _SUN_2000_KL
    settings = Settings(
        _env_file=None,
        twilio_webhook_base_url=_BASE,
        twilio_auth_token=_TOKEN,
        phone_after_hours_enabled=True,
        phone_voicemail_ingest_enabled=True,
    )
    client, _ = _client(_IvrLog(), settings=settings)
    res = client.post("/voice/phone/incoming")
    assert res.text == connect_stream_twiml("wss://example.ngrok.app/voice/phone/stream", None)
    assert client.post("/voice/phone/ivr/voicemail").status_code == 404


@pytest.mark.parametrize("call_status", ["completed", "canceled"])
def test_ring_stage_1_does_not_redial_a_caller_who_hung_up(clock, call_status: str) -> None:
    recorded: list[tuple[str, str]] = []

    class _Store:
        async def record(self, call_sid: str, status: str, at: Any = None) -> None:
            recorded.append((call_sid, status))

    client, _ = _client(_IvrLog(), settings=_agent_settings(), abandon_store=_Store())
    xml = _post(
        client, "/voice/phone/ivr/ring", {"lang": "en", "cat": "inquiry", "stage": "1"},
        {"DialCallStatus": "no-answer", "CallStatus": call_status},
    ).text
    assert "<Dial" not in xml and "<Hangup/>" in xml
    assert recorded == [("CA1", "canceled")]


def test_holiday_list_without_this_year_warns() -> None:
    from structlog.testing import capture_logs

    from chatbot.features.chat.phone.ivr import within_office_hours

    with capture_logs() as logs:
        within_office_hours(Settings(_env_file=None), datetime(2027, 3, 2, 2, 0, tzinfo=UTC))
    assert any(e.get("event") == "phone_ivr_public_holidays_missing_year" for e in logs)
    with capture_logs() as logs:
        within_office_hours(Settings(_env_file=None), datetime(2026, 3, 3, 2, 0, tzinfo=UTC))
    assert not any(e.get("event") == "phone_ivr_public_holidays_missing_year" for e in logs)


# --- review round 2 -----------------------------------------------------------


class _SoftFailLog(_IvrLog):
    """ChatwootConversationLog's soft failure: a failed create returns the
    session id itself instead of a conversation id."""

    def __init__(self, returned: Any) -> None:
        super().__init__()
        self.returned = returned

    async def ensure_conversation_ticket(
        self, session_id: str, subject: str, customer_name: str | None, customer_phone: str | None
    ) -> Any:
        self.ensured.append((session_id, subject, customer_phone))
        return session_id if self.returned == "SESSION" else self.returned


@pytest.mark.parametrize("returned", ["SESSION", None, "", "not-a-number"])
def test_soft_failed_case_open_dials_without_a_conversation_id(clock, returned) -> None:
    log = _SoftFailLog(returned)
    client, _ = _client_with_softphones(log, _agent_settings(), ["agent-3"])
    xml = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "2"}).text)
    assert log.ensured  # it was attempted
    assert "<Client><Identity>agent-3</Identity>" in xml
    assert "conversation_id" not in xml


class _SlowLog(_IvrLog):
    async def ensure_conversation_ticket(self, *a: Any, **k: Any) -> str:
        import asyncio

        await asyncio.sleep(5)
        return "9"


def test_slow_case_open_is_bounded_and_still_dials(clock) -> None:
    import time

    client, _ = _client_with_softphones(
        _SlowLog(), _agent_settings(phone_ivr_case_open_timeout_seconds=0.2), ["agent-3"]
    )
    started = time.monotonic()
    xml = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "3"}).text)
    assert time.monotonic() - started < 2
    assert "<Client><Identity>agent-3</Identity>" in xml and "conversation_id" not in xml
    client, _ = _client(_SlowLog(), settings=_settings(phone_ivr_rsa_number=_RSA, phone_ivr_case_open_timeout_seconds=0.2))
    started = time.monotonic()
    xml = _text(_post(client, "/voice/phone/ivr/menu", {"lang": "en", "attempt": 0}, {"Digits": "1"}).text)
    assert time.monotonic() - started < 2
    assert f"<Number>{_RSA}</Number>" in xml


def test_case_open_timeout_defaults_to_3s() -> None:
    assert Settings(_env_file=None).phone_ivr_case_open_timeout_seconds == 3.0
