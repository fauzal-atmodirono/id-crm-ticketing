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
