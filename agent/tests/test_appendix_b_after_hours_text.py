"""P1 task 9 — the deployed after-hours wording must match Appendix B.

`deploy/scripts/appendix-b-after-hours-text.json` is what the provisioning
script writes to every text-channel inbox. This test is the double entry: the
expected strings below were transcribed from the appendix independently of that
file, so neither can drift without the other noticing.

Re-extract the source of truth with:

    uv run python - <<'PY'
    import json, openpyxl
    wb = openpyxl.load_workbook(
        "docs/client-materials/RFP 2026_028/APPENDIX B - Call Handling Process Flow.xlsx",
        data_only=True)
    print(json.dumps(wb["WhatsApp Process"]["B18"].value))
    print(json.dumps(wb["Email"]["A11"].value))
    PY

The double spaces and trailing whitespace are the client's and are deliberate.
Tidying them would make this test assert something Appendix B does not say.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

TEXT_FILE = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "scripts"
    / "appendix-b-after-hours-text.json"
)

EXPECTED_AFTER_HOURS = (
    "Thank you for reaching out. Our customer service team is  \n"
    "currently unavailable as it's outside of our operating hours \n"
    "Mon–Fri: 8:30 AM – 5:30 PM \n"
    "Sat–Sun & Public Holidays: 9:00 AM – 5:00 PM \n"
    "To learn more about our product and services, you may visit our website at "
    "emas.proton.com or social media, at PROTON e.MAS Cars. We will respond to "
    "your inquiry as soon as we resume operations. Thank you for your patience. "
)

EXPECTED_EMAIL_ACK = (
    "Dear Customer,\n"
    "Thank you for your email. This message serves to acknowledge receipt of "
    "your enquiry.\n"
    "We will respond within one (1) business day during our operating hours.\n"
    "For urgent matters, please contact our Call Centre at 1300 888 877.\n"
    "Operating Hours:\n"
    "Monday–Friday: 8:30 AM – 5:30 PM\n"
    "Saturday, Sunday & Public Holidays: 9:00 AM – 5:00 PM\n"
    "Thank you for your patience and understanding.\n"
    "\n"
    "Warm regards,\n"
    "Proton e.MAS Centre"
)


@pytest.fixture(scope="module")
def texts() -> dict:
    return json.loads(TEXT_FILE.read_text(encoding="utf-8"))


def test_the_english_after_hours_text_matches_appendix_b_verbatim(texts):
    assert texts["after_hours_reply"]["en"] == EXPECTED_AFTER_HOURS


def test_the_email_acknowledgement_matches_appendix_b_verbatim(texts):
    assert texts["email_auto_acknowledgement"]["en"] == EXPECTED_EMAIL_ACK


def test_no_malay_variant_is_invented(texts):
    """Appendix B is English-only. A `ms` key here would mean someone wrote
    customer-facing Malay the client never approved."""
    assert "ms" not in texts["after_hours_reply"]
    assert "ms" not in texts["email_auto_acknowledgement"]


def test_the_operating_hours_match_the_text_customers_are_shown():
    """The stated hours and the configured hours are the same fact. If they
    diverge, the working-hours SLA clock measures against a calendar that
    contradicts what the customer was told."""
    hours = json.loads(TEXT_FILE.read_text(encoding="utf-8"))["operating_hours"]
    assert hours["weekday"]["open"] == "08:30"
    assert hours["weekday"]["close"] == "17:30"
    assert hours["weekend_and_public_holidays"]["open"] == "09:00"
    assert hours["weekend_and_public_holidays"]["close"] == "17:00"

    text = EXPECTED_AFTER_HOURS
    assert "8:30 AM – 5:30 PM" in text
    assert "9:00 AM – 5:00 PM" in text


def test_the_provisioning_script_defaults_to_a_dry_run():
    script = TEXT_FILE.with_name("provision-after-hours-replies.py").read_text(
        encoding="utf-8"
    )
    assert '"--apply", action="store_true"' in script
    assert "if not args.apply:" in script
