"""Synthetic nasabah must be deterministic, non-routable, and suitable:
the offer attached to a profile is chosen by risk profile, not by an LLM,
so a Konservatif customer can never be carrying an equity offer."""

from __future__ import annotations

import random
import re

from nasabah import (
    RISK_PROFILES,
    DemoNasabah,
    generate_nasabah,
    offer_for,
)

CONSERVATIVE_ONLY = {"Reksa Dana Pasar Uang", "Obligasi Ritel (ORI)"}
MODERATE_ONLY = {"Reksa Dana Campuran", "Obligasi Korporasi"}
AGGRESSIVE_ONLY = {"Reksa Dana Saham", "IPO Subscription"}
SUITABLE_BY_RISK = {
    "Konservatif": CONSERVATIVE_ONLY,
    "Moderat": MODERATE_ONLY,
    "Agresif": AGGRESSIVE_ONLY,
}

# The tracked product universe (mirrors nasabah._PRODUCTS). "IPO Subscription"
# is deliberately not part of it -- it can never be a `held_products` entry,
# so it can never collide with something a nasabah already holds.
_TRACKED_PRODUCTS = {
    "Saham",
    "Reksa Dana Pasar Uang",
    "Reksa Dana Campuran",
    "Reksa Dana Saham",
    "Obligasi Ritel (ORI)",
    "Obligasi Korporasi",
}


def test_generates_the_requested_number_of_nasabah():
    people = generate_nasabah(25, batch_id="b1")
    assert len(people) == 25
    assert all(isinstance(p, DemoNasabah) for p in people)


def test_generation_is_deterministic_per_batch():
    a = generate_nasabah(10, batch_id="b1")
    b = generate_nasabah(10, batch_id="b1")
    assert a == b


def test_two_batches_never_generate_colliding_identities():
    a = generate_nasabah(20, batch_id="b1")
    b = generate_nasabah(20, batch_id="b2")
    assert not ({p.phone for p in a} & {p.phone for p in b})
    assert not ({p.email for p in a} & {p.email for p in b})


def test_phones_are_unique_and_non_routable():
    people = generate_nasabah(50, batch_id="b1")
    phones = [p.phone for p in people]
    assert len(set(phones)) == len(phones)
    assert all(re.fullmatch(r"\+999\d{9}", p) for p in phones)


def test_names_are_visibly_synthetic():
    people = generate_nasabah(10, batch_id="b1")
    assert all(p.name.startswith("[DEMO] ") for p in people)


def test_risk_profiles_come_from_the_known_set():
    people = generate_nasabah(50, batch_id="b1")
    assert {p.risk_profile for p in people} <= set(RISK_PROFILES)


def test_offer_respects_suitability():
    # The whole point of selecting the offer in code: a conservative
    # investor must never be carrying an equity or IPO offer, no matter
    # what the language model is later asked to say.
    people = generate_nasabah(200, batch_id="b1")
    for p in people:
        if p.risk_profile == "Konservatif":
            assert p.next_best_offer not in AGGRESSIVE_ONLY
        if p.risk_profile == "Agresif":
            assert p.next_best_offer not in CONSERVATIVE_ONLY


def test_offer_for_is_deterministic_given_the_same_rng():
    a = offer_for("Moderat", random.Random("x"))
    b = offer_for("Moderat", random.Random("x"))
    assert a == b


def test_every_nasabah_has_a_rationale_for_their_offer():
    people = generate_nasabah(30, batch_id="b1")
    assert all(p.offer_rationale.strip() for p in people)


def test_holdings_and_gaps_do_not_overlap():
    people = generate_nasabah(50, batch_id="b1")
    for p in people:
        assert not (set(p.holdings) & set(p.product_gaps))


def test_pinned_phone_replaces_the_first_nasabah_number():
    people = generate_nasabah(10, batch_id="b1", pinned_phone="+628123456789")
    assert people[0].phone == "+628123456789"
    assert all(p.phone.startswith("+999") for p in people[1:])


def test_pinned_name_is_used_and_still_marked_demo():
    people = generate_nasabah(5, batch_id="b1", pinned_phone="+628123456789", pinned_name="Budi Santoso")
    assert people[0].name == "[DEMO] Budi Santoso"


def test_pinning_does_not_change_the_other_nasabah():
    plain = generate_nasabah(10, batch_id="b1")
    pinned = generate_nasabah(10, batch_id="b1", pinned_phone="+628123456789")
    assert plain[1:] == pinned[1:]


# --- Amendment: next-best offer must prefer a product not already held ----


