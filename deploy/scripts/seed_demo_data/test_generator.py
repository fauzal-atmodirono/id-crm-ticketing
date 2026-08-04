"""Generated data must match the vocabulary and proportions in the client's own
reports, and must be safe: unique identifiers, non-routable phone numbers."""

from __future__ import annotations

import collections
import json
import re

from generator import generate

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
