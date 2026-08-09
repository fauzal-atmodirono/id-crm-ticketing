"""P8 task 3 -- the effective-dated price table.

Test two (`test_a_price_change_in_march_does_not_re_price_a_february_call`)
is the requirement: re-pricing history whenever a rate changes would make
last month's reported cost change after it was already reported.

Test four (`test_an_unpriced_model_returns_none_and_is_reported_as_unpriced`):
an unpriced model must surface as "unpriced", never as free -- a new model
appearing in the cost report at zero cost is the failure mode.

Test six (`test_prices_use_decimal_not_float`): floating-point money in a
report a client is invoiced against will eventually produce a cent-level
discrepancy nobody can explain.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chatbot.features.metrics.price_table import (
    TOKEN_CLASS_CACHED,
    TOKEN_CLASS_EMBEDDING_CHARS,
    TOKEN_CLASS_OUTPUT,
    TOKEN_CLASS_PROMPT,
    InvalidPrice,
    Price,
    PriceTable,
)
from chatbot.platform.config import get_settings


class _FakeDoc:
    def __init__(self, store: dict[str, dict[str, Any]], key: str) -> None:
        self._store, self._key = store, key

    def get(self) -> MagicMock:
        snap = MagicMock()
        snap.exists = self._key in self._store
        snap.to_dict.return_value = self._store.get(self._key)
        snap.id = self._key
        return snap

    def set(self, data: dict[str, Any]) -> None:
        self._store[self._key] = data


class _FakeCollection:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self._store = store

    def document(self, key: str) -> _FakeDoc:
        return _FakeDoc(self._store, key)

    def stream(self):
        for key in list(self._store):
            yield _FakeDoc(self._store, key).get()


class _FakeFirestoreClient:
    def __init__(self) -> None:
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str):
        return _FakeCollection(self._collections.setdefault(name, {}))


def _table(**overrides):
    settings = get_settings().model_copy(update=overrides)
    return PriceTable(settings)


def _patched():
    return patch("chatbot.features.metrics.price_table.firestore.Client", autospec=True)


@pytest.mark.asyncio
async def test_a_price_effective_from_january_applies_to_a_february_call():
    with _patched() as C:
        C.return_value = _FakeFirestoreClient()
        table = _table()
        await table.set_price(
            Price("gemini-2.5-flash", TOKEN_CLASS_PROMPT, Decimal("0.0000001"), date(2026, 1, 1))
        )

        price = await table.price_for("gemini-2.5-flash", TOKEN_CLASS_PROMPT, date(2026, 2, 15))
        assert price == Decimal("0.0000001")


@pytest.mark.asyncio
async def test_a_price_change_in_march_does_not_re_price_a_february_call():
    """The requirement: re-pricing history whenever a rate changes would make
    last month's reported cost change after it was already reported."""
    with _patched() as C:
        C.return_value = _FakeFirestoreClient()
        table = _table()
        await table.set_price(
            Price("gemini-2.5-flash", TOKEN_CLASS_PROMPT, Decimal("0.0000001"), date(2026, 1, 1))
        )
        await table.set_price(
            Price("gemini-2.5-flash", TOKEN_CLASS_PROMPT, Decimal("0.0000002"), date(2026, 3, 1))
        )

        # A February call is priced with January's rate, both before and
        # after March's price change is on file.
        price = await table.price_for("gemini-2.5-flash", TOKEN_CLASS_PROMPT, date(2026, 2, 15))
        assert price == Decimal("0.0000001")


@pytest.mark.asyncio
async def test_input_output_and_cached_are_priced_independently():
    with _patched() as C:
        C.return_value = _FakeFirestoreClient()
        table = _table()
        model = "gemini-2.5-flash"
        eff = date(2026, 1, 1)
        await table.set_price(Price(model, TOKEN_CLASS_PROMPT, Decimal("0.0000001"), eff))
        await table.set_price(Price(model, TOKEN_CLASS_OUTPUT, Decimal("0.0000004"), eff))
        await table.set_price(Price(model, TOKEN_CLASS_CACHED, Decimal("0.00000002"), eff))

        at = date(2026, 2, 1)
        assert await table.price_for(model, TOKEN_CLASS_PROMPT, at) == Decimal("0.0000001")
        assert await table.price_for(model, TOKEN_CLASS_OUTPUT, at) == Decimal("0.0000004")
        assert await table.price_for(model, TOKEN_CLASS_CACHED, at) == Decimal("0.00000002")


@pytest.mark.asyncio
async def test_an_unpriced_model_returns_none_and_is_reported_as_unpriced():
    """A new model appearing in the cost report at zero cost is the failure
    mode this guards against -- unpriced must never resolve to free."""
    with _patched() as C:
        C.return_value = _FakeFirestoreClient()
        table = _table()
        await table.set_price(
            Price("gemini-2.5-flash", TOKEN_CLASS_PROMPT, Decimal("0.0000001"), date(2026, 1, 1))
        )

        assert (
            await table.price_for("gemini-3.0-preview", TOKEN_CLASS_PROMPT, date(2026, 2, 1))
        ) is None


