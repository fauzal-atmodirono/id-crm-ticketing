"""Token accounting: the `TokenUsage` shape, its sink port, and the two
adapters (noop default, BigQuery streaming).

Why a port with a `noop` default rather than "just write to BigQuery": this
repo already streams per-turn analytics through `MetricsPort` /
`build_metrics_port` (`features/chat/adapters/bigquery_metrics.py`), selected
off the same `metrics_provider` setting, defaulting to `noop`. Token metering
is the same kind of telemetry with the same failure tolerance, so it reuses
that port/adapter shape instead of inventing a second telemetry story with its
own configuration and its own failure modes.

Why every count is `int | None` and is NEVER coerced to `0`:
a zero-token call is a real, observed thing. "The response carried no usage
metadata" is a *different* thing. A cost report that conflates the two
**understates spend** -- silently, and in the direction that looks good. So
`token_usage_from_response` uses `getattr(..., None)` and returns `None` for an
absent field, and every reader (Task 3's price table, Task 4's `v_ai_cost`)
must keep the distinction. Concretely: never write `if tokens:` -- that
truthiness check collapses `0` and `None` into the same branch, which is
exactly the defect this module exists to prevent. Use `is None` to ask
"did we capture it" and `== 0` to ask "was it free".

Why `TokenUsage` carries no timestamp: it is a pure value, mirroring
`events.py`'s `TurnEvent` ("Pure: no clock, no uuid, no I/O") so it can be
built and asserted without freezing time. The sink stamps `occurred_at` and
mints `usage_id` at write time, exactly as `BigQueryMetricsAdapter.emit_turn`
does for turn events.

Why `record` must never raise: metering is bookkeeping wrapped around a
customer's conversation. Losing a usage record costs us a row in a cost
report; letting a sink failure propagate costs the customer their reply. Both
adapters therefore swallow everything, and `MeteredGenaiClient` swallows again
at the call site (defence in depth -- a third-party sink is not bound by this
docstring).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from google.cloud import bigquery

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# The warehouse table token usage streams into. Deliberately a module constant
# rather than a new setting: this task is barred from adding config, and the
# project/dataset it lives in are already configurable
# (`bigquery_project_id` / `bigquery_dataset`). If a tenant ever needs to
# rename it, that is a one-field addition for the wiring wave.
TOKEN_USAGE_TABLE = "token_usage"  # noqa: S105 -- a table name, not a secret

# NULLABLE on all three counts, deliberately: NULL is how "we did not capture
# it" survives the trip into the warehouse. A REQUIRED column would force the
# writer to invent a 0 and the cost report would understate spend.
TOKEN_USAGE_SCHEMA = [
    bigquery.SchemaField("usage_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("occurred_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("service", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("surface", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("model", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("prompt_tokens", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("output_tokens", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("cached_tokens", "INTEGER", mode="NULLABLE"),
]


@dataclass(frozen=True)
class TokenUsage:
    """One Gemini call's token accounting.

    `service` is which of the two services made the call ("backend" or
    "agent" -- the `agent/` side records the same three counts onto
    `ai_actions`). `surface` is which feature made it ("assist.suggest",
    "chat.turn", "embed", "phone.live", "orchestrator", ...), so a cost report
    can attribute spend to a product surface rather than to "Gemini".

    All three counts are `int | None`. `None` means "the response carried no
    usage metadata"; `0` means "the model reported zero". See the module
    docstring -- do not collapse them.
    """

    service: str
    surface: str
    model: str
    prompt_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None


def _int_or_none(value: Any) -> int | None:
    """Coerce an SDK count to `int`, preserving `None`.

    `0` survives as `0` and `None` survives as `None` -- the whole point. A
    non-numeric value (a Mock left over from a stub, say) degrades to `None`
    rather than raising, because a metering bug must not break a turn.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass; never a token count
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def token_usage_from_response(
    response: Any,
    *,
    service: str,
    surface: str,
    model: str,
) -> TokenUsage:
    """Extract the three token classes off a google-genai response.

    The field names are the ones on
    `google.genai.types.GenerateContentResponseUsageMetadata` (verified
    against the installed SDK, google-genai 2.8.0):

        prompt_token_count          -> prompt_tokens
        candidates_token_count      -> output_tokens
        cached_content_token_count  -> cached_tokens

    Note `cached_content_token_count`, NOT `cached_tokens` -- guessing that
    name would make the feature record `None` forever while every test using
    the same wrong name passed.

    An absent `usage_metadata` (a failed call, or `EmbedContentResponse`,
    which has no token accounting at all -- embeddings are billed per
    character via `metadata.billable_character_count`) yields `None` for all
    three rather than zeros.
    """
    usage = getattr(response, "usage_metadata", None)
    return TokenUsage(
        service=service,
        surface=surface,
        model=model,
        prompt_tokens=_int_or_none(getattr(usage, "prompt_token_count", None)),
        output_tokens=_int_or_none(getattr(usage, "candidates_token_count", None)),
        cached_tokens=_int_or_none(getattr(usage, "cached_content_token_count", None)),
    )


