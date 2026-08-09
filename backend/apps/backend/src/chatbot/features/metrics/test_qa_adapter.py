from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from chatbot.features.metrics.qa import CallQaRubric, QaLabel
from chatbot.features.metrics.qa_adapter import BigQueryQaLabels, build_qa_label_port
from chatbot.platform.config import Settings


class _FakeBQ:
    def __init__(self) -> None:
        self.created_datasets: list[str] = []
        self.created_tables: list[str] = []
        self.queries: list[str] = []
        self.inserted: list[tuple[str, list[dict[str, Any]]]] = []
        self.insert_error: Exception | None = None

    def create_dataset(self, ref: str, exists_ok: bool = False) -> None:
        self.created_datasets.append(ref)

    def create_table(self, table: Any, exists_ok: bool = False) -> None:
        self.created_tables.append(str(table))

    def query(self, ddl: str) -> Any:
        self.queries.append(ddl)
        return SimpleNamespace(result=lambda: None)

    def insert_rows_json(self, table_id: str, rows: list[dict[str, Any]]) -> list[Any]:
        if self.insert_error is not None:
            raise self.insert_error
        self.inserted.append((table_id, rows))
        return []


def _settings() -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            bigquery_project_id="proj",
            bigquery_dataset="ds",
            bigquery_qa_labels_table="qa_labels",
            qa_provider="bigquery",
        ),
    )


def _label() -> QaLabel:
    return QaLabel(
        conversation_id="T-9",
        accuracy=88,
        quality=92,
        reviewer="alice",
        notes="ok",
        labeled_at=datetime(2026, 6, 29, tzinfo=UTC),
    )


def test_init_ensures_dataset_table_and_view() -> None:
    fake = _FakeBQ()
    BigQueryQaLabels(_settings(), client=fake)
    assert fake.created_datasets == ["proj.ds"]
    assert fake.created_tables == ["proj.ds.qa_labels"]
    assert len(fake.queries) == 2  # v_quality + v_call_qa (P8 task 7)


async def test_record_label_inserts_row_with_all_fields() -> None:
    fake = _FakeBQ()
    adapter = BigQueryQaLabels(_settings(), client=fake)
    await adapter.record_label(_label())
    assert len(fake.inserted) == 1
    table_id, rows = fake.inserted[0]
    assert table_id == "proj.ds.qa_labels"
    row = rows[0]
    assert row["conversation_id"] == "T-9"
    assert row["accuracy"] == 88
    assert row["quality"] == 92
    assert row["reviewer"] == "alice"
    assert row["notes"] == "ok"
    assert row["labeled_at"].startswith("2026-06-29T")
    # P8 task 7: a channel-agnostic label (no channel, no rubric) sends the
    # EXACT pre-P8 row -- no new keys at all, so a pre-migration table (see
    # qa_schema.py's docstring) is never even asked for the new columns.
    assert "channel" not in row
    assert "rubric_greeting" not in row
    assert "call_qa_percentage" not in row


async def test_record_label_with_channel_and_rubric_writes_the_new_columns() -> None:
    fake = _FakeBQ()
    adapter = BigQueryQaLabels(_settings(), client=fake)
    label = QaLabel(
        conversation_id="T-9",
        accuracy=88,
        quality=92,
        reviewer="alice",
        notes="ok",
        labeled_at=datetime(2026, 6, 29, tzinfo=UTC),
        channel="Phone",
        call_rubric=CallQaRubric(
            greeting=True, identification=True, resolution=True, closing=False, compliance=True
        ),
    )
    await adapter.record_label(label)
    _table_id, rows = fake.inserted[0]
    row = rows[0]
    assert row["channel"] == "Phone"
    assert row["rubric_greeting"] is True
    assert row["rubric_closing"] is False
    assert row["call_qa_percentage"] == pytest.approx(80.0)


async def test_record_label_with_incomplete_rubric_omits_the_percentage() -> None:
    """A partially-scored rubric writes the criteria it has, but no
    call_qa_percentage -- there is nothing complete to report yet, and
    `NULL` there would be indistinguishable from "reviewed, 0%"."""
    fake = _FakeBQ()
    adapter = BigQueryQaLabels(_settings(), client=fake)
    label = QaLabel(
        conversation_id="T-9",
        accuracy=88,
        quality=92,
        reviewer="alice",
        notes="ok",
        labeled_at=datetime(2026, 6, 29, tzinfo=UTC),
        channel="Phone",
        call_rubric=CallQaRubric(greeting=True),
    )
    await adapter.record_label(label)
    _table_id, rows = fake.inserted[0]
    row = rows[0]
    assert row["rubric_greeting"] is True
    assert row["rubric_identification"] is None
    assert "call_qa_percentage" not in row


async def test_record_label_swallows_insert_errors() -> None:
    fake = _FakeBQ()
    fake.insert_error = RuntimeError("BQ down")
    adapter = BigQueryQaLabels(_settings(), client=fake)
    # Must not raise.
    await adapter.record_label(_label())


def test_build_qa_label_port_selects_noop_by_default() -> None:
    s = cast(Settings, SimpleNamespace(qa_provider="noop"))
    assert build_qa_label_port(s).__class__.__name__ == "NoOpQaLabels"


def test_build_qa_label_port_falls_back_to_noop_on_init_failure(monkeypatch: Any) -> None:
    from chatbot.features.metrics import qa_adapter  # noqa: PLC0415

    def _boom(_settings: Any) -> Any:
        raise RuntimeError("no creds")

    monkeypatch.setattr(qa_adapter, "BigQueryQaLabels", _boom)
    s = cast(Settings, SimpleNamespace(qa_provider="bigquery"))
    assert build_qa_label_port(s).__class__.__name__ == "NoOpQaLabels"
