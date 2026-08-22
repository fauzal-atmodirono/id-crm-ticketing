"""The customer profile block appended to the agent-bot decision prompt.

Two invariants matter more than the formatting: an absent or unrecognised
profile yields the empty string (so the prompt is unchanged), and the block
never authorises the model to select a product."""

from __future__ import annotations

from app.services.customer_context import format_customer_context

FULL = {
    "risk_profile": "Moderat",
    "aum_band": "Rp 100-500 juta",
    "rdn_balance": "Rp 12,500,000",
    "holdings": "BBCA, TLKM",
    "days_since_last_transaction": "47",
    "product_gaps": "Obligasi Ritel (ORI), Reksa Dana Saham",
    "next_best_offer": "Reksa Dana Campuran",
    "offer_rationale": "profil risiko moderat dengan portofolio terkonsentrasi",
}


def test_none_yields_empty_string():
    assert format_customer_context(None) == ""


def test_empty_dict_yields_empty_string():
    assert format_customer_context({}) == ""


def test_non_dict_yields_empty_string():
    assert format_customer_context("not a dict") == ""
    assert format_customer_context(["also", "not"]) == ""


def test_unrelated_attributes_yield_empty_string():
    # A tenant whose contacts carry the automotive attribute set must get
    # today's prompt, byte for byte -- not an empty "profile" heading.
    assert format_customer_context({"vehicle_no": "W 1234", "demo_seed": "b1"}) == ""


def test_includes_the_known_profile_fields():
    out = format_customer_context(FULL)
    assert "Moderat" in out
    assert "Rp 100-500 juta" in out
    assert "BBCA, TLKM" in out
    assert "47" in out


def test_includes_the_staged_offer_and_its_rationale():
    out = format_customer_context(FULL)
    assert "Reksa Dana Campuran" in out
    assert "profil risiko moderat" in out


def test_forbids_inventing_or_substituting_a_product():
    out = format_customer_context(FULL)
    lowered = out.lower()
    assert "only" in lowered or "never" in lowered
    assert "do not recommend" in lowered or "not investment advice" in lowered


def test_answers_the_question_first():
    out = format_customer_context(FULL)
    assert "answer" in out.lower()


def test_partial_profile_renders_only_what_is_present():
    out = format_customer_context({"risk_profile": "Konservatif"})
    assert "Konservatif" in out
    assert "AUM" not in out


def test_profile_without_an_offer_has_no_offer_section():
    out = format_customer_context({"risk_profile": "Konservatif", "aum_band": "< Rp 50 juta"})
    assert out != ""
    assert "offer" not in out.lower()


def test_offer_without_a_rationale_still_renders():
    out = format_customer_context({"risk_profile": "Agresif", "next_best_offer": "IPO Subscription"})
    assert "IPO Subscription" in out


def test_blank_values_are_skipped():
    out = format_customer_context({"risk_profile": "Moderat", "aum_band": "   "})
    assert "AUM" not in out


def test_is_deterministic():
    assert format_customer_context(FULL) == format_customer_context(FULL)
