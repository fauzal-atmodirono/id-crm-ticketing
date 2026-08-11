"""Unit tests for Phone Number E.164 Normalisation (P12 Task 1)."""

from __future__ import annotations

import pytest

from chatbot.features.chat.phone_number import normalise_phone_number


def test_plus_60123456789_normalises_to_e164() -> None:
    assert normalise_phone_number("+60123456789") == "+60123456789"


def test_0123456789_normalises_to_the_same_e164() -> None:
    assert normalise_phone_number("0123456789") == "+60123456789"


def test_60123456789_normalises_to_the_same_e164() -> None:
    assert normalise_phone_number("60123456789") == "+60123456789"


def test_spaces_and_dashes_are_ignored() -> None:
    assert normalise_phone_number("+60 12-345 6789") == "+60123456789"
    assert normalise_phone_number("012 - 345 - 6789") == "+60123456789"


def test_an_unparseable_string_returns_none_rather_than_a_guess() -> None:
    assert normalise_phone_number("abc") is None
    assert normalise_phone_number("") is None
    assert normalise_phone_number(None) is None
    assert normalise_phone_number("123") is None


def test_a_non_malaysian_number_with_a_country_code_is_preserved() -> None:
    assert normalise_phone_number("+14155552671") == "+14155552671"
    assert normalise_phone_number("+6591234567") == "+6591234567"


def test_normalisation_is_idempotent() -> None:
    first = normalise_phone_number("0123456789")
    second = normalise_phone_number(first)
    assert first == second == "+60123456789"
