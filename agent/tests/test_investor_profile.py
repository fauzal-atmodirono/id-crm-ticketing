"""Feature B canonicalization: the model's free-form answers become a fixed
vocabulary, or nothing at all."""

from app.services import investor_profile as ip


def test_maps_each_answer_to_its_indonesian_display_value():
    out = ip.canonical_attributes(
        {
            "goal": "retirement",
            "horizon": "very_long",
            "drawdown_reaction": "hold",
            "experience": "beginner",
        },
        captured_at="2026-08-26T10:00:00Z",
    )
    assert out == {
        "investor_goal": "Dana pensiun",
        "investor_horizon": "> 10 tahun",
        "investor_drawdown_reaction": "Tetap menahan",
        "investor_experience": "Pemula",
        "preference_captured_at": "2026-08-26T10:00:00Z",
    }


def test_unknown_values_are_dropped_not_guessed():
    out = ip.canonical_attributes(
        {"goal": "buying a yacht", "experience": "beginner"},
        captured_at="2026-08-26T10:00:00Z",
    )
    assert "investor_goal" not in out
    assert out["investor_experience"] == "Pemula"


def test_nothing_recognisable_yields_no_attributes_at_all():
    # Not even the timestamp: an empty capture must not stamp the contact,
    # or the sidebar shows "profiled" for a conversation that captured nothing.
    assert ip.canonical_attributes({"goal": "???"}, captured_at="x") == {}
    assert ip.canonical_attributes(None, captured_at="x") == {}
    assert ip.canonical_attributes("not a dict", captured_at="x") == {}


def test_never_emits_risk_profile():
    out = ip.canonical_attributes(
        {"risk_profile": "Agresif", "experience": "experienced"},
        captured_at="2026-08-26T10:00:00Z",
    )
    assert "risk_profile" not in out
    assert set(out) <= set(ip.ATTRIBUTE_KEYS)


def test_implied_risk_tier_reads_the_drawdown_answer():
    assert ip.implied_risk_tier({"drawdown_reaction": "sell_all"}) == 1
    assert ip.implied_risk_tier({"drawdown_reaction": "hold"}) == 2
    assert ip.implied_risk_tier({"drawdown_reaction": "buy_more"}) == 3
    assert ip.implied_risk_tier({"drawdown_reaction": "shrug"}) is None
    assert ip.implied_risk_tier({}) is None


def test_recorded_risk_tier_refuses_to_guess():
    # An unrecognised profile must mean "no comparison", never "assume
    # conservative" -- guessing here fires a review flag at a customer whose
    # profile was fine all along.
    assert ip.recorded_risk_tier("Konservatif") == 1
    assert ip.recorded_risk_tier("moderat") == 2
    assert ip.recorded_risk_tier("AGRESIF") == 3
    assert ip.recorded_risk_tier("Sangat Agresif") is None
    assert ip.recorded_risk_tier("") is None
    assert ip.recorded_risk_tier(None) is None


def test_profiling_is_off_unless_a_tenant_opts_in():
    from app.config import Settings

    assert Settings().investor_profiling_enabled is False


def test_the_default_action_space_is_unchanged():
    """Off must mean the model is never offered the tool.

    Every existing tenant runs on `TOOLS`; if this grew a fourth entry, a
    proton or aeon360 conversation could call it. That is why the profiling
    variant is a separate list rather than an append.
    """
    from app.ai import tools

    names = {d.name for d in tools.TOOLS[0].function_declarations}
    assert names == {"send_reply", "escalate_to_ticket", "handoff_to_human"}


def test_the_profiling_tool_cannot_take_a_risk_profile():
    from app.ai import tools

    by_name = {
        d.name: d for d in tools.TOOLS_WITH_PROFILING[0].function_declarations
    }
    assert "record_investor_preference" in by_name

    properties = by_name["record_investor_preference"].parameters.properties
    # The gate stays on the KYC record: the model has no argument for it.
    assert "risk_profile" not in properties
    assert set(properties) == {"goal", "horizon", "drawdown_reaction", "experience"}
