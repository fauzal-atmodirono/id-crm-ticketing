"""Effective-dated Gemini pricing, so a token count can become money.

Mirrors `TargetsStore` (`features/metrics/targets_store.py`): one Firestore
collection, `asyncio.to_thread` for I/O, fail-open on every read. The
interface is a single lookup, `price_for(model, token_class, at)`, because
that is the one thing a cost report needs and the one thing that must never
be gotten wrong: which rate applied to a call made on a given date.

**Why effective dating, and why `at` is the call's date, not `now()`.**
Google changes prices. If `price_for` always used the *current* rate, then
running the same cost report twice -- once in February, once again in April
after a March price change -- would print two different figures for the same
February calls. A cost figure for last March must use last March's rate,
permanently. `price_for` therefore takes the date of the usage being priced
and returns the most recent price with `effective_from <= at` -- never the
newest price on file, never `now()`. This is `test_a_price_change_in_march_
does_not_re_price_a_february_call` and it is the point of the task, not an
edge case of it.

**Why an unknown model returns `None`, never a zero price.** Same reasoning
as `TargetsStore`'s "unknown key resolves to `None`, never a zero target": a
`Decimal("0")` for a model nobody has priced yet would make that model's spend
look like the cheapest possible number, produced entirely by an operator
having not yet filled in a price row. A cost report must render that as "not
priced", not "free". Callers should treat `None` accordingly -- never
`rate or Decimal(0)`.

**Four measurement facts from task 2's metering wrapper that this table's
shape answers, made explicit because each one is a way a cost figure can lie
by omission:**

1. **Embeddings have no `usage_metadata` at all** -- `EmbedContentResponse`
   bills per *character* (`metadata.billable_character_count`), not per
   token, so a metered embed call's three `TokenUsage` counts are all `None`
   by construction, never a fabricated `0`. This table's decision, made
   visible rather than silently defaulting to "excluded": embeddings get
   their **own token class**, `TOKEN_CLASS_EMBEDDING_CHARS`, priced per
   character rather than per token, so an operator *can* price them. What
   this does not solve: `token_usage`'s BigQuery schema
   (`features/metrics/token_usage.py::TOKEN_USAGE_SCHEMA`) has no character
   count column, only the three token counts -- so even with a character
   rate on file, Task 4's cost view has no character count to multiply it by
   today. The rate exists so pricing is possible the day a character count is
   captured; until then, the cost view must state embeddings are visible but
   unpriced, not silently price them at 0.
2. **Live API tokens are uncaptured** -- a Live session's usage arrives in
   server messages, never on a response object, so no `TokenUsage` row is
   even produced for `phone.live`. There is nothing for `price_for` to be
   asked about: an uncaptured call has no row to price, and a cost view must
   not treat "no row" as "no spend". This table has no special case for it --
   the fix, if any, is upstream of pricing entirely.
3. **Thinking models bill more than the three captured classes** --
   `thoughts_token_count` and `tool_use_prompt_token_count` are real spend
   that `TokenUsage` never captures (only `prompt_tokens` / `output_tokens` /
   `cached_tokens` exist on that dataclass; `total_token_count` itself is not
   stored). This table's decision: **price only the three classes `TokenUsage`
   actually captures.** Reconciling against `total_token_count` was
   considered and rejected at this layer, because the number to reconcile
   against was never persisted upstream -- there is nothing in
   `token_usage`'s BigQuery rows for a reconciliation query to read. This is
   a real, named gap (a thinking model's true cost is understated), owed to
   whichever task next touches `TokenUsage`'s schema, not something a pricing
   lookup can silently fix by inventing a number it was never given.
4. **`None` means "not captured", never `0`.** This table only stores and
   looks up *rates*; it does not multiply a rate by a token count. That
   multiplication is the caller's (Task 4's) responsibility, and it must
   follow the same rule `token_usage.py` follows: a `None` count must produce
   a `None`/"unmeasured" cost, never a `0` cost from `rate * None -> 0`-style
   coercion. Documented here because a caller of `price_for` is exactly the
   place that rule can be silently violated.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from google.cloud import firestore

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_COLLECTION = "gemini_prices"

# The token classes this table knows how to price. The first three are
# exactly `TokenUsage`'s three captured counts (`features/metrics/
# token_usage.py`) so a price row's `token_class` lines up 1:1 with a
# `TokenUsage` field name for Task 4. The fourth is per-CHARACTER, not
# per-token -- see fact 1 in the module docstring.
TOKEN_CLASS_PROMPT = "prompt_tokens"  # noqa: S105 -- a field name, not a secret
TOKEN_CLASS_OUTPUT = "output_tokens"  # noqa: S105
TOKEN_CLASS_CACHED = "cached_tokens"  # noqa: S105
TOKEN_CLASS_EMBEDDING_CHARS = "embedding_chars"  # noqa: S105

SUPPORTED_TOKEN_CLASSES = frozenset(
    {TOKEN_CLASS_PROMPT, TOKEN_CLASS_OUTPUT, TOKEN_CLASS_CACHED, TOKEN_CLASS_EMBEDDING_CHARS}
)


class InvalidPrice(ValueError):
    """A price row the table refused to store. Message is shown to the operator."""


@dataclass(frozen=True)
class Price:
    """One effective-dated rate.

    `rate` is USD per unit -- per token for the three `TokenUsage` classes,
    per character for `TOKEN_CLASS_EMBEDDING_CHARS`. Always a `Decimal`:
    floating-point money in a report a client is invoiced against eventually
    produces a cent-level discrepancy nobody can explain
    (`test_prices_use_decimal_not_float`).

    `effective_from` is the date this rate starts applying. It is never
    mutated after the fact -- a price *change* is a new `Price` row with a
    later `effective_from`, so history is layered, not overwritten.
    """

    model: str
    token_class: str
    rate: Decimal
    effective_from: date

    def validate(self) -> Price:
        if self.token_class not in SUPPORTED_TOKEN_CLASSES:
            raise InvalidPrice(
                f"token_class must be one of {', '.join(sorted(SUPPORTED_TOKEN_CLASSES))}; "
                f"got {self.token_class!r}."
            )
        if not isinstance(self.rate, Decimal):
            raise InvalidPrice(
                f"rate must be a Decimal (never float) -- got {type(self.rate).__name__}. "
                "Floating-point money in an invoiced cost report eventually produces a "
                "cent-level discrepancy nobody can explain."
            )
        if not isinstance(self.effective_from, date):
            raise InvalidPrice(
                f"effective_from must be a date -- got {type(self.effective_from).__name__}."
            )
        return self


def _doc_id(model: str, token_class: str, effective_from: date) -> str:
    return f"{model}::{token_class}::{effective_from.isoformat()}"


class PriceTable:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> firestore.Client:
        return firestore.Client(
            project=self._settings.firestore_project_id,
            database=self._settings.firestore_database_id,
        )

    async def set_price(self, price: Price) -> None:
        """Add (or replace) one effective-dated rate.

        Writing a `Price` with the same `(model, token_class, effective_from)`
        as an existing one replaces that row -- correcting a typo'd rate for a
        date that has not happened yet. It does not retroactively change which
        rate a *past* `price_for(..., at=<earlier date>)` call resolves to,
        because `price_for` only ever looks at rows with `effective_from <=
        at`; a still-future correction is invisible to a past lookup.
        """
        price.validate()
        doc = (
            self._client()
            .collection(_COLLECTION)
            .document(_doc_id(price.model, price.token_class, price.effective_from))
        )
        data = asdict(price)
        data["rate"] = str(price.rate)  # Decimal isn't natively Firestore-serialisable
        data["effective_from"] = price.effective_from.isoformat()
        try:
            await asyncio.to_thread(doc.set, data)
        except Exception as e:
            _log.error(
                "price_table_set_failed",
                model=price.model,
                token_class=price.token_class,
                error=str(e),
            )

    async def price_for(self, model: str, token_class: str, at: date | datetime) -> Decimal | None:
        """The rate that applied to a call made on `at`.

        `at` must be the date of the *usage being priced*, never `now()` --
        see the module docstring. Resolution rule: among all rows for this
        `(model, token_class)` with `effective_from <= at`, the one with the
        latest `effective_from` wins (`test_the_most_recent_effective_price_
        at_or_before_the_call_wins`). A row whose `effective_from` is after
        `at` is invisible to this lookup, which is what makes a future price
        change unable to re-price a past call
        (`test_a_price_change_in_march_does_not_re_price_a_february_call`).

        Returns `None` -- never `Decimal("0")` -- when nothing on file
        qualifies: an unpriced model, an unpriced token class for an
        otherwise-priced model, or a Firestore outage. All three are
        "we cannot price this", and collapsing any of them to a zero rate
        would make an unpriced call look like the cheapest possible one,
        produced entirely by missing configuration. Callers must report this
        as "unpriced", not "free".
        """
        at_date = at.date() if isinstance(at, datetime) else at
        try:
            client = self._client()
            snaps = await asyncio.to_thread(lambda: list(client.collection(_COLLECTION).stream()))
        except Exception as e:
            _log.error(
                "price_table_lookup_failed", model=model, token_class=token_class, error=str(e)
            )
            return None

        best_effective: date | None = None
        best_rate: Decimal | None = None
        for snap in snaps:
            data = snap.to_dict() or {}
            if data.get("model") != model or data.get("token_class") != token_class:
                continue
            try:
                effective = date.fromisoformat(data["effective_from"])
                rate = Decimal(str(data["rate"]))
            except (KeyError, TypeError, ValueError):
                # A document written by an incompatible build. Skipping one
                # row beats failing the whole lookup.
                _log.warning("price_table_unreadable_document", doc=snap.id)
                continue
            if effective > at_date:
                continue
            if best_effective is None or effective > best_effective:
                best_effective, best_rate = effective, rate

        return best_rate

    async def list_all(self, model: str | None = None) -> list[Price]:
        """All price rows, optionally filtered to one model. Admin/debug use."""
        try:
            client = self._client()
            snaps = await asyncio.to_thread(lambda: list(client.collection(_COLLECTION).stream()))
        except Exception as e:
            _log.error("price_table_list_failed", error=str(e))
            return []

        out: list[Price] = []
        for snap in snaps:
            data = snap.to_dict() or {}
            if model is not None and data.get("model") != model:
                continue
            try:
                out.append(
                    Price(
                        model=data["model"],
                        token_class=data["token_class"],
                        rate=Decimal(str(data["rate"])),
                        effective_from=date.fromisoformat(data["effective_from"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                _log.warning("price_table_unreadable_document", doc=snap.id)
        return out


def build_price_table(settings: Settings) -> PriceTable:
    return PriceTable(settings)
