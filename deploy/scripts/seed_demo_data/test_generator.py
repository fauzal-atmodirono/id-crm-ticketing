"""Generated data must match the vocabulary and proportions in the client's own
reports, and must be safe: unique identifiers, non-routable phone numbers."""

from __future__ import annotations

import collections
import json
import re

from generator import canonical_division, generate

MODELS = {"e.MAS 5", "e.MAS 7", "e.MAS 7 PHEV", "NA"}
DIVISIONS = {"Sales", "After Sales", "Apps", "Charging", "Product", "Marketing", "Others"}
CHANNELS = {"whatsapp", "phone", "email", "social"}

# Exact field set accepted by backend/apps/backend/src/chatbot/features/rsa/
# rsa_router.py's incident-create request model.
RSA_ACCEPTED_FIELDS = {
    "incident_date", "vehicle_no", "cause", "vehicle_model", "purchased_from",
    "breakdown_location", "arrived_location", "customer_called_in_time",
    "towing_assigned_time", "time_arrived_breakdown_area", "time_arrived_outlet",
    "total_km", "late_reason", "remarks", "created_by",
}


def test_generates_the_requested_number_of_contacts():
    contacts, _, _ = generate(count=100, batch_id="b1")
    assert len(contacts) == 100


def test_phones_and_plates_are_unique():
    contacts, _, _ = generate(count=100, batch_id="b1")
    assert len({c.phone for c in contacts}) == 100
    assert len({c.vehicle_no for c in contacts}) == 100


def test_plates_use_malaysian_format():
    contacts, _, _ = generate(count=20, batch_id="b1")
    for c in contacts:
        head, tail = c.vehicle_no.split(" ")
        assert head.isalpha() and head.isupper()
        assert tail.isdigit() and 1 <= len(tail) <= 4


def test_vocabulary_matches_the_client_reports():
    contacts, cases, _ = generate(count=100, batch_id="b1")
    assert {c.vehicle_model for c in contacts} <= MODELS
    assert {c.division for c in cases} <= DIVISIONS
    assert {c.channel for c in cases} <= CHANNELS


def test_feedback_is_rare_like_the_real_data():
    _, cases, _ = generate(count=100, batch_id="b1")
    counts = collections.Counter(c.case_type for c in cases)
    assert counts["Feedback"] < counts["Complaint"] < counts["Inquiry"]


def test_some_customers_have_more_than_one_case():
    _, cases, _ = generate(count=100, batch_id="b1")
    per_contact = collections.Counter(c.contact_index for c in cases)
    assert max(per_contact.values()) > 1


def test_generation_is_deterministic():
    a, _, _ = generate(count=30, batch_id="b1")
    b, _, _ = generate(count=30, batch_id="b1")
    assert [c.phone for c in a] == [c.phone for c in b]


def test_two_batches_never_generate_colliding_identities():
    # Chatwoot enforces phone/email/plate uniqueness per ACCOUNT, so seeding
    # the same tenant twice at the default --rng-seed used to regenerate the
    # identical first contact and 422 on create -- killing the run. The
    # runbook's own guidance ("seed a small count first and watch, then go to
    # 100") is exactly that sequence, so batch_id has to be part of the seed.
    a, _, _ = generate(count=30, batch_id="b1")
    b, _, _ = generate(count=30, batch_id="b2")
    assert not ({c.phone for c in a} & {c.phone for c in b})
    assert not ({c.email for c in a} & {c.email for c in b})
    assert not ({c.vehicle_no for c in a} & {c.vehicle_no for c in b})


def test_only_a_minority_of_cases_are_dealer_escalated():
    # Spec §3: "a minority of cases escalated so dealer TAT reporting has
    # rows". Every case having a dealer makes the dealer-TAT view meaningless
    # (it is then just "all cases") -- but zero would leave it empty.
    _, cases, _ = generate(count=100, batch_id="b1")
    with_dealer = [c for c in cases if c.dealer]
    assert with_dealer, "dealer TAT reporting needs at least some escalated cases"
    assert len(with_dealer) < len(cases) / 2


def test_canonical_division_matches_the_warehouse_vocabulary():
    # backend/.../metrics/mapping.py's CATEGORY_TO_DIVISION values, which the
    # live label writer (adapters/chatwoot.py::_classification_labels) feeds
    # from. A seeded "After Sales" that stays "After Sales" would land in its
    # own report bucket beside real traffic's "Aftersales".
    category_to_division_values = {
        "Apps", "Sales", "Aftersales", "Charging", "Product", "Marketing", "Others",
    }
    for division in DIVISIONS:
        assert canonical_division(division) in category_to_division_values
    assert canonical_division("After Sales") == "Aftersales"
    # Every canonical name must also be a KEY of CATEGORY_TO_DIVISION when
    # lowercased, because that is how mapping.py resolves case_category.
    category_to_division_keys = {
        "apps", "app", "sales", "lead", "aftersales", "service",
        "charging", "charger", "product", "marketing", "others",
    }
    for division in DIVISIONS:
        assert canonical_division(division).lower() in category_to_division_keys


def test_unknown_divisions_pass_through_canonicalisation_unchanged():
    assert canonical_division("Something New") == "Something New"


def test_rsa_incidents_reuse_real_plates():
    contacts, _, incidents = generate(count=100, batch_id="b1")
    plates = {c.vehicle_no for c in contacts}
    assert incidents
    assert all(i["vehicle_no"] in plates for i in incidents)


def test_rsa_incidents_carry_the_batch_marker_in_created_by_and_no_stray_keys():
    _, _, incidents = generate(count=100, batch_id="b1")
    assert incidents
    for incident in incidents:
        assert incident["created_by"] == "demo-seed:b1"
        assert set(incident.keys()) <= RSA_ACCEPTED_FIELDS


def test_every_division_in_the_vocabulary_appears():
    _, cases, _ = generate(count=100, batch_id="b1")
    divisions_seen = {c.division for c in cases}
    assert divisions_seen == DIVISIONS


def test_emails_use_the_reserved_invalid_domain():
    contacts, _, _ = generate(count=100, batch_id="b1")
    for c in contacts:
        assert c.email.endswith("@example.invalid")


def test_phones_use_the_reserved_e164_prefix_and_are_not_valid_malaysian_numbers():
    contacts, _, _ = generate(count=100, batch_id="b1")
    for c in contacts:
        assert c.phone.startswith("+999")
        # +999 is ITU-T's permanently unassigned E.164 country code, so no
        # phone here can also parse as a real Malaysian (+60) number.
        assert not re.match(r"^\+60", c.phone)


def test_rsa_incident_payloads_are_json_serializable():
    _, _, incidents = generate(count=100, batch_id="b1")
    assert incidents
    # No `default=` handler: this is what json.dumps(payload) inside
    # httpx.post(json=payload) does, so if any field is still a raw
    # datetime this raises TypeError before the assertion below runs.
    serialized = json.dumps(incidents)
    assert json.loads(serialized) == incidents
