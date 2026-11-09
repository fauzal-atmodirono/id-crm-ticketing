"""The post-call transcriber's Proton glossary and language instruction.

A general model hearing "e-mas seven" over an 8 kHz line writes "a mass
seven"; hearing a Petaling Jaya dealer it invents a town. These tests pin
that the prompt names the vocabulary, the four languages, the noise rule and
the [inaudible] rule -- and that dealer names come from the DealerStore when
one is configured, never at the cost of failing the transcription.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from chatbot.features.chat.phone import recording_glossary
from chatbot.features.chat.phone.recording_glossary import (
    PROTON_GLOSSARY,
    build_transcribe_prompt,
    configure_dealer_source,
    dealer_display_name,
    glossary_terms,
)


@pytest.fixture(autouse=True)
def _reset_source():
    configure_dealer_source(None)
    yield
    configure_dealer_source(None)


class _Rec:
    def __init__(self, dealer: str) -> None:
        self.dealer = dealer


class _Store:
    def __init__(self, slugs: list[str]) -> None:
        self.slugs = slugs
        self.calls = 0

    async def list_all(self):
        self.calls += 1
        return [_Rec(s) for s in self.slugs]


class _BrokenStore:
    async def list_all(self):
        raise RuntimeError("firestore down")


def test_the_static_glossary_names_the_proton_vocabulary() -> None:
    for term in (
        "e.MAS",
        "e.MAS 7",
        "X50",
        "X70",
        "X90",
        "S70",
        "Saga",
        "Persona",
        "Iriz",
        "Exora",
        "Petaling Jaya",
        "PRO-NET",
        "RSA",
    ):
        assert term in PROTON_GLOSSARY


def test_the_prompt_carries_the_glossary_and_the_language_rules() -> None:
    prompt = build_transcribe_prompt(PROTON_GLOSSARY)
    for term in ("e.MAS 7", "X70", "Exora", "PRO-NET", "Petaling Jaya"):
        assert term in prompt
    for language in ("Malay", "English", "Mandarin", "Tamil"):
        assert language in prompt
    assert "code-switching is normal" in prompt
    assert "never output other scripts for noise" in prompt
    assert "[inaudible]" in prompt
    # The labels the CRM note readers already rely on.
    assert "CUSTOMER:" in prompt and "AGENT:" in prompt


def test_dealer_slugs_become_spoken_names() -> None:
    assert dealer_display_name("petaling_jaya") == "Petaling Jaya"
    assert dealer_display_name("dealer_shah-alam") == "Shah Alam"
    assert dealer_display_name("  ") == ""


async def test_without_a_dealer_source_the_terms_are_the_static_glossary() -> None:
    assert await glossary_terms() == list(PROTON_GLOSSARY)


async def test_dealer_names_are_added_from_the_store_and_deduplicated() -> None:
    store = _Store(["petaling_jaya", "bukit_jalil", "klang"])
    configure_dealer_source(store)
    terms = await glossary_terms()
    assert "Bukit Jalil" in terms and "Klang" in terms
    # Petaling Jaya is already static; it must not appear twice.
    assert terms.count("Petaling Jaya") == 1


async def test_dealer_names_are_cached_between_calls() -> None:
    store = _Store(["klang"])
    configure_dealer_source(store)
    await glossary_terms()
    await glossary_terms()
    assert store.calls == 1


async def test_a_broken_dealer_store_falls_back_to_the_static_glossary() -> None:
    configure_dealer_source(_BrokenStore())
    assert await glossary_terms() == list(PROTON_GLOSSARY)


async def test_the_post_call_transcriber_sends_the_glossary_prompt() -> None:
    """process_recording is the path every finished call takes; the prompt it
    hands the model must be the glossary one, including store dealers."""
    from chatbot.features.chat.phone.recording_transcriber import process_recording
    from chatbot.platform.config import get_settings

    configure_dealer_source(_Store(["bukit_jalil"]))
    settings = get_settings().model_copy(
        update={
            "phone_recording_transcription_enabled": True,
            "twilio_account_sid": "AC" + "0" * 32,
            "twilio_auth_token": "tok",
        }
    )
    seen: list[str] = []

    def _fake(_settings, _audio, prompt=None):
        seen.append(prompt or "")
        return "CUSTOMER: hi"

    port = AsyncMock()
    with patch(
        "chatbot.features.chat.phone.recording_transcriber.fetch_recording",
        new=AsyncMock(return_value=b"audio"),
    ), patch(
        "chatbot.features.chat.phone.recording_transcriber._transcribe_sync", new=_fake
    ):
        assert await process_recording(settings, port, "42", "https://x")
    assert seen and "Bukit Jalil" in seen[0] and "e.MAS 7" in seen[0]
    assert "Tamil" in seen[0]


def test_the_cache_window_is_finite() -> None:
    assert 0 < recording_glossary._DEALER_CACHE_SECONDS <= 3600
