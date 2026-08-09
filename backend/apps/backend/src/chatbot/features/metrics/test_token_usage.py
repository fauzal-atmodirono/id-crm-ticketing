"""Tests for the TokenUsage shape and its sink adapters.

The load-bearing assertions here are the `None`-vs-`0` ones: a cost report that
treats "we did not capture it" as "it was free" understates spend, silently and
in the flattering direction.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from google.genai import types

from chatbot.features.metrics.token_usage import (
    TOKEN_USAGE_SCHEMA,
    TOKEN_USAGE_TABLE,
    BigQueryTokenUsageSink,
    NoOpTokenUsageSink,
    TokenUsage,
    build_token_usage_sink,
    token_usage_from_response,
)
from chatbot.platform.config import Settings


class _FakeBQ:
    """Captures table/insert calls instead of hitting BigQuery (D2: there is no
    BigQuery in this environment). Mirrors test_bigquery_metrics.py's fake."""

    def __init__(self) -> None:
        self.created_datasets: list[str] = []
        self.created_tables: list[str] = []
        self.inserted: list[tuple[str, list[dict[str, Any]]]] = []
        self.insert_error: Exception | None = None

    def create_dataset(self, ref: str, exists_ok: bool = False) -> None:
        self.created_datasets.append(ref)

    def create_table(self, table: Any, exists_ok: bool = False) -> None:
        self.created_tables.append(str(table))

    def insert_rows_json(self, table_id: str, rows: list[dict[str, Any]]) -> list[Any]:
        if self.insert_error is not None:
            raise self.insert_error
        self.inserted.append((table_id, rows))
        return []


def _settings(provider: str = "bigquery") -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            bigquery_project_id="proj",
            bigquery_dataset="ds",
            metrics_provider=provider,
        ),
    )


def _row(**kw: Any) -> TokenUsage:
    base: dict[str, Any] = {
        "service": "backend",
        "surface": "assist.suggest",
        "model": "gemini-2.5-flash",
        "prompt_tokens": 10,
        "output_tokens": 5,
        "cached_tokens": 0,
    }
    base.update(kw)
    return TokenUsage(**base)


# --------------------------------------------------------------------------
# Extraction: the verified SDK field names, and None vs 0
# --------------------------------------------------------------------------


def test_all_three_token_classes_are_captured_from_the_real_field_names() -> None:
    """Verified against the installed google-genai (2.8.0):
    `GenerateContentResponseUsageMetadata` exposes `prompt_token_count`,
    `candidates_token_count` and `cached_content_token_count`.

    `cached_content_token_count` is the trap: guessing `cached_tokens` would
    record `None` forever, and a test that used the same wrong name would pass
    while the feature was dead.
    """
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=101,
            candidates_token_count=37,
            cached_content_token_count=64,
        )
    )
    usage = token_usage_from_response(
        response, service="backend", surface="chat.turn", model="gemini-2.5-flash"
    )
    assert usage == TokenUsage(
        service="backend",
        surface="chat.turn",
        model="gemini-2.5-flash",
        prompt_tokens=101,
        output_tokens=37,
        cached_tokens=64,
    )


def test_the_field_names_match_the_installed_sdk() -> None:
    """Pin the three names to the SDK itself rather than to this module's
    belief about them, so an SDK rename fails here instead of silently
    recording `None` for the rest of the model's life."""
    fields = types.GenerateContentResponseUsageMetadata.model_fields
    for name in ("prompt_token_count", "candidates_token_count", "cached_content_token_count"):
        assert name in fields, name


def test_absent_usage_metadata_records_none_for_all_three() -> None:
    usage = token_usage_from_response(
        SimpleNamespace(text="hi"), service="backend", surface="embed", model="m"
    )
    assert (usage.prompt_tokens, usage.output_tokens, usage.cached_tokens) == (None, None, None)


def test_a_none_usage_metadata_records_none_for_all_three() -> None:
    usage = token_usage_from_response(
        SimpleNamespace(usage_metadata=None), service="backend", surface="embed", model="m"
    )
    assert (usage.prompt_tokens, usage.output_tokens, usage.cached_tokens) == (None, None, None)


def test_an_observed_zero_is_recorded_as_zero_not_none() -> None:
    """The other half of the same rule. `0` is a fact about a call that
    happened; `None` is the absence of a fact. Anything that cannot tell them
    apart -- `if tokens:`, `tokens or 0`, `int(tokens or 0)` -- is the defect."""
    usage = token_usage_from_response(
        SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=0, candidates_token_count=0, cached_content_token_count=0
            )
        ),
        service="backend",
        surface="chat.turn",
        model="m",
    )
    assert usage.prompt_tokens == 0
    assert usage.prompt_tokens is not None
    assert usage.output_tokens == 0 and usage.output_tokens is not None
    assert usage.cached_tokens == 0 and usage.cached_tokens is not None


