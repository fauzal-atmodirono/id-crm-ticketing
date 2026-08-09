"""AI cost reporting: tokens x an effective-dated rate, and every hole in it.

**Why the money is computed here and not in SQL.** Prices are effective-dated
and live in Firestore (`price_table.py`), so a BigQuery view cannot join to a
rate; and the rate that applies is the one in force on the *day of the usage*,
never today's. So `v_ai_token_usage` returns a day-grained token aggregate and
this module asks `price_for(model, token_class, at=row.day)` per row.

**Why there is no total.** The report is structurally incomplete and the
payload has to say so in a shape a consumer cannot drop:

- Five surfaces are metered and priceable: `assist.suggest` (which
  `/assist/summarize` and `/assist/ask` roll up into), `assist.copilot`,
  `assist.translate`, `chat.transcribe`, `phone.classify`.
- `embed` is visible but unpriceable: embeddings bill per character and
  `EmbedContentResponse` carries no `usage_metadata`, so all three token
  counts are `None` and `token_usage` has no character-count column to
  multiply the per-character rate by.
- `chat.turn` produces no row at all -- google-adk builds its own Gemini
  client inside the installed package -- and it is the busiest surface in the
  product. `phone.live` likewise (usage arrives in server messages). The
  `agent` service's counts sit in Postgres and are never exported.
- `thoughts_token_count` and `tool_use_prompt_token_count` are billed and
  outside the three captured classes, so the three sum to LESS than
  `total_token_count`.

A single "AI spend" figure would therefore omit the largest line item and
understate the rest, in the direction that looks good. So this report emits
**`priced_subtotal_usd` and never a `total`** (`test_the_report_emits_no_
unqualified_total` asserts the absence, not the presence), lists every
unmetered and unpriceable surface as its own row with `cost_usd: null`, and
carries a `completeness` block naming what is missing. **An unmetered surface
must never appear as 0 cost** -- a zero is a claim about spend, a null is a
statement about instrumentation.

**Why every sum ships beside a call count.** `SUM()` skips NULLs. A surface
that captured usage metadata on 3 of 3000 calls sums to a small number that is
indistinguishable from a small bill. So each token class reports
`calls_captured` next to its `tokens`, and each row reports
`calls_without_usage_metadata`.

**Why an unpriced model is not a free one.** `price_for` returns `None` for a
model nobody has put a rate on file for (and for a Firestore outage). That
class's tokens are then reported under `unpriced_tokens` and the class is
named in `unpriced_token_classes`; its cost is `null` and it contributes
nothing to the subtotal. Never `rate or Decimal(0)`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from google.cloud import bigquery

from chatbot.features.metrics.bigquery_schema import (
    AI_COST_EXCLUDED_TOKEN_CLASSES,
    AI_COST_STATUS_METERED,
    AI_COST_STATUS_UNMETERED,
    AI_COST_STATUS_UNPRICEABLE,
    AI_COST_SURFACE_COVERAGE,
    SurfaceCoverage,
)
from chatbot.features.metrics.price_table import (
    TOKEN_CLASS_CACHED,
    TOKEN_CLASS_OUTPUT,
    TOKEN_CLASS_PROMPT,
)

if TYPE_CHECKING:
    from chatbot.features.metrics.period import PeriodRange
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

AI_COST_VIEW = "v_ai_cost"
_USAGE_VIEW = "v_ai_token_usage"

# The three classes `TokenUsage` captures, in the order a reader expects them.
# Deliberately the price table's own class names, so a report key and a price
# row's `token_class` are the same string.
PRICED_TOKEN_CLASSES: tuple[str, ...] = (
    TOKEN_CLASS_PROMPT,
    TOKEN_CLASS_OUTPUT,
    TOKEN_CLASS_CACHED,
)

AI_COST_INCOMPLETE_CAVEAT = (
    "This is a PARTIAL cost figure and there is deliberately no total. "
    "chat.turn (the highest-volume surface), phone.live and the agent "
    "service produce no priced usage rows at all, and embeddings are visible "
    "but unpriceable, so their spend is missing rather than zero. "
    "thoughts_token_count and tool_use_prompt_token_count are billed but "
    "outside the three captured token classes, so even the metered surfaces "
    "are understated for a thinking-enabled model. Read "
    "priced_subtotal_usd as a floor, never as AI spend."
)

# Why the per-conversation figure carries its own basis string: the number a
# client quotes is "what does the AI cost per conversation", and dividing a
# partial subtotal by a full conversation count understates it twice over.
COST_PER_CONVERSATION_BASIS = (
    "priced_subtotal_usd / conversations. The numerator excludes every "
    "unmetered and unpriceable surface listed under completeness, so this is "
    "a lower bound on cost per conversation, not the figure itself."
)


@dataclass(frozen=True)
class TokenUsageAggregateRow:
    """One row of `v_ai_token_usage`: a day x service x surface x model bucket.

    Every token count is `int | None` and `None` means "no call in this bucket
    reported it" -- `SUM()` over an all-NULL column is NULL. Never coerce.
    """

    day: date | None
    service: str
    surface: str
    model: str
    calls: int
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    calls_with_prompt_tokens: int = 0
    calls_with_output_tokens: int = 0
    calls_with_cached_tokens: int = 0
    calls_without_usage_metadata: int = 0

    def tokens(self, token_class: str) -> int | None:
        return {
            TOKEN_CLASS_PROMPT: self.prompt_tokens,
            TOKEN_CLASS_OUTPUT: self.output_tokens,
            TOKEN_CLASS_CACHED: self.cached_tokens,
        }[token_class]

    def calls_captured(self, token_class: str) -> int:
        return {
            TOKEN_CLASS_PROMPT: self.calls_with_prompt_tokens,
            TOKEN_CLASS_OUTPUT: self.calls_with_output_tokens,
            TOKEN_CLASS_CACHED: self.calls_with_cached_tokens,
        }[token_class]


class AiCostUsagePort(Protocol):
    """Read side for the cost report. Fail-open like every other metrics
    read: a failed query returns `([], False)` so the endpoint can say
    "unavailable" rather than "no spend"."""

    async def fetch_token_usage(
        self, period: PeriodRange | None
    ) -> tuple[list[TokenUsageAggregateRow], bool]: ...

    async def fetch_conversation_count(self, period: PeriodRange | None) -> int | None:
        """The denominator for cost-per-conversation, or `None` when it could
        not be read. `None`, not 0 -- a zero denominator and an unreadable one
        are different, and only one of them is a division by zero."""
        ...


class PriceLookup(Protocol):
    async def price_for(self, model: str, token_class: str, at: Any) -> Decimal | None: ...


class NoOpAiCostUsage:
    """Used whenever `metrics_provider != "bigquery"`. Reports the read as
    FAILED rather than empty: with no warehouse configured there is no
    evidence of zero spend, and an empty-but-ok read would render as a
    confident 0.00."""

    async def fetch_token_usage(
        self, _period: PeriodRange | None
    ) -> tuple[list[TokenUsageAggregateRow], bool]:
        return [], False

    async def fetch_conversation_count(self, _period: PeriodRange | None) -> int | None:
        return None


class BigQueryAiCostUsage:
    """Reads `v_ai_token_usage` and the conversation count for the denominator.

    Mirrors `BigQueryMetricsQuery`: `asyncio.to_thread` around the blocking
    SDK call, named query parameters for the date window (never interpolated),
    and a failed query degrading to `([], False)` instead of raising.
    """

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._prefix = f"{settings.bigquery_project_id}.{settings.bigquery_dataset}"
        self._conversations = settings.bigquery_conversations_table
        self._client = client or bigquery.Client(project=settings.bigquery_project_id)

    def _job_config(self, period: PeriodRange | None) -> bigquery.QueryJobConfig | None:
        if period is None:
            return None
        return bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start", "DATE", period.start),
                bigquery.ScalarQueryParameter("end", "DATE", period.end),
            ]
        )

    def _fetch_usage(self, period: PeriodRange | None) -> tuple[list[TokenUsageAggregateRow], bool]:
        sql = f"SELECT * FROM `{self._prefix}.{_USAGE_VIEW}`"  # noqa: S608
        if period is not None:
            sql = f"{sql} WHERE day BETWEEN @start AND @end"
        try:
            job = self._client.query(sql, job_config=self._job_config(period))
            return [TokenUsageAggregateRow(**dict(r)) for r in job.result()], True
        except Exception as e:
            _log.error("ai_cost_usage_query_failed", error=str(e))
            return [], False

    def _fetch_conversations(self, period: PeriodRange | None) -> int | None:
        sql = f"SELECT COUNT(*) AS cases FROM `{self._prefix}.{self._conversations}`"  # noqa: S608
        if period is not None:
            sql = f"{sql} WHERE DATE(created_at) BETWEEN @start AND @end"
        try:
            job = self._client.query(sql, job_config=self._job_config(period))
            for row in job.result():
                return int(dict(row)["cases"])
            return None
        except Exception as e:
            _log.error("ai_cost_conversation_count_query_failed", error=str(e))
            return None

    async def fetch_token_usage(
        self, period: PeriodRange | None
    ) -> tuple[list[TokenUsageAggregateRow], bool]:
        return await asyncio.to_thread(self._fetch_usage, period)

    async def fetch_conversation_count(self, period: PeriodRange | None) -> int | None:
        return await asyncio.to_thread(self._fetch_conversations, period)


def build_ai_cost_usage_port(settings: Settings) -> AiCostUsagePort:
    """Same `metrics_provider` switch the token sink and the dashboard
    adapter read, and the same degrade-to-noop-on-init-failure rule: a cost
    report must not be able to crash boot."""
    if getattr(settings, "metrics_provider", "noop") == "bigquery":
        try:
            return BigQueryAiCostUsage(settings)
        except Exception as e:
            _log.error("ai_cost_usage_port_init_failed_falling_back_to_noop", error=str(e))
    return NoOpAiCostUsage()


def _decimal_str(value: Decimal) -> str:
    """Money as a string, never a float. A JSON float for an invoiced figure
    is how a cent-level discrepancy nobody can explain gets into a report."""
    return str(value)


async def build_ai_cost_report(
    rows: list[TokenUsageAggregateRow],
    prices: PriceLookup,
    *,
    ok: bool = True,
    conversations: int | None = None,
    period: PeriodRange | None = None,
    coverage: tuple[SurfaceCoverage, ...] = AI_COST_SURFACE_COVERAGE,
) -> dict[str, Any]:
    """The `/metrics/ai-cost` payload.

    `rows` are `v_ai_token_usage` rows for the window. `coverage` is the
    declared surface inventory -- every entry produces a row in `surfaces`
    whether or not it has usage, so an unmetered surface is visibly present
    and visibly null rather than absent (absent reads as "we have all the
    surfaces and this one is not among them").
    """
    by_key: dict[tuple[str, str, str], list[TokenUsageAggregateRow]] = {}
    for row in rows:
        by_key.setdefault((row.service, row.surface, row.model), []).append(row)

    coverage_by_surface = {(c.service, c.surface): c for c in coverage}

    surfaces: list[dict[str, Any]] = []
    subtotal = Decimal("0")
    priced_anything = False
    unpriced_models: set[str] = set()

    # 1) Every metered (service, surface, model) that actually produced usage.
    for (service, surface, model), group in sorted(by_key.items()):
        cover = coverage_by_surface.get((service, surface))
        status = cover.status if cover else AI_COST_STATUS_METERED
        reason = (
            cover.reason
            if cover
            else (
                "Not in the declared surface inventory. Usage rows exist for it, "
                "so it is real spend attributed to an unknown surface."
            )
        )
        entry, entry_cost, entry_unpriced = await _priced_surface(
            service, surface, model, group, prices, status, reason
        )
        surfaces.append(entry)
        unpriced_models |= entry_unpriced
        if entry_cost is not None:
            subtotal += entry_cost
            priced_anything = True

    # 2) Every declared surface with NO usage rows at all. Reported with null
    #    tokens and null cost, never zero.
    seen = {(service, surface) for service, surface, _model in by_key}
    for cover in coverage:
        if (cover.service, cover.surface) in seen:
            continue
        surfaces.append(
            {
                "service": cover.service,
                "surface": cover.surface,
                "model": None,
                "cost_status": cover.status,
                "cost_status_reason": cover.reason,
                "calls": None,
                "cost_usd": None,
                "token_classes": {},
                "calls_without_usage_metadata": None,
                "unpriced_token_classes": [],
            }
        )

    priced_subtotal = _decimal_str(subtotal) if priced_anything else None
    cost_per_conversation = (
        _decimal_str(subtotal / Decimal(conversations))
        if priced_anything and conversations
        else None
    )

    return {
        "currency": "USD",
        "period": (
            None
            if period is None
            else {
                "from": period.start.isoformat(),
                "to": period.end.isoformat(),
                "granularity": period.granularity,
            }
        ),
        "read_status": "ok" if ok else "unavailable",
        "surfaces": surfaces,
        # NOT "total". See the module docstring.
        "priced_subtotal_usd": priced_subtotal,
        "conversations": conversations,
        "cost_per_conversation_usd": cost_per_conversation,
        "cost_per_conversation_basis": COST_PER_CONVERSATION_BASIS,
        "completeness": {
            "is_complete": False,
            "metered_surfaces": [c.surface for c in coverage if c.status == AI_COST_STATUS_METERED],
            "unpriceable_surfaces": [
                {"service": c.service, "surface": c.surface, "reason": c.reason}
                for c in coverage
                if c.status == AI_COST_STATUS_UNPRICEABLE
            ],
            "unmetered_surfaces": [
                {"service": c.service, "surface": c.surface, "reason": c.reason}
                for c in coverage
                if c.status == AI_COST_STATUS_UNMETERED
            ],
            "unpriced_models": sorted(unpriced_models),
            "excluded_token_classes": list(AI_COST_EXCLUDED_TOKEN_CLASSES),
            "caveat": AI_COST_INCOMPLETE_CAVEAT,
        },
    }


async def _priced_surface(
    service: str,
    surface: str,
    model: str,
    group: list[TokenUsageAggregateRow],
    prices: PriceLookup,
    status: str,
    reason: str,
) -> tuple[dict[str, Any], Decimal | None, set[str]]:
    """One `surfaces[]` entry, its contribution to the subtotal, and the
    models it could not price.

    Cost is summed per DAY, because the rate that applied is the rate in force
    on the day of the usage: pricing a month's tokens at one rate is exactly
    the mistake `price_table`'s effective dating exists to prevent.
    """
    classes: dict[str, dict[str, Any]] = {}
    entry_cost: Decimal | None = None
    unpriced_models: set[str] = set()

    for token_class in PRICED_TOKEN_CLASSES:
        tokens_total = 0
        captured_calls = 0
        any_tokens = False
        class_cost: Decimal | None = None
        priced = status == AI_COST_STATUS_METERED

        for row in group:
            captured_calls += row.calls_captured(token_class)
            count = row.tokens(token_class)
            if count is None:
                # Nothing captured in this bucket. Not zero tokens -- unknown.
                continue
            any_tokens = True
            tokens_total += count
            if not priced or row.day is None:
                continue
            rate = await prices.price_for(model, token_class, row.day)
            if rate is None:
                unpriced_models.add(model)
                priced = False
                class_cost = None
                continue
            class_cost = (class_cost or Decimal("0")) + rate * Decimal(count)

        classes[token_class] = {
            "tokens": tokens_total if any_tokens else None,
            "calls_captured": captured_calls,
            "cost_usd": _decimal_str(class_cost) if class_cost is not None else None,
            "priced": class_cost is not None,
        }
        if class_cost is not None:
            entry_cost = (entry_cost or Decimal("0")) + class_cost

    return (
        {
            "service": service,
            "surface": surface,
            "model": model,
            "cost_status": status,
            "cost_status_reason": reason,
            "calls": sum(row.calls for row in group),
            "cost_usd": _decimal_str(entry_cost) if entry_cost is not None else None,
            "token_classes": classes,
            "calls_without_usage_metadata": sum(row.calls_without_usage_metadata for row in group),
            "unpriced_token_classes": [
                name for name, spec in classes.items() if not spec["priced"]
            ],
        },
        entry_cost,
        unpriced_models,
    )
