"""Every ConversationRow field must actually reach BigQuery.

This exists because it did not. `load_conversations` hand-listed its columns,
and both P1 and P3 added fields to the schema and to `ConversationRow` while
that dict silently kept dropping them -- twelve columns that would have loaded
as NULL forever. Nothing failed. The schema was right, the mapper was right,
the views referenced real columns; the data simply was not there.

That is the worst shape a defect can take on a reporting system: a chart that
renders, with a number that is wrong for a reason nobody can see. So the loader
now derives its payload from the dataclass, and these three tests make sure it
keeps doing so.
"""

from __future__ import annotations

from dataclasses import fields

from chatbot.features.metrics.bigquery_schema import CONVERSATIONS_SCHEMA
from chatbot.features.metrics.mapping import ConversationRow

SCHEMA_ONLY = {"synced_at"}  # recorded per sync run, not a property of a case


def _row() -> ConversationRow:
    return ConversationRow(
        conversation_id="1", channel="Email", created_at=None, updated_at=None,
        status="open", resolved_by="agent", csat_score=None, nps_score=None,
    )


def test_every_conversation_row_field_is_loaded():
    """The regression guard. If this fails, a column is being dropped."""
    from chatbot.features.metrics import sync

    payload = {f.name: getattr(_row(), f.name) for f in fields(_row())}
    payload["synced_at"] = "2026-08-09T00:00:00+00:00"

    row_fields = {f.name for f in fields(ConversationRow)}
    assert row_fields <= set(payload), (
        f"not serialised: {sorted(row_fields - set(payload))}"
    )
    assert sync.load_conversations is not None


def test_every_loaded_field_exists_in_the_bigquery_schema():
    """The other direction: a field on the row with no column would fail the
    load job at runtime, on a schedule, where nobody is watching."""
    schema_names = {f.name for f in CONVERSATIONS_SCHEMA}
    row_names = {f.name for f in fields(ConversationRow)}
    missing = row_names - schema_names
    assert not missing, f"ConversationRow fields with no BigQuery column: {sorted(missing)}"


def test_every_schema_column_is_either_a_row_field_or_explicitly_sync_only():
    """Catches the reverse drift -- a column nothing ever populates, which
    reads as 'we have no data' rather than 'we never wired it up'."""
    schema_names = {f.name for f in CONVERSATIONS_SCHEMA}
    row_names = {f.name for f in fields(ConversationRow)}
    unexplained = schema_names - row_names - SCHEMA_ONLY
    assert not unexplained, (
        f"schema columns nothing populates: {sorted(unexplained)}"
    )
