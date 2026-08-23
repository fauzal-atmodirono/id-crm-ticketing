#!/usr/bin/env python3
"""Repoint the Bahana demo handset at a different nasabah profile, live.

Why this exists
---------------
The demo's whole claim is that the AI's answer -- and the offer it introduces --
are driven by the customer's stored profile, not by the model's imagination.
The most direct way to *show* that is to change the profile and ask the same
question again.

We cannot do it by having several people message the bot, because a WhatsApp
number has exactly one inbound webhook and Chatwoot matches an inbound message
to a contact by phone number: one handset is one contact, always. So instead of
switching handsets we switch what that one contact *is*, between beats of the
demo. Run this, send the same question again, and the bot answers a different
investor.

The three profiles below are internally coherent on purpose. An earlier seeded
record had a Moderat nasabah whose offer rationale said their portfolio was
"terkonsentrasi pada satu kelas aset" while their holdings were empty -- true of
nobody, and exactly the kind of detail a securities audience notices on screen.
Each profile here holds what its rationale claims it holds.

`product_gaps` is restricted to the products suitable for that risk profile,
matching the invariant enforced in `seed_demo_data/nasabah.py::_gaps_for`. That
is not cosmetic: the gaps list is rendered into the model's prompt, so a
conservative investor whose gaps mention an equity fund puts an unsuitable
product one sentence away from being said out loud.

Usage
-----
    export CW_TOKEN=...            # CHATWOOT_API_TOKEN from tenants/bahana.env
    python3 bahana_demo_profile.py konservatif
    python3 bahana_demo_profile.py moderat
    python3 bahana_demo_profile.py agresif
    python3 bahana_demo_profile.py --show

Takes about a second. Safe to run mid-conversation -- the agent re-reads the
contact on every turn, so the next message the nasabah sends is answered
against the new profile.
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

BASE_URL = os.environ.get("CW_URL", "https://bahana.crm.34-50-103-151.nip.io")
ACCOUNT_ID = int(os.environ.get("CW_ACCOUNT_ID", "1"))
CONTACT_ID = int(os.environ.get("CW_CONTACT_ID", "4"))

# Keys must match seed_demo_data/client.py::build_nasabah_custom_attributes and
# agent/app/services/customer_context.py::_PROFILE_FIELDS exactly. A typo here
# does not error -- it silently empties that row of the agent sidebar and drops
# the field from the AI's prompt.
PROFILES: dict[str, dict[str, str]] = {
    "moderat": {
        "name": "[DEMO] Budi Santoso",
        "risk_profile": "Moderat",
        "aum_band": "Rp 100-500 juta",
        "rdn_balance": "Rp 46,000,000",
        "holdings": "BBCA, BBRI, TLKM",
        "days_since_last_transaction": "190",
        "product_gaps": "Reksa Dana Campuran, Obligasi Korporasi",
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
        "product_gaps": "Reksa Dana Pasar Uang, Obligasi Ritel (ORI)",
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
        "product_gaps": "Reksa Dana Saham, IPO Subscription",
        "next_best_offer": "Reksa Dana Saham",
        "offer_rationale": (
            "profil risiko agresif yang sudah sangat aktif di saham namun belum "
            "terdiversifikasi lewat reksa dana"
        ),
    },
}

DEMO_SEED = "demo1"


def _token() -> str:
    tok = os.environ.get("CW_TOKEN", "").strip()
    if not tok:
        sys.exit(
            "CW_TOKEN is not set. Read CHATWOOT_API_TOKEN out of "
            "tenants/bahana.env on the VM and export it:\n"
            "  export CW_TOKEN=$(gcloud compute ssh crm-ticketing "
            "--zone=asia-southeast2-a --project=lv-playground-genai "
            "--command='sudo grep -E \"^CHATWOOT_API_TOKEN=\" "
            "/opt/platform/deploy/tenants/bahana.env | cut -d= -f2-')"
        )
    return tok


def _headers(tok: str) -> dict[str, str]:
    return {"api_access_token": tok, "Content-Type": "application/json"}


def show(tok: str) -> None:
    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{CONTACT_ID}"
    r = httpx.get(url, headers={"api_access_token": tok}, timeout=30)
    r.raise_for_status()
    body = r.json()
    body = body.get("payload") if isinstance(body.get("payload"), dict) else body
    attrs = body.get("custom_attributes") or {}
    print(f"contact {CONTACT_ID}: {body.get('name')}  {body.get('phone_number')}")
    for key in sorted(attrs):
        print(f"  {key:28} = {attrs[key]}")


def apply(tok: str, profile: str) -> None:
    data = dict(PROFILES[profile])
    name = data.pop("name")
    data["demo_seed"] = DEMO_SEED

    url = f"{BASE_URL}/api/v1/accounts/{ACCOUNT_ID}/contacts/{CONTACT_ID}"
    r = httpx.put(
        url,
        headers=_headers(tok),
        json={"name": name, "custom_attributes": data},
        timeout=30,
    )
    r.raise_for_status()

    print(f"contact {CONTACT_ID} is now: {name} ({data['risk_profile']})")
    print(f"  holdings        : {data['holdings']}")
    print(f"  next best offer : {data['next_best_offer']}")
    print("\nSend the same question again from the demo handset.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repoint the Bahana demo handset at a different nasabah profile."
    )
    parser.add_argument(
        "profile",
        nargs="?",
        choices=sorted(PROFILES),
        help="Which profile the demo contact should become.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Print the contact's current attributes and exit.",
    )
    args = parser.parse_args()

    tok = _token()
    if args.show:
        show(tok)
        return 0
    if not args.profile:
        parser.error("give a profile name, or --show")
    apply(tok, args.profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
