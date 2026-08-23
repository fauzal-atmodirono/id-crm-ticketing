"""Demo-only: let a trailing `[slug]` in a customer message repoint the
contact at a different nasabah persona, mid-conversation.

Why this exists
---------------
A demo has to show that the AI's answer is driven by the customer's stored
profile. The most direct proof is to change the profile and ask the same
question again. But a WhatsApp number has exactly one inbound webhook and
Chatwoot resolves an inbound message to a contact **by phone number**, so one
handset is one contact, permanently — several personas cannot arrive by phone.

The alternative was an operator running a CLI between beats, which works but
puts a terminal in the middle of a customer-facing demo. This lets the persona
ride in the message itself, the way `apac-aeon360-foundry-prototype` does it.

**The slug rewrites the contact record, not just the prompt.** That distinction
is the whole design. Overriding only what the model sees would leave the CRM
sidebar showing the previous persona while the bot answered as the new one —
on stage, with the sidebar visible, that reads as a bug rather than a feature.
Writing the record keeps the sidebar, the warehouse projection and the AI
telling one story.

Scope
-----
Off unless `DEMO_PERSONA_SLUGS_ENABLED` is true, and it never fires on a
message that does not end in a known slug — so a tenant that has not opted in,
or a customer who happens to type brackets, is unaffected.

The profiles here intentionally mirror
`deploy/scripts/bahana_demo_profile.py`. They are demo fixtures, not product
data: real personalization comes from the CRM record, which is exactly what
this module writes to. Keep the two in step, or the CLI and the slug will
disagree about who Sari Wijaya is.
"""

from __future__ import annotations

import re

# Trailing `[slug]` only, optionally followed by whitespace. Anchoring to the
# end is what stops ordinary chat containing brackets from switching persona --
# the same rule AEON360 uses, and for the same reason.
_SLUG_RE = re.compile(r"\[([a-z0-9][a-z0-9_-]*)\]\s*$", re.IGNORECASE)

# Keys must match client.py::build_nasabah_custom_attributes and
# customer_context.py::_PROFILE_FIELDS exactly. A typo does not error; it
# silently empties that row of the agent sidebar and drops the field from the
# prompt.
PROFILES: dict[str, dict[str, str]] = {
    "moderat": {
        "name": "[DEMO] Budi Santoso",
        "risk_profile": "Moderat",
        "aum_band": "Rp 100-500 juta",
        "rdn_balance": "Rp 46,000,000",
        "holdings": "BBCA, BBRI, TLKM",
        "days_since_last_transaction": "190",
        "product_gaps": "Obligasi Korporasi, Reksa Dana Campuran",
        "next_best_offer": "Reksa Dana Campuran",
        "offer_rationale": (
            "profil risiko moderat dengan portofolio yang terkonsentrasi pada "
            "satu kelas aset"
        ),
    },
    "konservatif": {
        "name": "[DEMO] Sari Wijaya",
        "risk_profile": "Konservatif",
        "aum_band": "Rp 50-100 juta",
        "rdn_balance": "Rp 82,500,000",
        "holdings": "Tidak ada",
        "days_since_last_transaction": "312",
        "product_gaps": "Obligasi Ritel (ORI), Reksa Dana Pasar Uang",
        "next_best_offer": "Reksa Dana Pasar Uang",
        "offer_rationale": (
            "profil risiko konservatif dengan saldo RDN menganggur cukup besar "
            "dan belum ditempatkan pada produk apa pun"
        ),
    },
    "agresif": {
        "name": "[DEMO] Rizki Pratama",
        "risk_profile": "Agresif",
        "aum_band": "> Rp 1 miliar",
        "rdn_balance": "Rp 240,000,000",
        "holdings": "ANTM, BBRI, ICBP, PGAS",
        "days_since_last_transaction": "3",
        "product_gaps": "IPO Subscription, Reksa Dana Saham",
        "next_best_offer": "Reksa Dana Saham",
        "offer_rationale": (
            "profil risiko agresif yang sudah sangat aktif di saham namun belum "
            "terdiversifikasi lewat reksa dana"
        ),
    },
}


def detect_slug(text: object) -> str | None:
    """The persona slug at the end of `text`, or None.

    Accepts `object` rather than `str` because this is fed from a JSON message
    body where a null or a non-string can appear. Returns None for anything
    that is not a recognised slug, so an unknown `[foo]` leaves the
    conversation exactly as it was rather than blanking the profile.
    """
    if not isinstance(text, str):
        return None
    match = _SLUG_RE.search(text)
    if match is None:
        return None
    slug = match.group(1).lower()
    return slug if slug in PROFILES else None


def strip_slug(text: object) -> str:
    """`text` with a trailing persona slug removed.

    The slug is an operator control, not something the customer said. Leaving
    it in the history means the model sees `[konservatif]` in the transcript
    and may respond to it -- answering the command instead of the question.
    """
    if not isinstance(text, str):
        return ""
    return _SLUG_RE.sub("", text).strip()


def attributes_for(slug: str) -> dict[str, str] | None:
    """The contact custom_attributes for a persona, without its display name.

    Returns None for an unknown slug. `name` is split out because Chatwoot
    takes it as a top-level field on the contact, not as a custom attribute.
    """
    profile = PROFILES.get(slug)
    if profile is None:
        return None
    return {k: v for k, v in profile.items() if k != "name"}


def display_name_for(slug: str) -> str | None:
    """The contact's display name for a persona, or None if unknown."""
    profile = PROFILES.get(slug)
    return profile.get("name") if profile else None
