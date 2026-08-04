"""Pure TwiML builders for the phone channel."""

from __future__ import annotations

from collections.abc import Sequence
from xml.sax.saxutils import escape, quoteattr

# Review fix (Minor, round 2): `language=` alone does not select a Malay
# voice -- Twilio's default engine (Amazon Polly) has no Malay voice at
# all, so `language="ms-MY"` with no explicit `voice=` still speaks in
# English (or is rejected outright). Reused verbatim from this repo's
# OTHER Twilio voice surface, the standalone Studio-Flow IVR
# (`deploy/twilio/README.md`: "Malay prompts use `Google.ms-MY-Standard-A`
# (female); English prompts use `Google.en-US-Standard-C` (female). Amazon
# Polly has no Malay voice, which is why Google TTS is used.") -- these
# are the SAME two Google voice ids `deploy/twilio/ivr-studio-flow.json`
# already uses, not invented here.
GOOGLE_VOICE_EN_US = "Google.en-US-Standard-C"
GOOGLE_VOICE_MS_MY = "Google.ms-MY-Standard-A"


def connect_stream_twiml(wss_url: str, announcement: str | None = None) -> str:
    """TwiML that bridges the call into a bidirectional Media Stream,
    optionally preceded by a spoken announcement.

    Package C Task 6 review fix (carried from Task 5): Malaysia's PDPA
    requires the recorded-line notice be spoken BEFORE recording can
    start. A `<Say>` here runs deterministically before `<Connect><Stream>`
    -- and therefore before Twilio ever opens the Media Stream whose
    "start" event is what triggers `PhoneBridge`/`CallControl.
    start_recording` -- unlike the best-effort in-session text hint in
    `bridge.py`'s `_maybe_start_recording`, which only asks the live model
    to say the notice and cannot prove it ran first. That text hint is
    left in place as a secondary reinforcement (some models may not act on
    a `<Say>` alone, e.g. if the caller talks over it) -- this does not
    replace it, it just makes the notice's precedence provable at the
    TwiML level instead of merely "best effort".

    `announcement=None` (the default) is BYTE-IDENTICAL to this
    function's shape before this parameter existed.

    Review minor: this `<Say>` carries no `language` attribute, so a
    bilingual (EN + Bahasa Melayu) operator-authored announcement is read
    entirely in Twilio's default English TTS voice -- mispronouncing the
    Bahasa Melayu portion. Left as a single free-text field (not split
    into per-language segments like `fallback_twiml` below) because
    `phone_recording_announcement` is operator-authored prose of unknown
    internal structure; there is no reliable way to split arbitrary
    operator text into language segments here without a config shape
    change this fix did not attempt. Tracked as a known limitation, not
    silently accepted -- see `.env.example`'s `PHONE_RECORDING_ANNOUNCEMENT`
    comment.
    """
    say = f"<Say>{escape(announcement)}</Say>" if announcement else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response>{say}<Connect>"
        f"<Stream url={quoteattr(wss_url)} />"
        "</Connect></Response>"
    )


def fallback_twiml(segments: Sequence[tuple[str, str, str]]) -> str:
    """TwiML spoken on a live call after a transfer attempt could not be
    completed (no answer / busy / failed -- see
    `/webhooks/phone/dial-status`): say each `(text, language, voice)`
    triple as its own `<Say language=... voice=...>` verb, in order, then
    hang up.

    Review minor fix, round 2: `language=` alone doesn't select a Malay
    voice (see `GOOGLE_VOICE_MS_MY`'s module docstring above) -- BOTH
    attributes are required for the apology to actually be intelligible,
    not just `language=`.

    Unlike `connect_stream_twiml`'s operator-authored free text, callers
    of this function control their own text (today, just the hard-coded
    bilingual unanswered-handoff apology in `router.py`), so it CAN split
    -- and does -- each language into its own correctly-voiced `<Say>`
    rather than reading a mixed-language blob in one voice.
    """
    says = "".join(
        f"<Say language={quoteattr(lang)} voice={quoteattr(voice)}>{escape(text)}</Say>"
        for text, lang, voice in segments
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{says}<Hangup/></Response>'
