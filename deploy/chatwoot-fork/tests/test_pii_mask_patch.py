"""Fork patch 0085 (Wave 2 Task 29, FIX-49, RFP 2026_028 2.2.5): client-side
PII masking in Chatwoot's contact views. Content-level checks on the patch's
added lines; the chain-apply against upstream v4.15.1 is run by the SDD task."""
from pathlib import Path

PATCH = Path(__file__).resolve().parent.parent / "patches" / "0085-pii-mask-contact-views.patch"
TEXT = PATCH.read_text()
LINES = TEXT.split("\n")
ADDED = "\n".join(l[1:] for l in LINES if l.startswith("+") and not l.startswith("+++"))


def _section(name: str) -> str:
    return TEXT.split(f"diff --git a/{name}", 1)[1].split("diff --git", 1)[0]


def test_asks_the_backend_privacy_route_and_masks_only_on_explicit_yes():
    # backend: features/privacy/access.py GET /privacy/me
    assert "adminRequest('/privacy/me')" in ADDED
    assert "data.masking_enabled && data.masked" in ADDED
    # a failure never masks (flag off / older backend = unchanged views)
    assert ".catch(() => {\n      masked.value = false;" in ADDED


def test_sidebar_rows_are_masked_and_not_copyable_or_editable():
    s = _section("app/javascript/dashboard/routes/dashboard/conversation/contact/ContactInfo.vue")
    assert ":value=\"maskPii(contact.email, 'email')\"" in s
    assert ":value=\"maskPii(contact.phone_number, 'phone')\"" in s
    assert ":value=\"maskPii(contact.identifier, 'identifier')\"" in s
    assert s.count(':show-copy="!piiMasked"') == 2
    assert s.count(':editable="!piiMasked"') == 2
    assert '+          v-if="!piiMasked"' in s  # edit-contact modal button


def test_contact_list_masks_and_hides_the_inline_edit_form():
    s = _section("app/javascript/dashboard/components-next/Contacts/ContactsCard/ContactsCard.vue")
    assert "{{ maskPii(email, 'email') }}" in s
    assert "{{ maskPii(phoneNumber, 'phone') }}" in s
    assert '+        v-if="!piiMasked"' in s


def test_custom_attributes_masked_read_only_and_never_saved_back():
    s = _section(
        "app/javascript/dashboard/routes/dashboard/conversation/customAttributes/CustomAttributes.vue"
    )
    assert ':value="piiValue(element)"' in s
    assert ':show-actions="!piiLocked(element)"' in s
    assert "if (piiMasked.value && /•|\\*{6}-\\*{2}-/.test(String(value ?? ''))) return;" in s


def test_display_format_matches_the_backend():
    # features/privacy/pii.py: +60•••••6789, s•••@gmail.com, ******-**-5678
    assert "const MASK = '•';" in ADDED
    assert "`******-**-${digits.slice(-4)}`" in ADDED
    assert "MASK.repeat(digits.length - 6) + digits.slice(-4)" in ADDED


def test_no_raw_html_is_injected():
    assert "v-html" not in ADDED
