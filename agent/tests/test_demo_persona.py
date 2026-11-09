"""Slug detection has to be conservative in both directions: it must fire on a
deliberate trailing [slug], and must not fire on ordinary chat that happens to
contain brackets. A false positive silently rewrites a customer record."""

from __future__ import annotations

from app.services.demo_persona import (
    PROFILES,
    attributes_for,
    detect_slug,
    display_name_for,
    strip_slug,
)


def test_detects_each_known_slug():
    for slug in PROFILES:
        assert detect_slug(f"halo [{slug}]") == slug


def test_slug_is_case_insensitive():
    assert detect_slug("halo [KONSERVATIF]") == "konservatif"


def test_trailing_whitespace_is_tolerated():
    assert detect_slug("halo [agresif]   ") == "agresif"


def test_unknown_slug_returns_none():
    # Must be None, not a blank profile: an unrecognised tag leaves the
    # conversation exactly as it was.
    assert detect_slug("halo [tidakada]") is None


def test_slug_must_be_trailing():
    # The whole point of anchoring: ordinary chat containing brackets must not
    # repoint a customer record.
    assert detect_slug("[moderat] halo") is None
    assert detect_slug("saya lihat [moderat] di aplikasi, itu apa?") is None


def test_no_slug_returns_none():
    assert detect_slug("berapa saldo RDN saya?") is None


def test_non_string_returns_none():
    assert detect_slug(None) is None
    assert detect_slug(42) is None
    assert detect_slug({"a": 1}) is None


def test_strip_removes_the_slug():
    assert strip_slug("berapa saldo saya? [konservatif]") == "berapa saldo saya?"


def test_strip_leaves_text_without_a_slug_alone():
    assert strip_slug("berapa saldo saya?") == "berapa saldo saya?"


def test_strip_handles_non_string():
    assert strip_slug(None) == ""


def test_attributes_exclude_the_display_name():
    attrs = attributes_for("moderat")
    assert attrs is not None
    assert "name" not in attrs
    assert attrs["risk_profile"] == "Moderat"


def test_attributes_for_unknown_slug_is_none():
    assert attributes_for("nope") is None


def test_display_name_is_marked_demo():
    for slug in PROFILES:
        assert display_name_for(slug).startswith("[DEMO] ")


def test_every_profile_carries_the_full_attribute_contract():
    # These keys are shared with the seeder and the prompt formatter. A missing
    # one does not error anywhere -- it silently empties a sidebar row.
    required = {
        "risk_profile",
        "aum_band",
        "rdn_balance",
        "holdings",
        "holdings_sectors",
        "days_since_last_transaction",
        "product_gaps",
        "next_best_offer",
        "offer_rationale",
    }
    for slug in PROFILES:
        assert set(attributes_for(slug)) == required, slug


def test_sector_breakdown_covers_every_holding():
    # `holdings_sectors` is hand-maintained here (the seeder derives it via
    # `nasabah.sectors_for`, which this package deliberately does not import).
    # The realistic drift is editing a persona's tickers and forgetting the
    # sector line, which leaves the prompt stating two different portfolios
    # three lines apart -- so pin coverage rather than the exact grouping.
    for slug, profile in PROFILES.items():
        holdings = profile["holdings"]
        sectors = profile["holdings_sectors"]
        if holdings == "Tidak ada":
            assert sectors == "Tidak ada", slug
            continue
        for ticker in (t.strip() for t in holdings.split(",")):
            assert ticker in sectors, (slug, ticker)


def test_offers_respect_suitability():
    # The same invariant the seeder enforces: a conservative persona must never
    # be carrying an aggressive product as its offer.
    aggressive = {"Reksa Dana Saham", "IPO Subscription"}
    conservative = {"Reksa Dana Pasar Uang", "Obligasi Ritel (ORI)"}
    assert PROFILES["konservatif"]["next_best_offer"] not in aggressive
    assert PROFILES["agresif"]["next_best_offer"] not in conservative


def test_product_gaps_stay_inside_the_personas_own_risk_band():
    # product_gaps is rendered into the prompt, so an unsuitable product listed
    # there sits one sentence away from being said out loud.
    aggressive = {"Reksa Dana Saham", "IPO Subscription"}
    gaps = PROFILES["konservatif"]["product_gaps"]
    assert not any(p in gaps for p in aggressive)