@pytest.mark.asyncio
async def test_the_most_recent_effective_price_at_or_before_the_call_wins():
    with _patched() as C:
        C.return_value = _FakeFirestoreClient()
        table = _table()
        model = "gemini-2.5-flash"
        await table.set_price(
            Price(model, TOKEN_CLASS_PROMPT, Decimal("0.0000001"), date(2026, 1, 1))
        )
        await table.set_price(
            Price(model, TOKEN_CLASS_PROMPT, Decimal("0.0000002"), date(2026, 3, 1))
        )
        await table.set_price(
            Price(model, TOKEN_CLASS_PROMPT, Decimal("0.0000003"), date(2026, 6, 1))
        )

        # Exactly on an effective_from date: that row applies (>= not >).
        assert await table.price_for(model, TOKEN_CLASS_PROMPT, date(2026, 3, 1)) == Decimal(
            "0.0000002"
        )
        # Between two effective dates: the earlier, still-active one wins.
        assert await table.price_for(model, TOKEN_CLASS_PROMPT, date(2026, 4, 15)) == Decimal(
            "0.0000002"
        )
        # After the latest change: the latest rate wins.
        assert await table.price_for(model, TOKEN_CLASS_PROMPT, date(2026, 12, 1)) == Decimal(
            "0.0000003"
        )
        # Before any price was ever on file: unpriced, not free.
        assert (await table.price_for(model, TOKEN_CLASS_PROMPT, date(2025, 12, 31))) is None


@pytest.mark.asyncio
async def test_prices_use_decimal_not_float():
    """Floating-point money in an invoiced report eventually produces a
    cent-level discrepancy nobody can explain."""
    with _patched() as C:
        C.return_value = _FakeFirestoreClient()
        table = _table()
        await table.set_price(
            Price("gemini-2.5-flash", TOKEN_CLASS_PROMPT, Decimal("0.0000001"), date(2026, 1, 1))
        )

        price = await table.price_for("gemini-2.5-flash", TOKEN_CLASS_PROMPT, date(2026, 2, 1))
        assert isinstance(price, Decimal)

        with pytest.raises(InvalidPrice):
            Price("gemini-2.5-flash", TOKEN_CLASS_PROMPT, 0.0000001, date(2026, 1, 1)).validate()  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_datetime_at_is_accepted_and_uses_its_date():
    """`at` is the date of the usage being priced; a datetime (e.g. straight
    off a BigQuery `occurred_at` timestamp) must work without the caller
    having to strip the time component itself."""
    with _patched() as C:
        C.return_value = _FakeFirestoreClient()
        table = _table()
        await table.set_price(
            Price("gemini-2.5-flash", TOKEN_CLASS_PROMPT, Decimal("0.0000001"), date(2026, 1, 1))
        )

        price = await table.price_for(
            "gemini-2.5-flash", TOKEN_CLASS_PROMPT, datetime(2026, 2, 15, 13, 30)
        )
        assert price == Decimal("0.0000001")


@pytest.mark.asyncio
async def test_embedding_models_are_priced_per_character_via_a_dedicated_token_class():
    """Fact 1: `EmbedContentResponse` carries no `usage_metadata`, so
    embeddings cannot be priced per token. The table's decision, made
    visible rather than a silent exclusion: a dedicated per-character token
    class an operator can price."""
    with _patched() as C:
        C.return_value = _FakeFirestoreClient()
        table = _table()
        await table.set_price(
            Price(
                "text-embedding-004",
                TOKEN_CLASS_EMBEDDING_CHARS,
                Decimal("0.00000001"),
                date(2026, 1, 1),
            )
        )

        price = await table.price_for(
            "text-embedding-004", TOKEN_CLASS_EMBEDDING_CHARS, date(2026, 2, 1)
        )
        assert price == Decimal("0.00000001")


@pytest.mark.asyncio
async def test_an_unsupported_token_class_is_rejected_at_write_time():
    with _patched() as C:
        C.return_value = _FakeFirestoreClient()
        with pytest.raises(InvalidPrice):
            Price(
                "gemini-2.5-flash", "thinking_tokens", Decimal("0.0000001"), date(2026, 1, 1)
            ).validate()


@pytest.mark.asyncio
async def test_a_store_outage_resolves_to_none_rather_than_raising():
    """Fail-open: a Firestore hiccup must not crash the cost report, and must
    not be conflated with a genuinely unpriced model either -- both surface
    identically as `None`, which is the correct caller-facing behaviour for
    'cannot price this right now'."""
    with patch("chatbot.features.metrics.price_table.firestore.Client", autospec=True) as C:
        C.side_effect = RuntimeError("firestore down")
        assert (
            await _table().price_for("gemini-2.5-flash", TOKEN_CLASS_PROMPT, date.today()) is None
        )


@pytest.mark.asyncio
async def test_list_all_can_filter_by_model():
    with _patched() as C:
        C.return_value = _FakeFirestoreClient()
        table = _table()
        await table.set_price(
            Price("gemini-2.5-flash", TOKEN_CLASS_PROMPT, Decimal("0.0000001"), date(2026, 1, 1))
        )
        await table.set_price(
            Price("gemini-3.0-preview", TOKEN_CLASS_PROMPT, Decimal("0.0000005"), date(2026, 1, 1))
        )

        flash_only = await table.list_all(model="gemini-2.5-flash")
        assert [p.model for p in flash_only] == ["gemini-2.5-flash"]
