"""Task 31 (FIX-31, FIX-56 step 7): the Proton SOP's DTMF IVR as pure TwiML.

Gated by `PHONE_DTMF_MENU_ENABLED`. With it off nothing here runs and
`/voice/phone/incoming` returns exactly what it returned before. With it on, the
inbound webhook answers with the SOP's menus instead of going straight into the
AI voice bridge:

    welcome + language   (1 English / 2 Bahasa Melayu)
      -> category menu   (1 Roadside Assistance / 2 Inquiry / 3 Complaint / 0 repeat)
           1 RSA        -> 24/7: dial PHONE_IVR_RSA_NUMBER, or the AI bridge
                           when none is set (or when the RSA line rings out)
           2, 3 non-RSA -> office-hour check (PHONE_AFTER_HOURS_ENABLED)
                             out of hours -> bilingual after-hours message,
                                             then voicemail (PHONE_VOICEMAIL_
                                             INGEST_ENABLED) or goodbye
                             in hours     -> PHONE_IVR_NON_RSA_ROUTE:
                                             "ai"    the AI voice bridge (today's path)
                                             "agent" ring agents 10 s, queue-busy
                                                     prompt, ring 10 s more (agent
                                                     attends within 20 s), then
                                                     voicemail or apology

This module only builds strings and makes decisions. Request parsing,
signature checks and side effects live in `ivr_router.py`; every word spoken
lives in `ivr_prompts.py`.

**A caller can never be trapped.** A timeout or an invalid key repeats the menu
once; a second one routes onwards (language -> English category menu is
skipped and the call goes to the non-RSA route, which is an agent or the AI).
`0` repeats on request. Every `<Gather>` is followed by a `<Redirect>` to its own
action, because Twilio does NOT post the action on a silent timeout -- it falls
through to the next verb -- and the redirect is how a timeout reaches the
decision code at all.

**Why the queue-busy prompt is a second `<Dial>`.** The SOP wants the busy
prompt after 10 s of ringing (B28) and an agent on the line within 20 s (B32).
TwiML cannot speak over a ringing `<Dial>`, so the ring stage is split: ring
for `PHONE_IVR_QUEUE_BUSY_AFTER_SECONDS`, and if nobody answered, `<Say>` the
busy prompt and ring again for the rest of `PHONE_IVR_AGENT_ATTEND_SECONDS`.
The caller hears ringing, the prompt, ringing -- which is what "please stay on
the line" promises.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape, quoteattr
from zoneinfo import ZoneInfo

import structlog

from chatbot.features.chat.phone import ivr_prompts as p

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

LANGUAGES = ("en", "ms")
CATEGORIES = ("rsa", "inquiry", "complaint", "unselected")
_LANGUAGE_CODE = {"en": "en-US", "ms": "ms-MY"}

PATH_LANGUAGE = "/voice/phone/ivr/language"
PATH_MENU = "/voice/phone/ivr/menu"
PATH_RING = "/voice/phone/ivr/ring"
PATH_VOICEMAIL = "/voice/phone/ivr/voicemail"
IVR_PATHS = (PATH_LANGUAGE, PATH_MENU, PATH_RING, PATH_VOICEMAIL)

_XML = '<?xml version="1.0" encoding="UTF-8"?>'
HANGUP_TWIML = f"{_XML}<Response><Hangup/></Response>"
_MIN_RING_SECONDS = 5
_SATURDAY = 5  # date.weekday(): Monday 0 .. Sunday 6


# --- speech -----------------------------------------------------------------


def _voice(settings: Settings, lang: str) -> str:
    return settings.phone_tts_voice_ms if lang == "ms" else settings.phone_tts_voice_en


def say(settings: Settings, lang: str, text: str) -> str:
    """One `<Say>` in the language's own voice. Both attributes are needed:
    `language=` alone does not pick a Malay voice (see twiml.py)."""
    return (
        f"<Say language={quoteattr(_LANGUAGE_CODE[lang])} "
        f"voice={quoteattr(_voice(settings, lang))}>{escape(text)}</Say>"
    )


def _other(lang: str) -> str:
    return "en" if lang == "ms" else "ms"


# --- menus ------------------------------------------------------------------


def _gather(settings: Settings, action: str, says: str) -> str:
    return (
        f'<Gather input="dtmf" numDigits="1" '
        f'timeout="{int(settings.phone_ivr_gather_timeout_seconds)}" '
        f'action={quoteattr(action)} method="POST">{says}</Gather>'
        f'<Redirect method="POST">{escape(action)}</Redirect>'
    )


def language_menu_twiml(settings: Settings, base: str, attempt: int = 0) -> str:
    """The SOP's START shape: bilingual welcome, then the language choice.
    Each half in its own voice."""
    action = f"{base}{PATH_LANGUAGE}?attempt={int(attempt)}"
    says = (
        say(settings, "en", p.WELCOME_EN)
        + say(settings, "ms", p.WELCOME_MS)
        + say(settings, "en", p.LANGUAGE_CHOICE_EN)
        + say(settings, "ms", p.LANGUAGE_CHOICE_MS)
    )
    return f"{_XML}<Response>{_gather(settings, action, says)}</Response>"


def category_menu_twiml(settings: Settings, base: str, lang: str, attempt: int = 0) -> str:
    action = f"{base}{PATH_MENU}?lang={lang}&attempt={int(attempt)}"
    says = say(settings, lang, p.PROMPTS[lang]["menu"])
    return f"{_XML}<Response>{_gather(settings, action, says)}</Response>"


def decide_language(digit: str | None, attempt: int) -> tuple[str, str | int]:
    """`("menu", lang)`, `("repeat", next_attempt)` or `("route", "en")`.

    "route" means the caller twice pressed nothing usable: they go to the
    non-RSA route in English rather than hearing the welcome a third time.
    """
    if digit == "1":
        return "menu", "en"
    if digit == "2":
        return "menu", "ms"
    if attempt < 1:
        return "repeat", attempt + 1
    return "route", "en"


def decide_category(digit: str | None, attempt: int) -> tuple[str, str | int]:
    """`("route", category)`, `("repeat", attempt)` for an explicit 0 (does
    not count against the caller), `("repeat", attempt + 1)` for a first
    timeout/invalid key, and `("route", "unselected")` for a second one."""
    if digit == "1":
        return "route", "rsa"
    if digit == "2":
        return "route", "inquiry"
    if digit == "3":
        return "route", "complaint"
    if digit == "0":
        return "repeat", attempt
    if attempt < 1:
        return "repeat", attempt + 1
    return "route", "unselected"


# --- office hours -----------------------------------------------------------


def _parse_window(window: str) -> tuple[time, time]:
    start, end = (part.strip() for part in window.split("-", 1))
    return time.fromisoformat(start), time.fromisoformat(end)


def public_holidays(settings: Settings) -> frozenset[date]:
    out: set[date] = set()
    for item in settings.phone_ivr_public_holidays.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            out.add(date.fromisoformat(value))
        except ValueError:
            _log.warning("phone_ivr_public_holiday_unparseable", value=value)
    return frozenset(out)


def within_office_hours(settings: Settings, now: datetime) -> bool:
    """SOP office hours in `PHONE_IVR_TIMEZONE`: Mon-Fri the weekday window,
    Sat/Sun and listed public holidays the weekend window. Start inclusive,
    end exclusive (17:30:00 is closed).

    Fails OPEN (in hours) on a malformed setting, logged: a caller wrongly
    told "we are closed" during business hours is the worse failure, and the
    after-hours branch has its own default-off flag to fall back on.
    """
    try:
        local = now.astimezone(ZoneInfo(settings.phone_ivr_timezone))
        holidays = public_holidays(settings)
        if not any(d.year == local.year for d in holidays):
            # A stale list silently treats every holiday as a working day.
            _log.warning("phone_ivr_public_holidays_missing_year", year=local.year)
        weekend = local.weekday() >= _SATURDAY or local.date() in holidays
        window = settings.phone_ivr_weekend_hours if weekend else settings.phone_ivr_weekday_hours
        start, end = _parse_window(window)
    except Exception as e:
        _log.warning("phone_ivr_office_hours_misconfigured", error=str(e))
        return True
    return start <= local.time().replace(tzinfo=None) < end


# --- ring stage, after hours, voicemail -------------------------------------


def ring_url(base: str, lang: str, cat: str, stage: str) -> str:
    return f"{base}{PATH_RING}?lang={lang}&cat={cat}&stage={stage}"


def voicemail_url(base: str, lang: str, cat: str, reason: str) -> str:
    return f"{base}{PATH_VOICEMAIL}?lang={lang}&cat={cat}&reason={reason}"


def dial_twiml(
    nouns: str, action: str, timeout: int, caller_id: str = "", prefix: str = ""
) -> str:
    """`<Dial>` with pre-rendered nouns (`<Number>`/`<Client>`), optionally
    preceded by spoken TwiML (`prefix`, the queue-busy prompt)."""
    # callerId only for a PSTN leg (same rule as handoff_target.dial_twiml):
    # a <Client> leg does not need one.
    caller_id_attr = f" callerId={quoteattr(caller_id)}" if caller_id and "<Number>" in nouns else ""
    return (
        f"{_XML}<Response>{prefix}"
        f'<Dial action={quoteattr(action)} method="POST" timeout="{int(timeout)}"'
        f"{caller_id_attr}>{nouns}</Dial></Response>"
    )


def second_ring_seconds(settings: Settings) -> int:
    """What is left of the 20 s attend window after the busy prompt's 10 s,
    never less than 5 s (a 0 s `<Dial>` would fail immediately)."""
    rest = settings.phone_ivr_agent_attend_seconds - settings.phone_ivr_queue_busy_after_seconds
    return max(_MIN_RING_SECONDS, int(rest))


def voicemail_twiml(settings: Settings, base: str, lang: str, cat: str, reason: str) -> str:
    """Voicemail prompt and `<Record>`. Twilio posts `action` when the caller
    finishes (or hangs up mid-message), with From/CallSid/RecordingUrl/
    RecordingSid -- which is what `ivr_router` turns into a Chatwoot case."""
    action = voicemail_url(base, lang, cat, reason)
    return (
        f"{_XML}<Response>{say(settings, lang, p.PROMPTS[lang]['voicemail'])}"
        f"<Record action={quoteattr(action)} method=\"POST\" "
        f'maxLength="{int(settings.phone_ivr_voicemail_max_seconds)}" '
        f'timeout="5" finishOnKey="#" playBeep="true"/>'
        f"{say(settings, lang, p.PROMPTS[lang]['goodbye'])}<Hangup/></Response>"
    )


def after_hours_twiml(settings: Settings, base: str, lang: str, cat: str) -> str:
    """The SOP's after-hours message in both languages (the caller's first),
    then voicemail when ingest is on, else goodbye. With ingest off nothing
    would ever read a recording, so none is taken -- see example.env, which
    says to switch the two flags on together."""
    message = say(settings, lang, p.PROMPTS[lang]["after_hours"]) + say(
        settings, _other(lang), p.PROMPTS[_other(lang)]["after_hours"]
    )
    if settings.phone_voicemail_ingest_enabled:
        record = voicemail_twiml(settings, base, lang, cat, "after_hours")
        return record.replace("<Response>", f"<Response>{message}", 1)
    return (
        f"{_XML}<Response>{message}"
        f"{say(settings, lang, p.PROMPTS[lang]['goodbye'])}<Hangup/></Response>"
    )


def no_agent_twiml(settings: Settings, base: str, lang: str, cat: str) -> str:
    """The ring stage ended with nobody answering."""
    if settings.phone_voicemail_ingest_enabled:
        return voicemail_twiml(settings, base, lang, cat, "no_answer")
    return f"{_XML}<Response>{say(settings, lang, p.PROMPTS[lang]['no_agent'])}<Hangup/></Response>"


def goodbye_twiml(settings: Settings, lang: str) -> str:
    return f"{_XML}<Response>{say(settings, lang, p.PROMPTS[lang]['goodbye'])}<Hangup/></Response>"


def number_noun(number: str) -> str:
    return f"<Number>{escape(number)}</Number>"


def client_nouns(identities: list[str], parameters: dict[str, str] | None = None) -> str:
    params = "".join(
        f"<Parameter name={quoteattr(k)} value={quoteattr(v)}/>" for k, v in (parameters or {}).items()
    )
    return "".join(
        f"<Client><Identity>{escape(i)}</Identity>{params}</Client>" for i in identities if i
    )
