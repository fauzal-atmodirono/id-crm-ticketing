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


def test_does_not_strip_from_mid_sentence():
    """From: at line start in prose should not be treated as email header."""
    body = ("The delay was due to a backorder.\n\n"
            "From: the parts team's perspective, this could have been avoided "
            "if we had stocked the part.\n\n"
            "We will follow up tomorrow with an ETA.")
    assert strip_quoted_trail(body) == body.strip()


def test_does_not_strip_quote_mid_reply():
    """Quoted lines in the middle of a reply should not be stripped."""
    body = ("See below:\n"
            "> Customer said the car makes a rattling noise.\n"
            "We checked and diagnosed it as a loose heat shield, now fixed.\n\n"
            "Regards,\nDealer")
    assert strip_quoted_trail(body) == body.strip()


def test_strips_outlook_header_block():
    """Outlook-style forwarded header block should be stripped."""
    body = ("We have a solution.\n\n"
            "-----Original Message-----\n"
            "From: support@example.com\n"
            "Sent: Thursday, August 6, 2026\n"
            "To: dealer@example.com\n"
            "Subject: Your car is ready\n\n"
            "Original message text here")
    assert strip_quoted_trail(body) == "We have a solution."


def test_strips_contiguous_quote_block_at_end():
    """Quoted lines that run to the end should be stripped."""
    body = ("Here is the analysis:\n\n"
            "> This was the original message\n"
            "> from the support team")
    assert strip_quoted_trail(body) == "Here is the analysis:"


def test_preserves_quote_with_content_after():
    """Quoted lines followed by unquoted content should be preserved."""
    body = ("See the original:\n"
            "> Original message here\n"
            "\n"
            "That's what we're replying to.\n"
            "Our response continues...")
    assert strip_quoted_trail(body) == body.strip()