def test_offer_for_excludes_a_held_product_but_falls_back_when_everything_suitable_is_held():
    # Constructed directly rather than hoping a generated population
    # produces the collision by chance: Konservatif has exactly two
    # suitable offers, so both branches of the exclusion (something left
    # to exclude to / nothing left, must fall back) are easy to force and
    # cheap to run hundreds of times.
    rnd = random.Random("exclude-direct")
    for _ in range(200):
        offer, rationale = offer_for(
            "Konservatif", rnd, exclude=frozenset({"Reksa Dana Pasar Uang"})
        )
        assert offer == "Obligasi Ritel (ORI)"
        assert rationale.strip()

    for _ in range(200):
        # Holding every suitable product must fall back to the unfiltered
        # risk-appropriate catalogue -- never widen to another profile's.
        offer, _ = offer_for(
            "Konservatif",
            rnd,
            exclude=frozenset({"Reksa Dana Pasar Uang", "Obligasi Ritel (ORI)"}),
        )
        assert offer in CONSERVATIVE_ONLY


def test_generated_nasabah_are_never_offered_a_tracked_product_they_already_hold():
    # Integration-level check that _make_nasabah actually wires held_products
    # into the exclusion (not just that offer_for's `exclude` param works in
    # isolation). product_gaps is the complement of held_products within the
    # tracked product universe, so held_products is reconstructed from it
    # rather than reaching into the module's private state.
    people = generate_nasabah(500, batch_id="offer-exclusion-check")
    saw_a_fallback_case = False
    for p in people:
        if p.next_best_offer not in _TRACKED_PRODUCTS:
            continue  # e.g. "IPO Subscription" is never a held product
        held = _TRACKED_PRODUCTS - set(p.product_gaps)
        suitable = SUITABLE_BY_RISK.get(p.risk_profile, CONSERVATIVE_ONLY)
        if p.next_best_offer in held:
            saw_a_fallback_case = True
            assert held >= suitable, (
                f"{p.name} was offered {p.next_best_offer!r}, which they "
                f"already hold, without holding every suitable product for "
                f"{p.risk_profile}"
            )
        else:
            assert p.next_best_offer not in held
    assert saw_a_fallback_case, (
        "this population never produced a nasabah holding every suitable "
        "product, so the fallback branch was never exercised -- widen the "
        "population or batch_id"
    )


# --- Amendment: product_gaps must never leak a risk-inappropriate product -


def test_gaps_for_never_includes_a_product_outside_the_profiles_catalogue():
    # Constructed directly, not sampled from a population: a Konservatif
    # nasabah who holds nothing at all would, under the old (unfiltered)
    # implementation, have every other _PRODUCTS entry in their gaps --
    # including "Reksa Dana Saham", an Agresif-only catalogue entry, and
    # "Reksa Dana Campuran" / "Obligasi Korporasi", both Moderat-only. That
    # is exactly the leak design spec §7.4 forbids reaching the model's
    # context: the candidate set the model sees must be filtered before it
    # gets there, not after.
    from nasabah import _gaps_for  # noqa: PLC0415 -- private, test-only import

    gaps = _gaps_for("Konservatif", held_products=[])
    assert set(gaps) == CONSERVATIVE_ONLY
    assert "Reksa Dana Saham" not in gaps
    assert "Reksa Dana Campuran" not in gaps
    assert "Obligasi Korporasi" not in gaps
    assert "Saham" not in gaps


def test_gaps_for_shrinks_as_suitable_products_are_held():
    from nasabah import _gaps_for

    assert _gaps_for("Konservatif", held_products=["Reksa Dana Pasar Uang"]) == [
        "Obligasi Ritel (ORI)"
    ]
    assert (
        _gaps_for(
            "Konservatif",
            held_products=["Reksa Dana Pasar Uang", "Obligasi Ritel (ORI)"],
        )
        == []
    )


def test_gaps_for_falls_back_to_conservative_for_an_unknown_risk_profile():
    from nasabah import _gaps_for

    assert set(_gaps_for("nonsense-profile", held_products=[])) == CONSERVATIVE_ONLY


def test_generated_nasabah_never_carry_an_out_of_profile_product_gap():
    # Integration-level check that _make_nasabah actually wires the filter
    # in (not just that _gaps_for works in isolation).
    people = generate_nasabah(300, batch_id="gap-suitability-integration")
    exercised = {profile: False for profile in RISK_PROFILES}
    for p in people:
        suitable = SUITABLE_BY_RISK.get(p.risk_profile, CONSERVATIVE_ONLY)
        if p.product_gaps:
            exercised[p.risk_profile] = True
        assert set(p.product_gaps) <= suitable, (
            f"{p.name} ({p.risk_profile}) has {p.product_gaps!r}, which "
            f"includes a product outside {suitable}"
        )
    assert all(exercised.values()), (
        "this population never produced a non-empty product_gaps for every "
        "risk profile, so the assertion above could have passed vacuously "
        "-- widen the population or batch_id"
    )
