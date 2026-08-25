"""The customer profile block appended to the agent-bot decision prompt.

Three invariants matter more than the formatting: an absent or unrecognised
profile yields the empty string (so the prompt is unchanged), the block never
lets the model reach outside the suitability-checked set, and it never
authorises investment advice.

The middle one changed on 2026-08-25. It used to be "the model may name
exactly one product". A live replay (`deploy/scripts/bahana_replay.py`) showed
every persona dead-ending on the same turn, with the model citing that rule as
its reason for giving up -- so the boundary moved from *one product* to *the
eligible set*, which is still chosen by code and still suitability-checked.
See `_ELIGIBLE_INSTRUCTIONS` for why that is the same guarantee, not a weaker
one."""

from __future__ import annotations

from app.services.customer_context import format_customer_context

FULL = {
    "risk_profile": "Moderat",
    "aum_band": "Rp 100-500 juta",
    "rdn_balance": "Rp 12,500,000",
    "holdings": "BBCA, TLKM",
    "holdings_sectors": "Keuangan (BBCA), Infrastruktur (TLKM)",
    "days_since_last_transaction": "47",
    "product_gaps": "Obligasi Korporasi, Reksa Dana Campuran",
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


def test_includes_the_holdings_sectors():
    # Concentration is a sector fact, not a ticker fact: "two of your three
    # holdings are banks" is the advisory insight, and it is useless if the
    # model only ever sees the tickers.
    out = format_customer_context(FULL)
    assert "Keuangan (BBCA), Infrastruktur (TLKM)" in out


def test_includes_the_staged_offer_and_its_rationale():
    out = format_customer_context(FULL)
    assert "Reksa Dana Campuran" in out
    assert "profil risiko moderat" in out


def test_eligible_alternatives_exclude_the_staged_offer():
    # The alternatives are `product_gaps` minus the offer already named above.
    # Listing the offer twice would read as two separate products and invite
    # the model to pitch it as if it were a second option.
    out = format_customer_context(FULL)
    alternatives = out.split("## Other products")[1]
    assert "Obligasi Korporasi" in alternatives
    assert "Reksa Dana Campuran" not in alternatives


def test_forbids_reaching_outside_the_eligible_set():
    # Pin the specific regulatory clauses, not a loose "only"/"never"
    # disjunction -- a future edit that drops the containment or the
    # no-quoting language must fail this test, not slip through it.
    out = format_customer_context(FULL).lower()
    assert "never name, invent, or imply a product" in out
    assert "never quote a return, yield, price, or fee" in out
    assert "not investment advice" in out
    assert "do not recommend buying or selling" in out


def test_permits_pivoting_within_the_eligible_set():
    # The behaviour this module was changed to allow. Without an explicit
    # permission the model treats the staged offer as the only legal product
    # and hands off the moment the customer declines it.
    out = format_customer_context(FULL).lower()
    assert "may name any product from the eligible list" in out


def test_offers_a_review_instead_of_closing_the_topic():
    # The dead-end turn: customer wants something the suitability rule does
    # not allow. Saying so and offering an RM review is the correct answer;
    # "is there anything else?" is the failure this module is fixing.
    out = format_customer_context(FULL).lower()
    assert "relationship manager review" in out
    assert "do not simply close the topic" in out


def test_forbids_reciting_the_profile_as_a_list():
    out = format_customer_context(FULL).lower()
    assert "do not read it back as a list" in out


def test_answers_the_question_first():
    out = format_customer_context(FULL)
    assert "answer the customer's actual question first" in out.lower()


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


def test_offer_without_alternatives_has_no_alternatives_section():
    # A nasabah whose only eligible product is the one already staged. The
    # heading must not appear with nothing under it.
    out = format_customer_context(
        {
            "risk_profile": "Moderat",
            "next_best_offer": "Reksa Dana Campuran",
            "product_gaps": "Reksa Dana Campuran",
        }
    )
    assert "Reksa Dana Campuran" in out
    assert "## Other products" not in out


def test_the_no_gaps_sentinel_yields_no_alternatives():
    # Both writers of this attribute (the seeder and the v_nasabah_profile
    # view) spell "none" as the literal string below, not as an empty value.
    out = format_customer_context(
        {
            "risk_profile": "Moderat",
            "next_best_offer": "Reksa Dana Campuran",
            "product_gaps": "Tidak ada",
        }
    )
    assert "## Other products" not in out


def test_blank_values_are_skipped():
    out = format_customer_context({"risk_profile": "Moderat", "aum_band": "   "})
    assert "AUM" not in out


def test_is_deterministic():
    assert format_customer_context(FULL) == format_customer_context(FULL)


def test_int_value_is_stringified():
    out = format_customer_context({"days_since_last_transaction": 47})
    assert "47" in out


def test_whitespace_only_offer_is_treated_as_absent():
    out = format_customer_context({"risk_profile": "Moderat", "next_best_offer": "   "})
    assert "offer" not in out.lower()
