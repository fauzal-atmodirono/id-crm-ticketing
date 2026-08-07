"""Correlation-token extraction and quoted-trail stripping."""

from app.services.escalation_replies import extract_case_id, strip_quoted_trail


def test_extracts_from_to_header():
    msg = {"content_attributes": {"email": {"to": ["support+case42@test"]}}}
    assert extract_case_id(msg) == 42


def test_extracts_from_cc_header():
    msg = {"content_attributes": {"email": {"cc": ["support+case7@test"]}}}
    assert extract_case_id(msg) == 7


def test_falls_back_to_subject_tag():
    msg = {"content_attributes": {"email": {"subject": "Re: [CASE-99] my car"}}}
    assert extract_case_id(msg) == 99


def test_to_header_wins_over_subject():
    msg = {"content_attributes": {
        "email": {"to": ["support+case42@test"], "subject": "Re: [CASE-99] x"}
    }}
    assert extract_case_id(msg) == 42


def test_returns_none_without_a_token():
    assert extract_case_id({"content_attributes": {"email": {"to": ["support@test"]}}}) is None
    assert extract_case_id({}) is None
    assert extract_case_id({"content_attributes": {"email": {"to": "not-a-list"}}}) is None


def test_strips_gmail_style_trail():
    body = "We fixed it.\n\nOn Thu, 6 Aug 2026 at 10:00, Support <s@t> wrote:\n> original\n> more"
    assert strip_quoted_trail(body) == "We fixed it."


def test_strips_outlook_style_trail():
    body = "Parts ordered.\r\n\r\n-----Original Message-----\r\nFrom: Support\r\nsomething"
    assert strip_quoted_trail(body) == "Parts ordered."


def test_strips_leading_quote_block():
    assert strip_quoted_trail("Done.\n\n> quoted line\n> another") == "Done."


def test_leaves_clean_body_untouched():
    assert strip_quoted_trail("Just a reply.") == "Just a reply."


def test_never_returns_empty_when_input_is_only_a_trail():
    body = "On Thu, 6 Aug 2026 at 10:00, Support <s@t> wrote:\n> original"
    assert strip_quoted_trail(body) == body.strip()
