"""The contact custom_attributes a seeded nasabah carries. These keys are the
contract with two consumers that never import this module: Chatwoot's contact
attribute definitions (which render them in the agent sidebar) and the agent
service's customer_context formatter. Renaming a key here silently empties
both."""

from __future__ import annotations

from client import build_nasabah_custom_attributes
from nasabah import generate_nasabah

EXPECTED_KEYS = {
    "demo_seed",
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


def test_carries_exactly_the_agreed_keys():
    nasabah = generate_nasabah(1, batch_id="b1")[0]
    attrs = build_nasabah_custom_attributes(nasabah, "b1")
    assert set(attrs) == EXPECTED_KEYS


def test_stamps_the_purge_marker():
    nasabah = generate_nasabah(1, batch_id="b1")[0]
    attrs = build_nasabah_custom_attributes(nasabah, "b1")
    assert attrs["demo_seed"] == "b1"


def test_every_value_is_a_string():
    # Chatwoot custom attribute values round-trip as strings; a list or int
    # sent here comes back in a shape the sidebar renders as "[object]".
    nasabah = generate_nasabah(1, batch_id="b1")[0]
    attrs = build_nasabah_custom_attributes(nasabah, "b1")
    assert all(isinstance(v, str) for v in attrs.values())


def test_lists_are_comma_joined():
    people = generate_nasabah(40, batch_id="b1")
    with_holdings = next(p for p in people if p.holdings)
    attrs = build_nasabah_custom_attributes(with_holdings, "b1")
    assert attrs["holdings"] == ", ".join(with_holdings.holdings)


def test_empty_holdings_render_as_an_explicit_phrase_not_blank():
    # A blank sidebar field is ambiguous between "no equities" and "we don't
    # know". The AI reads these values verbatim, so say which one it is.
    people = generate_nasabah(60, batch_id="b1")
    without = next(p for p in people if not p.holdings)
    attrs = build_nasabah_custom_attributes(without, "b1")
    assert attrs["holdings"] == "Tidak ada"


def test_rdn_balance_is_formatted_for_a_human_reader():
    nasabah = generate_nasabah(1, batch_id="b1")[0]
    attrs = build_nasabah_custom_attributes(nasabah, "b1")
    assert attrs["rdn_balance"].startswith("Rp ")
    # rdn_balance is always >= 500_000, so the thousands separator is not optional
    assert "," in attrs["rdn_balance"]
