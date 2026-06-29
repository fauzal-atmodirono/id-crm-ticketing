from chatbot.features.metrics.turn_schema import TURN_EVENTS_SCHEMA, turn_view_ddls


def test_schema_field_names_and_order() -> None:
    assert [f.name for f in TURN_EVENTS_SCHEMA] == [
        "event_id",
        "occurred_at",
        "channel",
        "session_id",
        "latency_ms",
        "is_first_turn",
        "is_fallback",
        "handed_off",
    ]


def test_schema_types() -> None:
    by_name = {f.name: f for f in TURN_EVENTS_SCHEMA}
    assert by_name["event_id"].field_type == "STRING"
    assert by_name["occurred_at"].field_type == "TIMESTAMP"
    assert by_name["latency_ms"].field_type == "INT64"
    assert by_name["is_fallback"].field_type in {"BOOL", "BOOLEAN"}


def test_view_keys_and_targets() -> None:
    ddls = turn_view_ddls("proj", "ds", "turn_events")
    assert set(ddls) == {"v_speed_of_response", "v_fallback_rate", "v_bounce_rate"}
    for name, sql in ddls.items():
        assert f"`proj.ds.{name}`" in sql
        assert "`proj.ds.turn_events`" in sql


def test_view_aggregates() -> None:
    ddls = turn_view_ddls("proj", "ds")
    assert "APPROX_QUANTILES(latency_ms, 100)[OFFSET(99)]" in ddls["v_speed_of_response"]
    assert "is_first_turn" in ddls["v_speed_of_response"]
    assert "COUNTIF(is_fallback)" in ddls["v_fallback_rate"]
    assert "SAFE_DIVIDE" in ddls["v_fallback_rate"]
    # bounce derives from a per-session turn count, then single-turn sessions
    assert "COUNT(*)" in ddls["v_bounce_rate"]
    assert "session_id" in ddls["v_bounce_rate"]
    assert "SAFE_DIVIDE" in ddls["v_bounce_rate"]