def test_a_partially_populated_usage_metadata_keeps_the_distinction_per_field() -> None:
    """Uncached calls omit `cached_content_token_count` entirely. That must not
    turn into a 0 that a "cached tokens are cheaper" multiplier then prices."""
    usage = token_usage_from_response(
        SimpleNamespace(
            usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=0)
        ),
        service="backend",
        surface="chat.turn",
        model="m",
    )
    assert (usage.prompt_tokens, usage.output_tokens, usage.cached_tokens) == (12, 0, None)


def test_a_non_numeric_count_degrades_to_none_rather_than_raising() -> None:
    """A stub or a future SDK shape must not be able to break a customer's turn
    from inside the metering code."""
    usage = token_usage_from_response(
        SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=object())),
        service="backend",
        surface="chat.turn",
        model="m",
    )
    assert usage.prompt_tokens is None


def test_a_bool_is_not_accepted_as_a_token_count() -> None:
    """`bool` is an `int` subclass, so `True` would otherwise be recorded as 1
    token and priced."""
    usage = token_usage_from_response(
        SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=True)),
        service="backend",
        surface="chat.turn",
        model="m",
    )
    assert usage.prompt_tokens is None


def test_token_usage_is_immutable() -> None:
    """A recorded measurement is a fact; nothing downstream gets to adjust it."""
    usage = _row()
    try:
        usage.prompt_tokens = 999  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("TokenUsage should be frozen")


# --------------------------------------------------------------------------
# Sinks
# --------------------------------------------------------------------------


async def test_the_noop_sink_drops_records_without_raising() -> None:
    await NoOpTokenUsageSink().record(_row())


def test_the_sink_defaults_to_noop() -> None:
    """Same `metrics_provider` switch (and same `noop` default) as
    `build_metrics_port` -- one telemetry story, not two."""
    assert build_token_usage_sink(_settings("noop")).__class__.__name__ == "NoOpTokenUsageSink"


def test_init_ensures_the_dataset_and_table() -> None:
    fake = _FakeBQ()
    BigQueryTokenUsageSink(_settings(), client=fake)
    assert fake.created_datasets == ["proj.ds"]
    assert fake.created_tables == [f"proj.ds.{TOKEN_USAGE_TABLE}"]


async def test_record_streams_one_row_with_an_id_and_a_timestamp() -> None:
    """`TokenUsage` is a pure value with no clock (mirroring `TurnEvent`); the
    sink is what stamps identity and time, so tests can assert the shape
    without freezing time."""
    fake = _FakeBQ()
    sink = BigQueryTokenUsageSink(_settings(), client=fake)
    await sink.record(_row(prompt_tokens=10, output_tokens=5, cached_tokens=2))
    assert len(fake.inserted) == 1
    table_id, rows = fake.inserted[0]
    assert table_id == f"proj.ds.{TOKEN_USAGE_TABLE}"
    row = rows[0]
    assert row["usage_id"]
    assert row["occurred_at"]
    assert row["service"] == "backend"
    assert row["surface"] == "assist.suggest"
    assert row["model"] == "gemini-2.5-flash"
    assert (row["prompt_tokens"], row["output_tokens"], row["cached_tokens"]) == (10, 5, 2)


async def test_record_writes_null_for_uncaptured_counts_and_zero_for_observed_zero() -> None:
    """The `None`/`0` distinction has to survive the trip into the warehouse,
    or the cost view rebuilds the same lie from a REQUIRED column."""
    fake = _FakeBQ()
    sink = BigQueryTokenUsageSink(_settings(), client=fake)
    await sink.record(_row(prompt_tokens=None, output_tokens=0, cached_tokens=None))
    row = fake.inserted[0][1][0]
    assert row["prompt_tokens"] is None
    assert row["output_tokens"] == 0
    assert row["cached_tokens"] is None


def test_the_three_count_columns_are_nullable() -> None:
    """A REQUIRED column would force the writer to invent a 0."""
    modes = {f.name: f.mode for f in TOKEN_USAGE_SCHEMA}
    assert modes["prompt_tokens"] == "NULLABLE"
    assert modes["output_tokens"] == "NULLABLE"
    assert modes["cached_tokens"] == "NULLABLE"
    # The dimensions Task 4's day x service x surface x model view groups by
    # must always be present.
    assert modes["service"] == "REQUIRED"
    assert modes["surface"] == "REQUIRED"
    assert modes["model"] == "REQUIRED"
    assert modes["occurred_at"] == "REQUIRED"


async def test_record_swallows_insert_errors() -> None:
    fake = _FakeBQ()
    fake.insert_error = RuntimeError("BQ down")
    sink = BigQueryTokenUsageSink(_settings(), client=fake)
    await sink.record(_row())  # must not raise


def test_ensure_schema_swallows_bootstrap_errors() -> None:
    class _Broken(_FakeBQ):
        def create_dataset(self, ref: str, exists_ok: bool = False) -> None:
            raise RuntimeError("no permission")

    BigQueryTokenUsageSink(_settings(), client=_Broken())  # must not raise