class TokenUsageSink(Protocol):
    """Port for recording one Gemini call's token usage. Mirrors
    `MetricsPort.emit_turn`: best-effort, must never raise."""

    async def record(self, usage: TokenUsage) -> None:
        """Best-effort: record a single usage row. Must never raise."""
        ...


class NoOpTokenUsageSink:
    """Default sink -- drops every record. Used in dev/tests and whenever
    `metrics_provider != "bigquery"`, so turning `token_metering_enabled` on
    without a warehouse configured is harmless rather than an error."""

    async def record(self, _usage: TokenUsage) -> None:
        return None


class BigQueryTokenUsageSink:
    """Streams one row per Gemini call into BigQuery.

    Ensures the dataset + table exist on init (best-effort, mirroring
    `BigQueryMetricsAdapter`) so a fresh tenant does not need a manual DDL
    step. `record` is fail-open: an insert error is logged and dropped.
    """

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._project = settings.bigquery_project_id
        self._dataset = settings.bigquery_dataset
        self._table = TOKEN_USAGE_TABLE
        self._table_id = f"{self._project}.{self._dataset}.{self._table}"
        self._client = client or bigquery.Client(project=self._project)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            self._client.create_dataset(f"{self._project}.{self._dataset}", exists_ok=True)
            self._client.create_table(
                bigquery.Table(self._table_id, schema=TOKEN_USAGE_SCHEMA), exists_ok=True
            )
        except Exception as e:  # best-effort bootstrap
            _log.error("token_usage_ensure_schema_failed", error=str(e))

    async def record(self, usage: TokenUsage) -> None:
        try:
            row = {
                "usage_id": uuid.uuid4().hex,
                "occurred_at": datetime.now(UTC).isoformat(),
                "service": usage.service,
                "surface": usage.surface,
                "model": usage.model,
                # Passed through as-is: None becomes SQL NULL, 0 becomes 0.
                "prompt_tokens": usage.prompt_tokens,
                "output_tokens": usage.output_tokens,
                "cached_tokens": usage.cached_tokens,
            }
            errors = await asyncio.to_thread(self._client.insert_rows_json, self._table_id, [row])
            if errors:
                _log.warning("token_usage_insert_returned_errors", errors=str(errors))
        except Exception as e:  # best-effort: never break the call being metered
            _log.error("token_usage_record_failed", error=str(e))


def build_token_usage_sink(settings: Settings) -> TokenUsageSink:
    """Pick the sink implementation from settings.

    Reuses `metrics_provider` (the same switch `build_metrics_port` reads)
    rather than adding a parallel one: a tenant that streams turn events to
    BigQuery streams token usage there too, and one that does not streams
    neither. An adapter that fails to initialise degrades to noop -- metering
    must not be able to crash boot.
    """
    if settings.metrics_provider == "bigquery":
        try:
            return BigQueryTokenUsageSink(settings)
        except Exception as e:
            _log.error("token_usage_sink_init_failed_falling_back_to_noop", error=str(e))
            return NoOpTokenUsageSink()
    return NoOpTokenUsageSink()
