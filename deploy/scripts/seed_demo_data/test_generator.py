"""Generated data must match the vocabulary and proportions in the client's own
reports, and must be safe: unique identifiers, non-routable phone numbers."""

from __future__ import annotations

import collections

from generator import generate

MODELS = {"e.MAS 5", "e.MAS 7", "e.MAS 7 PHEV", "NA"}
DIVISIONS = {"Sales", "After Sales", "Apps", "Charging", "Product", "Marketing", "Others"}
CHANNELS = {"whatsapp", "phone", "email", "social"}


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


def test_rsa_incidents_reuse_real_plates():
    contacts, _, incidents = generate(count=100, batch_id="b1")
    plates = {c.vehicle_no for c in contacts}
    assert incidents
    assert all(i["vehicle_no"] in plates for i in incidents)
