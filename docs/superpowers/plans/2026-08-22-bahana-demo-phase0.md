# Bahana Demo (Phase 0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A live WhatsApp demo in which the AI recognizes a nasabah, personalizes its answer from their portfolio, introduces a pre-selected offer in context, and a human agent can take the conversation over at any moment.

**Architecture:** Two independent code changes plus a runbook. The seeder gains a `seed-nasabah` subcommand that writes synthetic securities-customer profiles into Chatwoot **contact** custom attributes (which Chatwoot renders in the conversation sidebar with no fork patch). The `agent/` orchestrator reads those attributes off the conversation's contact and injects them into the Gemini decision prompt. Both are fail-open: no contact, no attributes, or any HTTP failure yields today's behavior byte-identical.

**Tech Stack:** Python 3.12, pytest, httpx, respx (agent tests), Chatwoot REST API, Twilio WhatsApp, Gemini via `google-genai`.

**Spec:** `docs/superpowers/specs/2026-08-22-bahana-personalization-design.md` (Phase 0 is §5)

## Global Constraints

- **No Chatwoot fork patch, and no Chatwoot image rebuild.** Only `agent/` and `deploy/scripts/seed_demo_data/` are touched. (Spec §5.1)
- **Fail-open everywhere.** Missing contact, missing attributes, or any downstream HTTP failure must log and continue, never raise. Background tasks that raise produce unretrieved-exception logs. (CLAUDE.md, spec §5.2)
- **Default behavior must stay byte-identical.** With no customer context, `_build_system_prompt` returns exactly what it returns today. Existing tests must pass unmodified.
- **Suitability is enforced in generation, never in the prompt.** The offer attached to a nasabah is selected by a deterministic rule from their risk profile. The LLM may only phrase the offer it was handed. (Spec §4.3)
- **Determinism per batch.** `(count, batch_id, seed)` must produce byte-identical output. Use the `random.Random(f"{seed}:{batch_id}")` pattern already in `generator.py`; never module-level `random.*`.
- **Phone numbers are non-routable** — `+999` (ITU-T E.164 reserved-for-testing) — with exactly one exception: the explicitly pinned demo handset passed on the command line.
- **All seeded data is visibly synthetic.** Names carry the existing `[DEMO] ` prefix.
- **Seeder tests run from inside `deploy/scripts/seed_demo_data/`** (they import `from generator import ...`, not a package path) and are **synchronous** — no async tests in that directory today. Keep it that way by testing pure functions.
- **Agent tests run from `agent/`** with `pytest` (asyncio_mode = auto, no flags needed).
- Chatwoot custom attribute values are stored as **strings**; lists are comma-joined.

---

### Task 1: Nasabah profile generator

Pure, deterministic generation of synthetic securities customers. No I/O.

**Files:**
- Create: `deploy/scripts/seed_demo_data/nasabah.py`
- Test: `deploy/scripts/seed_demo_data/test_nasabah.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `DemoNasabah` frozen dataclass with fields `name: str`, `phone: str`, `email: str`, `risk_profile: str`, `aum_band: str`, `rdn_balance: int`, `holdings: list[str]`, `days_since_last_transaction: int`, `product_gaps: list[str]`, `next_best_offer: str`, `offer_rationale: str`
  - `generate_nasabah(count: int, batch_id: str, seed: int = 20260822, pinned_phone: str | None = None, pinned_name: str | None = None) -> list[DemoNasabah]`
  - `RISK_PROFILES: tuple[str, ...]` = `("Konservatif", "Moderat", "Agresif")`
  - `offer_for(risk_profile: str, rnd: random.Random) -> tuple[str, str]`

- [ ] **Step 1: Write the failing test**

Create `deploy/scripts/seed_demo_data/test_nasabah.py`:

```python
"""Synthetic nasabah must be deterministic, non-routable, and suitable:
the offer attached to a profile is chosen by risk profile, not by an LLM,
so a Konservatif customer can never be carrying an equity offer."""

from __future__ import annotations

import re

from nasabah import (
    RISK_PROFILES,
    DemoNasabah,
    generate_nasabah,
    offer_for,
)

CONSERVATIVE_ONLY = {"Reksa Dana Pasar Uang", "Obligasi Ritel (ORI)"}
AGGRESSIVE_ONLY = {"Reksa Dana Saham", "IPO Subscription"}


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
    import random

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd deploy/scripts/seed_demo_data && python3 -m pytest test_nasabah.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nasabah'`

- [ ] **Step 3: Write minimal implementation**

Create `deploy/scripts/seed_demo_data/nasabah.py`:

```python
"""Deterministic synthetic-nasabah generator for the Bahana Phase 0 demo.

Pure, in-memory generation only: no I/O, no network. Produces `DemoNasabah`
records that `client.create_nasabah_contact` writes to Chatwoot as contact
custom attributes, which Chatwoot renders in the conversation sidebar with
no fork patch (see the design spec §5.1 for why that matters).

Two things here are deliberate rather than incidental:

**The offer is selected in code, from the risk profile.** Suitability written
as a prompt instruction leaks eventually; suitability written as a lookup
cannot. The language model is later handed one offer and asked to phrase it,
never to choose one -- so a Konservatif nasabah is structurally incapable of
being pitched an equity product, whatever the model is prompted with. See
design spec §4.3.

**Every phone is +999 except one.** +999 is the ITU-T E.164 reserved-for-
testing country code: permanently unassigned, so no generated number can
route to a real subscriber. The single exception is `pinned_phone`, which is
the demo handset -- the AI has to recognise the phone the demo is performed
from, and a +999 number can never be that. It is opt-in on the command line
precisely so a real number never appears by accident.

Determinism follows `generator.py`: every draw goes through the
`random.Random(f"{seed}:{batch_id}")` instance threaded through this module,
never the module-level `random.*` functions, so two calls with the same
`(count, batch_id, seed)` are byte-identical and two different batches never
collide on Chatwoot's per-account phone/email uniqueness.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import TypeVar

T = TypeVar("T")


# --- Public data shapes -----------------------------------------------------


@dataclass(frozen=True)
class DemoNasabah:
    name: str
    phone: str
    email: str
    risk_profile: str
    aum_band: str
    rdn_balance: int
    holdings: list[str]
    days_since_last_transaction: int
    product_gaps: list[str]
    next_best_offer: str
    offer_rationale: str


RISK_PROFILES: tuple[str, ...] = ("Konservatif", "Moderat", "Agresif")


# --- Vocabulary -------------------------------------------------------------

_FIRST_NAMES = [
    "Budi", "Siti", "Agus", "Dewi", "Andi", "Rina", "Bambang", "Fitri",
    "Joko", "Ayu", "Hendra", "Maya", "Rizki", "Indah", "Yusuf", "Lestari",
    "Dimas", "Ratna", "Fajar", "Wulan",
]
_LAST_NAMES = [
    "Santoso", "Wijaya", "Pratama", "Kusuma", "Hartono", "Sari", "Nugroho",
    "Halim", "Setiawan", "Anggraini", "Suryadi", "Permata", "Gunawan",
    "Puspita", "Firmansyah", "Handayani", "Saputra", "Maharani", "Iskandar",
    "Rahayu",
]

# Real IDX tickers -- a demo that shows plausible holdings reads as a real
# product. Nothing here is a recommendation; see `offer_for`.
_TICKERS = [
    "BBCA", "BBRI", "BMRI", "TLKM", "ASII", "UNVR", "ICBP", "ANTM",
    "PGAS", "KLBF",
]

_RISK_WEIGHTS = [("Konservatif", 35), ("Moderat", 45), ("Agresif", 20)]

_AUM_BANDS = [
    "< Rp 50 juta",
    "Rp 50-100 juta",
    "Rp 100-500 juta",
    "Rp 500 juta - 1 miliar",
    "> Rp 1 miliar",
]

# Product universe. `product_gaps` is this set minus what the nasabah holds,
# which is what makes "belum memiliki X" a fact rather than a guess.
_PRODUCTS = [
    "Saham",
    "Reksa Dana Pasar Uang",
    "Reksa Dana Campuran",
    "Reksa Dana Saham",
    "Obligasi Ritel (ORI)",
    "Obligasi Korporasi",
]

# Suitability, as a lookup. Each risk profile maps to the offers that are
# appropriate for it -- and to nothing else.
_OFFERS_BY_RISK: dict[str, list[tuple[str, str]]] = {
    "Konservatif": [
        (
            "Reksa Dana Pasar Uang",
            "profil risiko konservatif dengan saldo RDN menganggur; produk "
            "pasar uang menawarkan likuiditas harian dengan risiko rendah",
        ),
        (
            "Obligasi Ritel (ORI)",
            "profil risiko konservatif yang mencari imbal hasil tetap di atas "
            "deposito",
        ),
    ],
    "Moderat": [
        (
            "Reksa Dana Campuran",
            "profil risiko moderat dengan portofolio yang terkonsentrasi pada "
            "satu kelas aset",
        ),
        (
            "Obligasi Korporasi",
            "profil risiko moderat yang dapat menyeimbangkan portofolio saham "
            "dengan pendapatan tetap",
        ),
    ],
    "Agresif": [
        (
            "Reksa Dana Saham",
            "profil risiko agresif yang sudah aktif di saham namun belum "
            "terdiversifikasi lewat reksa dana",
        ),
        (
            "IPO Subscription",
            "profil risiko agresif dengan transaksi aktif; berminat pada "
            "penawaran perdana",
        ),
    ],
}


# --- Helpers ----------------------------------------------------------------


def _weighted_choice(rnd: random.Random, weighted: list[tuple[T, int]]) -> T:
    items = [item for item, _ in weighted]
    weights = [weight for _, weight in weighted]
    return rnd.choices(items, weights=weights, k=1)[0]


def offer_for(risk_profile: str, rnd: random.Random) -> tuple[str, str]:
    """The (offer, rationale) appropriate for one risk profile.

    Public and pure so a test can assert the suitability rule directly
    without generating a population. An unknown profile falls back to the
    most conservative catalogue rather than raising -- a profile value this
    module doesn't recognise must degrade to "offered something safe", never
    to "crashed the seeder" and never to "offered something aggressive".
    """
    catalogue = _OFFERS_BY_RISK.get(risk_profile, _OFFERS_BY_RISK["Konservatif"])
    return rnd.choice(catalogue)


def _unique_phone(rnd: random.Random, used: set[str]) -> str:
    while True:
        rest = "".join(str(rnd.randrange(10)) for _ in range(9))
        phone = f"+999{rest}"
        if phone not in used:
            used.add(phone)
            return phone


def _make_nasabah(rnd: random.Random, index: int, used_phones: set[str]) -> DemoNasabah:
    first = rnd.choice(_FIRST_NAMES)
    last = rnd.choice(_LAST_NAMES)
    name = f"[DEMO] {first} {last}"
    phone = _unique_phone(rnd, used_phones)
    slug = f"{first}.{last}".lower().replace(" ", "")
    email = f"{slug}.demo{index}@example.invalid"

    risk_profile = _weighted_choice(rnd, _RISK_WEIGHTS)
    aum_band = rnd.choice(_AUM_BANDS)
    rdn_balance = rnd.randrange(500_000, 250_000_000, 500_000)

    held_products = rnd.sample(_PRODUCTS, k=rnd.randint(1, 3))
    product_gaps = [p for p in _PRODUCTS if p not in held_products]
    holdings = (
        rnd.sample(_TICKERS, k=rnd.randint(1, 4)) if "Saham" in held_products else []
    )

    days_since_last_transaction = rnd.randint(0, 400)
    offer, rationale = offer_for(risk_profile, rnd)

    return DemoNasabah(
        name=name,
        phone=phone,
        email=email,
        risk_profile=risk_profile,
        aum_band=aum_band,
        rdn_balance=rdn_balance,
        holdings=holdings,
        days_since_last_transaction=days_since_last_transaction,
        product_gaps=product_gaps,
        next_best_offer=offer,
        offer_rationale=rationale,
    )


# --- Public API -------------------------------------------------------------


def generate_nasabah(
    count: int,
    batch_id: str,
    seed: int = 20260822,
    pinned_phone: str | None = None,
    pinned_name: str | None = None,
) -> list[DemoNasabah]:
    """Generate `count` synthetic nasabah, deterministic per batch.

    `pinned_phone` overrides the FIRST record's phone (and `pinned_name` its
    name) so the handset the demo is performed from is recognised as an
    existing nasabah when its WhatsApp message arrives -- Chatwoot matches an
    inbound Twilio message to a contact by phone number, so the contact has
    to exist with that exact E.164 value beforehand.

    Pinning is applied by REPLACEMENT after generation rather than by
    branching inside the draw, so pinning cannot shift the random sequence:
    records 1..n-1 are identical whether or not a phone was pinned. That is
    what `test_pinning_does_not_change_the_other_nasabah` protects, and it
    means an operator can re-seed with a different handset without
    regenerating (and re-colliding) the rest of the batch.
    """
    rnd = random.Random(f"{seed}:{batch_id}")
    used_phones: set[str] = set()
    people = [_make_nasabah(rnd, i, used_phones) for i in range(count)]

    if people and pinned_phone:
        people[0] = replace(
            people[0],
            phone=pinned_phone,
            **({"name": f"[DEMO] {pinned_name}"} if pinned_name else {}),
        )

    return people
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd deploy/scripts/seed_demo_data && python3 -m pytest test_nasabah.py -v`
Expected: PASS, 13 tests

- [ ] **Step 5: Commit**

```bash
git add deploy/scripts/seed_demo_data/nasabah.py deploy/scripts/seed_demo_data/test_nasabah.py
git commit -m "feat(seeder): synthetic nasabah generator with suitability in code"
```

---

### Task 2: Chatwoot attribute payload for a nasabah

The pure attribute-builder plus the network call that uses it, mirroring the
existing `build_case_custom_attributes` / `create_contact` pair.

**Files:**
- Modify: `deploy/scripts/seed_demo_data/client.py` (append after `create_contact`, which ends at line 476)
- Test: `deploy/scripts/seed_demo_data/test_nasabah_attributes.py`

**Interfaces:**
- Consumes: `DemoNasabah` from Task 1
- Produces:
  - `build_nasabah_custom_attributes(nasabah: DemoNasabah, batch_id: str) -> dict[str, str]`
  - `async create_nasabah_contact(nasabah: DemoNasabah, batch_id: str) -> int`

- [ ] **Step 1: Write the failing test**

Create `deploy/scripts/seed_demo_data/test_nasabah_attributes.py`:

```python
"""The contact custom_attributes a seeded nasabah carries. These keys are the
contract with two consumers that never import this module: Chatwoot's contact
attribute definitions (which render them in the agent sidebar) and the agent
service's customer_context formatter. Renaming a key here silently empties
both."""

from __future__ import annotations

from client import build_nasabah_custom_attributes
from nasabah import generate_nasabah

EXPECTED_KEYS = {
    "demo_seed",
    "risk_profile",
    "aum_band",
    "rdn_balance",
    "holdings",
    "days_since_last_transaction",
    "product_gaps",
    "next_best_offer",
    "offer_rationale",
}


def test_carries_exactly_the_agreed_keys():
    nasabah = generate_nasabah(1, batch_id="b1")[0]
    attrs = build_nasabah_custom_attributes(nasabah, "b1")
    assert set(attrs) == EXPECTED_KEYS


def test_stamps_the_purge_marker():
    nasabah = generate_nasabah(1, batch_id="b1")[0]
    attrs = build_nasabah_custom_attributes(nasabah, "b1")
    assert attrs["demo_seed"] == "b1"


def test_every_value_is_a_string():
    # Chatwoot custom attribute values round-trip as strings; a list or int
    # sent here comes back in a shape the sidebar renders as "[object]".
    nasabah = generate_nasabah(1, batch_id="b1")[0]
    attrs = build_nasabah_custom_attributes(nasabah, "b1")
    assert all(isinstance(v, str) for v in attrs.values())


def test_lists_are_comma_joined():
    people = generate_nasabah(40, batch_id="b1")
    with_holdings = next(p for p in people if p.holdings)
    attrs = build_nasabah_custom_attributes(with_holdings, "b1")
    assert attrs["holdings"] == ", ".join(with_holdings.holdings)


def test_empty_holdings_render_as_an_explicit_phrase_not_blank():
    # A blank sidebar field is ambiguous between "no equities" and "we don't
    # know". The AI reads these values verbatim, so say which one it is.
    people = generate_nasabah(60, batch_id="b1")
    without = next(p for p in people if not p.holdings)
    attrs = build_nasabah_custom_attributes(without, "b1")
    assert attrs["holdings"] == "Tidak ada"


def test_rdn_balance_is_formatted_for_a_human_reader():
    nasabah = generate_nasabah(1, batch_id="b1")[0]
    attrs = build_nasabah_custom_attributes(nasabah, "b1")
    assert attrs["rdn_balance"].startswith("Rp ")
    # rdn_balance is always >= 500_000, so the thousands separator is not optional
    assert "," in attrs["rdn_balance"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd deploy/scripts/seed_demo_data && python3 -m pytest test_nasabah_attributes.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_nasabah_custom_attributes'`

- [ ] **Step 3: Write minimal implementation**

Append to `deploy/scripts/seed_demo_data/client.py`, immediately after
`create_contact` (which ends with `return contact_id` around line 476):

```python
def build_nasabah_custom_attributes(nasabah: "DemoNasabah", batch_id: str) -> dict[str, str]:
    """The full contact `custom_attributes` object one seeded nasabah carries.

    Pure and public for the same reason `build_case_custom_attributes` is: the
    exact key set is a contract with two consumers that never import this
    module -- Chatwoot's contact attribute *definitions*, which is what makes
    these render in the agent sidebar, and the agent service's
    `customer_context` formatter, which reads them back to build the AI's
    prompt. Renaming a key here silently empties both rather than failing.

    Every value is a string. Chatwoot round-trips custom attribute values as
    strings, and a list sent here comes back in a shape the sidebar renders
    as an object literal. Empty lists become an explicit phrase rather than
    an empty string: the AI reads these verbatim, and a blank field is
    ambiguous between "holds no equities" and "we have no data", which are
    very different things to say to a customer.
    """
    return {
        "demo_seed": batch_id,
        "risk_profile": nasabah.risk_profile,
        "aum_band": nasabah.aum_band,
        "rdn_balance": f"Rp {nasabah.rdn_balance:,}",
        "holdings": ", ".join(nasabah.holdings) if nasabah.holdings else "Tidak ada",
        "days_since_last_transaction": str(nasabah.days_since_last_transaction),
        "product_gaps": ", ".join(nasabah.product_gaps) if nasabah.product_gaps else "Tidak ada",
        "next_best_offer": nasabah.next_best_offer,
        "offer_rationale": nasabah.offer_rationale,
    }


async def create_nasabah_contact(nasabah: "DemoNasabah", batch_id: str) -> int:
    """Create one Chatwoot contact carrying a synthetic nasabah profile.

    Deliberately a sibling of `create_contact` rather than a parameter on it:
    that function writes the automotive attribute set (`vehicle_no`,
    `vehicle_model`, `purchased_from`) that Customer 360 and the Cases list
    read, and those two attribute sets have no overlap and no shared consumer.
    Merging them would mean every contact carrying both vocabularies.

    The create-then-verify-then-PATCH shape is copied from `create_contact`
    for the same reason it exists there: it is unverified whether Chatwoot
    persists unrecognised custom-attribute keys at create time, so the marker
    is confirmed on the response and stamped explicitly if absent. A contact
    this function returns is guaranteed marked, or the call raised.
    """
    config = _require_config()
    demo_attributes = build_nasabah_custom_attributes(nasabah, batch_id)
    payload = {
        "inbox_id": config.chatwoot_inbox_id,
        "name": nasabah.name,
        "email": nasabah.email,
        "phone_number": nasabah.phone,
        "custom_attributes": demo_attributes,
    }
    response = await _chatwoot.post(_account_path("/contacts"), json=payload)
    if response.status_code == 422:
        raise RuntimeError(
            f"Chatwoot rejected demo nasabah {nasabah.name!r} (phone {nasabah.phone}, "
            f"email {nasabah.email}) with HTTP 422: {response.text.strip()[:300]}. "
            "This is almost always a uniqueness collision -- a contact with that phone "
            "or email already exists in this account. Purge the earlier batch, or "
            "re-run with a different --batch-id. If the collision is on the PINNED "
            "phone, the demo handset is already a contact in this account: either "
            "purge it or drop --pinned-phone and edit that contact's attributes by hand."
        )
    response.raise_for_status()
    data = response.json()
    contact_obj = data.get("payload", {}).get("contact") if isinstance(data.get("payload"), dict) else None
    contact_obj = contact_obj if isinstance(contact_obj, dict) else data
    contact_id = contact_obj.get("id")
    if contact_id is None:
        raise RuntimeError(f"nasabah contact create returned no id: {data!r}")
    contact_id = int(contact_id)
    await _throttle()

    returned_attributes = contact_obj.get("custom_attributes")
    marker_confirmed = isinstance(returned_attributes, dict) and returned_attributes.get("demo_seed") == batch_id
    if not marker_confirmed:
        stamp_response = await _chatwoot.patch(
            _account_path(f"/contacts/{contact_id}"), json={"custom_attributes": demo_attributes}
        )
        stamp_response.raise_for_status()
        await _throttle()

    return contact_id
```

Add the import to `client.py` at line 104, directly below the existing
`from generator import DemoCase, DemoContact, canonical_division`:

```python
from nasabah import DemoNasabah
```

That import is a plain runtime import, not `TYPE_CHECKING`-guarded, so drop
the quotes from both `"DemoNasabah"` annotations above — write `nasabah:
DemoNasabah` in each signature.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd deploy/scripts/seed_demo_data && python3 -m pytest test_nasabah_attributes.py -v`
Expected: PASS, 6 tests

Then confirm nothing regressed:

Run: `cd deploy/scripts/seed_demo_data && python3 -m pytest -v`
Expected: PASS, all pre-existing tests still green

- [ ] **Step 5: Commit**

```bash
git add deploy/scripts/seed_demo_data/client.py deploy/scripts/seed_demo_data/test_nasabah_attributes.py
git commit -m "feat(seeder): write nasabah profiles to Chatwoot contact attributes"
```

---

### Task 3: `seed-nasabah` CLI subcommand

**Files:**
- Modify: `deploy/scripts/seed_demo_data/__main__.py` (`_build_parser` at line 503; add a command function beside `_cmd_seed` at line 336)
- Test: `deploy/scripts/seed_demo_data/test_nasabah_cli.py`

**Interfaces:**
- Consumes: `generate_nasabah` (Task 1), `create_nasabah_contact` (Task 2), the existing `_resolve_tenant_config`, `client.configure`
- Produces: `_cmd_seed_nasabah(args, parser) -> int`, and a `seed-nasabah` subparser

- [ ] **Step 1: Write the failing test**

Create `deploy/scripts/seed_demo_data/test_nasabah_cli.py`:

```python
"""The seed-nasabah subcommand's argument surface. Parsing only -- the
network path is exercised by hand against a live tenant, not in CI."""

from __future__ import annotations

import pytest

from __main__ import _build_parser


def test_seed_nasabah_is_a_registered_subcommand():
    parser = _build_parser()
    args = parser.parse_args(["seed-nasabah", "--tenant", "bahana", "--inbox-id", "1"])
    assert args.command == "seed-nasabah"
    assert args.tenant == "bahana"


def test_count_defaults_to_a_demo_sized_batch():
    parser = _build_parser()
    args = parser.parse_args(["seed-nasabah", "--tenant", "bahana", "--inbox-id", "1"])
    assert args.count == 25


def test_pinned_phone_is_optional_and_defaults_to_none():
    parser = _build_parser()
    args = parser.parse_args(["seed-nasabah", "--tenant", "bahana", "--inbox-id", "1"])
    assert args.pinned_phone is None


def test_pinned_phone_and_name_are_accepted():
    parser = _build_parser()
    args = parser.parse_args([
        "seed-nasabah", "--tenant", "bahana", "--inbox-id", "1",
        "--pinned-phone", "+628123456789", "--pinned-name", "Budi Santoso",
    ])
    assert args.pinned_phone == "+628123456789"
    assert args.pinned_name == "Budi Santoso"


def test_inbox_id_is_required():
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["seed-nasabah", "--tenant", "bahana"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd deploy/scripts/seed_demo_data && python3 -m pytest test_nasabah_cli.py -v`
Expected: FAIL — argparse errors with `invalid choice: 'seed-nasabah'`

- [ ] **Step 3: Write minimal implementation**

In `deploy/scripts/seed_demo_data/__main__.py`, add the runner beside
`_cmd_seed` (line 336). Read `_run_seed` (line 214) first and mirror its
config-resolution and progress-printing style exactly:

```python
async def _run_nasabah_seed(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Create `--count` synthetic nasabah contacts on the target tenant.

    Contacts only -- no conversations, no RSA rows. The Bahana Phase 0 demo
    needs profiles visible in the agent sidebar and recognisable by the bot;
    it never opens a seeded case. Generating cases too would mean porting the
    whole automotive case/division/RSA vocabulary to a securities one for
    surfaces the demo never visits (design spec §5.3).
    """
    batch_id = args.batch_id or _default_batch_id()
    config = _resolve_tenant_config(args, parser)
    client.configure(config)

    people = generate_nasabah(
        args.count,
        batch_id=batch_id,
        seed=args.rng_seed,
        pinned_phone=args.pinned_phone,
        pinned_name=args.pinned_name,
    )

    if args.pinned_phone:
        print(f"Pinned demo handset {args.pinned_phone} -> {people[0].name}")

    created = 0
    try:
        for nasabah in people:
            await client.create_nasabah_contact(nasabah, batch_id)
            created += 1
            if created % 10 == 0:
                print(f"  {created}/{len(people)} nasabah created")
    finally:
        await client.aclose()

    print(f"Created {created} nasabah contacts on tenant {args.tenant!r}.")
    print(f"BATCH ID: {batch_id}")
    # `purge` spells this flag --batch, not --batch-id. Getting it wrong here
    # sends an operator hunting for a command that argparse rejects.
    print(f"Purge with: python3 -m seed_demo_data purge --tenant {args.tenant} --batch {batch_id}")
    return 0


def _cmd_seed_nasabah(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    return asyncio.run(_run_nasabah_seed(args, parser))
```

Add the import beside the existing generator import at the top:

```python
from nasabah import generate_nasabah
```

In `_build_parser` (line 503), register the subcommand after the existing
`seed` parser block. Match how `seed` wires `--batch-id` and `--rng-seed` —
copy those two `add_argument` calls verbatim from the `seed` parser so the
flags behave identically:

```python
    nasabah_cmd = subparsers.add_parser(
        "seed-nasabah",
        help="Create synthetic nasabah contacts (Bahana demo; contacts only, no cases)",
    )
    nasabah_cmd.add_argument("--tenant", required=True, help="Tenant slug (e.g. 'bahana')")
    nasabah_cmd.add_argument(
        "--count", type=int, default=25, help="Number of nasabah contacts (default: 25)"
    )
    nasabah_cmd.add_argument(
        "--pinned-phone",
        default=None,
        help=(
            "E.164 phone of the handset the demo will be performed from. "
            "Replaces the first nasabah's number so the bot recognises it. "
            "This is the ONLY routable number the seeder will ever write."
        ),
    )
    nasabah_cmd.add_argument(
        "--pinned-name",
        default=None,
        help="Display name for the pinned demo contact (still prefixed [DEMO])",
    )
    nasabah_cmd.add_argument(
        "--rng-seed",
        type=int,
        default=20260822,
        help="nasabah.py's determinism seed (advanced; default matches generate_nasabah()'s own default).",
    )
    nasabah_cmd.add_argument("--batch-id", default=None, help="Override the auto-generated batch id.")
    _add_chatwoot_flags(nasabah_cmd, require_inbox=True)
    # `parser=nasabah_cmd` mirrors the `seed` subparser: `_resolve_tenant_config`
    # calls `parser.error()`, and stashing the SUBcommand's parser is what makes
    # that print this subcommand's usage line instead of the top-level one.
    nasabah_cmd.set_defaults(func=_cmd_seed_nasabah, parser=nasabah_cmd)
```

`--rng-seed` and `--batch-id` are declared here rather than inherited:
`_add_chatwoot_flags` does not supply them — the `seed` subparser declares
both itself, and `_run_nasabah_seed` reads both.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd deploy/scripts/seed_demo_data && python3 -m pytest test_nasabah_cli.py -v`
Expected: PASS, 5 tests

Run: `cd deploy/scripts/seed_demo_data && python3 -m pytest -v`
Expected: PASS, whole seeder suite green

- [ ] **Step 5: Commit**

```bash
git add deploy/scripts/seed_demo_data/__main__.py deploy/scripts/seed_demo_data/test_nasabah_cli.py
git commit -m "feat(seeder): seed-nasabah subcommand with pinned demo handset"
```

---

### Task 4: Customer-context formatter (agent side)

Pure function turning contact custom attributes into a prompt section. No I/O.

**Files:**
- Create: `agent/app/services/customer_context.py`
- Test: `agent/tests/test_customer_context.py`

**Interfaces:**
- Consumes: nothing
- Produces: `format_customer_context(attributes: dict | None) -> str` — returns `""` when there is nothing usable

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_customer_context.py`:

```python
"""The customer profile block appended to the agent-bot decision prompt.

Two invariants matter more than the formatting: an absent or unrecognised
profile yields the empty string (so the prompt is unchanged), and the block
never authorises the model to select a product."""

from __future__ import annotations

from app.services.customer_context import format_customer_context

FULL = {
    "risk_profile": "Moderat",
    "aum_band": "Rp 100-500 juta",
    "rdn_balance": "Rp 12,500,000",
    "holdings": "BBCA, TLKM",
    "days_since_last_transaction": "47",
    "product_gaps": "Obligasi Ritel (ORI), Reksa Dana Saham",
    "next_best_offer": "Reksa Dana Campuran",
    "offer_rationale": "profil risiko moderat dengan portofolio terkonsentrasi",
}


def test_none_yields_empty_string():
    assert format_customer_context(None) == ""


def test_empty_dict_yields_empty_string():
    assert format_customer_context({}) == ""


def test_non_dict_yields_empty_string():
    assert format_customer_context("not a dict") == ""
    assert format_customer_context(["also", "not"]) == ""


def test_unrelated_attributes_yield_empty_string():
    # A tenant whose contacts carry the automotive attribute set must get
    # today's prompt, byte for byte -- not an empty "profile" heading.
    assert format_customer_context({"vehicle_no": "W 1234", "demo_seed": "b1"}) == ""


def test_includes_the_known_profile_fields():
    out = format_customer_context(FULL)
    assert "Moderat" in out
    assert "Rp 100-500 juta" in out
    assert "BBCA, TLKM" in out
    assert "47" in out


def test_includes_the_staged_offer_and_its_rationale():
    out = format_customer_context(FULL)
    assert "Reksa Dana Campuran" in out
    assert "profil risiko moderat" in out


def test_forbids_inventing_or_substituting_a_product():
    out = format_customer_context(FULL)
    lowered = out.lower()
    assert "only" in lowered or "never" in lowered
    assert "do not recommend" in lowered or "not investment advice" in lowered


def test_answers_the_question_first():
    out = format_customer_context(FULL)
    assert "answer" in out.lower()


def test_partial_profile_renders_only_what_is_present():
    out = format_customer_context({"risk_profile": "Konservatif"})
    assert "Konservatif" in out
    assert "AUM" not in out


def test_profile_without_an_offer_has_no_offer_section():
    out = format_customer_context({"risk_profile": "Konservatif", "aum_band": "< Rp 50 juta"})
    assert out != ""
    assert "offer" not in out.lower()


def test_offer_without_a_rationale_still_renders():
    out = format_customer_context({"risk_profile": "Agresif", "next_best_offer": "IPO Subscription"})
    assert "IPO Subscription" in out


def test_blank_values_are_skipped():
    out = format_customer_context({"risk_profile": "Moderat", "aum_band": "   "})
    assert "AUM" not in out


def test_is_deterministic():
    assert format_customer_context(FULL) == format_customer_context(FULL)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_customer_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.customer_context'`

- [ ] **Step 3: Write minimal implementation**

Create `agent/app/services/customer_context.py`:

```python
"""Render a Chatwoot contact's stored profile into a prompt section.

Pure and side-effect free: the orchestrator does the fetching, this decides
what the model is allowed to see and told to do with it.

Three decisions are load-bearing:

**An unrecognised profile yields the empty string, not an empty heading.**
Every other tenant's contacts carry a different attribute set (`vehicle_no`,
`vehicle_model`, ...). Those must produce today's prompt byte for byte --
`_build_system_prompt` appends nothing when this returns "". A "Customer
profile:" heading with no fields under it would be a behaviour change for
every existing tenant, and a confusing one for the model.

**The offer is handed over, never chosen.** The catalogue and the suitability
rule live in the seeder (`nasabah.offer_for`); by the time the model sees
anything, the decision is made. The instructions below say so explicitly so
that a persona's custom instructions can't talk the model into substituting a
product it likes better. See design spec §4.3.

**Answering the customer comes first.** A model handed an offer will lead with
it unless told not to. The offer is a thing to weave in when it fits, not the
purpose of the reply -- and in the demo, a bot that ignores the actual question
to pitch a product is the failure mode everyone in the room will notice.
"""

from __future__ import annotations

# (attribute key, human label) in the order they are rendered. Keys must match
# `client.build_nasabah_custom_attributes` exactly -- that function's docstring
# names this module as the reason.
_PROFILE_FIELDS: tuple[tuple[str, str], ...] = (
    ("risk_profile", "Risk profile"),
    ("aum_band", "AUM band"),
    ("rdn_balance", "RDN cash balance"),
    ("holdings", "Equity holdings"),
    ("days_since_last_transaction", "Days since last transaction"),
    ("product_gaps", "Products not yet held"),
)

_OFFER_INSTRUCTIONS = (
    "Mention this offer ONLY if it fits naturally into the conversation. "
    "Answer the customer's actual question first and completely; the offer "
    "is secondary and may be left out entirely. You may only mention the "
    "offer named above -- never substitute, invent, or add another product, "
    "and never quote a return, yield, price, or fee. This is a relationship "
    "offer, not investment advice: do not recommend buying or selling any "
    "specific security."
)


def format_customer_context(attributes: object) -> str:
    """A prompt section describing this customer, or "" if there is nothing
    to say.

    Accepts `object` rather than `dict | None` on purpose: this is fed
    straight from a JSON response body, where a malformed or unexpected
    payload can put a string or a list where a dict belongs. Returning ""
    for those is the fail-open path -- the alternative is an AttributeError
    inside a background task.
    """
    if not isinstance(attributes, dict):
        return ""

    lines: list[str] = []
    for key, label in _PROFILE_FIELDS:
        value = attributes.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            lines.append(f"- {label}: {text}")

    offer = str(attributes.get("next_best_offer") or "").strip()
    rationale = str(attributes.get("offer_rationale") or "").strip()

    if not lines and not offer:
        return ""

    parts = ["## Customer profile (from the CRM record for this contact)"]
    if lines:
        parts.append("\n".join(lines))
    else:
        parts.append("- No profile details recorded.")

    if offer:
        offer_block = f"## Relationship offer selected for this customer\n- {offer}"
        if rationale:
            offer_block += f"\n- Why it was selected: {rationale}"
        parts.append(offer_block)
        parts.append(_OFFER_INSTRUCTIONS)

    return "\n\n".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_customer_context.py -v`
Expected: PASS, 13 tests

- [ ] **Step 5: Commit**

```bash
git add agent/app/services/customer_context.py agent/tests/test_customer_context.py
git commit -m "feat(agent): customer profile prompt section, empty when unrecognised"
```

---

### Task 5: Wire customer context into the orchestrator

**Files:**
- Modify: `agent/app/services/orchestrator.py` — `_build_system_prompt` (line 84) and `_process_conversation` (line 347)
- Test: `agent/tests/test_customer_context_wiring.py`

**Interfaces:**
- Consumes: `format_customer_context` (Task 4), `ChatwootClient.get_conversation` / `.get_contact` (existing)
- Produces: `_build_system_prompt(persona: dict | None, customer_context: str = "") -> str`

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_customer_context_wiring.py`:

```python
"""Customer context reaches the decision prompt, and its absence changes
nothing. The second half matters more than the first: every other tenant runs
this code path with contacts that carry no nasabah attributes at all."""

from __future__ import annotations

from app.services.orchestrator import SYSTEM_PROMPT, _build_system_prompt

PERSONA = {
    "instructions": "You are Bahana's relationship assistant.",
    "guardrails": ["Never promise a return."],
    "language": "Bahasa Indonesia",
}

CONTEXT = "## Customer profile (from the CRM record for this contact)\n- Risk profile: Moderat"


def test_no_persona_no_context_is_byte_identical_to_today():
    assert _build_system_prompt(None) == SYSTEM_PROMPT
    assert _build_system_prompt(None, "") == SYSTEM_PROMPT


def test_persona_without_context_is_unchanged():
    assert _build_system_prompt(PERSONA, "") == _build_system_prompt(PERSONA)


def test_empty_persona_dict_without_context_is_still_the_default():
    assert _build_system_prompt({}, "") == SYSTEM_PROMPT
    assert _build_system_prompt({"instructions": "", "guardrails": [], "language": ""}, "") == SYSTEM_PROMPT


def test_context_is_appended_when_there_is_no_persona():
    out = _build_system_prompt(None, CONTEXT)
    assert out.startswith(SYSTEM_PROMPT)
    assert CONTEXT in out


def test_context_is_appended_after_the_persona():
    out = _build_system_prompt(PERSONA, CONTEXT)
    assert "Bahana's relationship assistant" in out
    assert "Never promise a return." in out
    assert out.rstrip().endswith(CONTEXT)


def test_context_never_replaces_the_persona_guardrails():
    out = _build_system_prompt(PERSONA, CONTEXT)
    assert "Guardrails" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && pytest tests/test_customer_context_wiring.py -v`
Expected: FAIL — `TypeError: _build_system_prompt() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Write minimal implementation**

In `agent/app/services/orchestrator.py`, rename the existing function body to
a private helper and add the new signature on top of it. Replace the `def
_build_system_prompt(persona: dict | None) -> str:` line (line 84) and keep
its entire existing body, renamed:

```python
def _persona_prompt(persona: dict | None) -> str:
    """Compose the agent-bot decision prompt from an assistant persona.

    None or all-empty persona -> the module SYSTEM_PROMPT verbatim (byte-identical
    default). Otherwise: base = instructions if set else SYSTEM_PROMPT; then append
    a Guardrails section and a language-preference line when present.

    LANGUAGE_MATCH_INSTRUCTION is always present in the output, even when custom
    instructions replace SYSTEM_PROMPT — operators routinely forget to restate it,
    and dropping it caused the bot to answer in the wrong language (WA-2/IVR-4).
    """
    # ... existing body unchanged, lines 95-112 ...


def _build_system_prompt(persona: dict | None, customer_context: str = "") -> str:
    """The persona prompt, plus this customer's profile when we have one.

    `customer_context` defaults to "" and appends nothing when empty, so every
    caller and every tenant that has no nasabah profile on the contact gets the
    persona prompt byte for byte. That default is what
    `test_no_persona_no_context_is_byte_identical_to_today` pins.

    The profile goes LAST, after the persona's guardrails. An operator-authored
    guardrail should constrain what the model does with the profile, not be
    buried under it.
    """
    base = _persona_prompt(persona)
    if not customer_context:
        return base
    return f"{base}\n\n{customer_context}"
```

Then in `_process_conversation` (line 347), hoist `conversation_data` so it
survives the existing `if proton is not None` block, and add the contact
fetch. Change the existing declaration block near the top of the function:

```python
    proton = get_proton_config_client()
    inbox_id: int | None = None
    conversation_data: dict | None = None
    if proton is not None:
        try:
            conversation_data = await chatwoot.get_conversation(conversation_id)
            inbox_id = conversation_data.get("inbox_id")
        except Exception:
            logger.debug(
                "orchestrator: could not fetch conversation %s for inbox_id lookup; "
                "will use global agent_mode",
                conversation_id,
            )
```

Then, immediately after the existing `system_prompt = _build_system_prompt(persona)`
line (line 397), replace that line with:

```python
    # Fetch this contact's stored profile and fold it into the decision prompt.
    # Entirely best-effort: any failure, a conversation with no sender, or a
    # contact carrying attributes this build doesn't recognise all leave
    # customer_context empty, which makes _build_system_prompt a no-op. Runs as
    # part of a background task, so it must never raise (see CLAUDE.md).
    customer_context = ""
    try:
        if conversation_data is None:
            conversation_data = await chatwoot.get_conversation(conversation_id)
        sender = (conversation_data.get("meta") or {}).get("sender") or {}
        contact_id = sender.get("id")
        if contact_id is not None:
            contact = await chatwoot.get_contact(int(contact_id))
            body = contact.get("payload") if isinstance(contact, dict) else None
            body = body if isinstance(body, dict) else contact
            customer_context = format_customer_context(
                body.get("custom_attributes") if isinstance(body, dict) else None
            )
    except Exception:
        logger.debug(
            "orchestrator: could not resolve customer context for conversation %s; "
            "proceeding without a customer profile",
            conversation_id,
        )

    system_prompt = _build_system_prompt(persona, customer_context)
```

Add the import to the existing `from app.services import ...` line:

```python
from app.services import lifecycle, lifecycle_store, whatsapp_format
from app.services.customer_context import format_customer_context
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd agent && pytest tests/test_customer_context_wiring.py -v`
Expected: PASS, 6 tests

Then the full suite — this task changes a function every orchestrator test
exercises, so a green suite is the actual deliverable:

Run: `cd agent && pytest`
Expected: PASS, no regressions

- [ ] **Step 5: Commit**

```bash
git add agent/app/services/orchestrator.py agent/tests/test_customer_context_wiring.py
git commit -m "feat(agent): fold the contact's profile into the bot decision prompt"
```

---

### Task 6: Provisioning and demo runbook

The parts of Phase 0 that are console work, written down so they can be
executed under time pressure without re-deriving anything.

**Files:**
- Create: `docs/bahana-demo-runbook.md`

**Interfaces:**
- Consumes: the `seed-nasabah` command (Task 3), the deployed `agent` (Task 5)
- Produces: nothing code depends on

- [ ] **Step 1: Write the runbook**

Create `docs/bahana-demo-runbook.md` covering, in this order:

1. **Pre-flight (do first, external dependencies).**
   - Start the WhatsApp display-name change for `+16292843510` — currently
     "Demo Main Account", visible to the customer in WhatsApp, and the change
     goes through Meta review.
   - Top up the Twilio balance (was $6.90).
   - Confirm `+16292843510` is in the **PT Devoteam Cloud Services** account
     and that aeon360's `+16823993949` is in a different account/subaccount —
     do not repoint a live customer's number.

2. **Provision the tenant.**
   - Diff `deploy/scripts/add-tenant.sh` and `deploy/tenants/example.env`
     against what the aeon360 provisioning actually needed before running.
   - `deploy/scripts/add-tenant.sh bahana` on the VM. Hostnames land at
     `bahana.crm.<ip>.nip.io` (nip.io, so no DNS wait; Caddy obtains TLS).
   - Trim `PROTON_FEATURES` in `tenants/bahana.env` to the demo surfaces.
     **Do not create a custom Chatwoot role** — a custom role replaces
     `administrator` outright and will lock you out of the tenant.

3. **Wire the channel.**
   - Create the Chatwoot admin user.
   - Add the Twilio WhatsApp inbox in Chatwoot (account SID, auth token,
     `whatsapp:+16292843510`).
   - Point the Twilio number's inbound webhook at that inbox's callback URL.
   - Register the agent bot and assign it to the inbox.

4. **Define contact custom attributes** in Chatwoot admin. Record the exact
   keys, which must match `build_nasabah_custom_attributes`:
   `risk_profile`, `aum_band`, `rdn_balance`, `holdings`,
   `days_since_last_transaction`, `product_gaps`, `next_best_offer`,
   `offer_rationale`. All of type Text. State in the runbook that a key
   mismatch silently empties the sidebar rather than erroring.

5. **Deploy the agent** with the Task 5 change:
   ```
   docker compose -p bahana -f docker-compose.tenant.yml \
     --env-file tenants/bahana.env up -d --build agent
   ```
   Note explicitly: sync source to `/opt/platform` first, and never copy a
   single file wholesale — it imports that file's whole future import graph.

6. **Seed.** Small batch first, watch it, then the real one:
   ```
   python -m seed_demo_data seed-nasabah --tenant bahana --inbox-id <id> \
     --count 3 --batch-id smoke
   python -m seed_demo_data seed-nasabah --tenant bahana --inbox-id <id> \
     --count 25 --batch-id demo1 \
     --pinned-phone <demo handset E.164> --pinned-name "Budi Santoso"
   ```
   Record the purge command for both batches — note the flag is `--batch`,
   not `--batch-id`:
   ```
   python3 -m seed_demo_data purge --tenant bahana --batch smoke
   ```

7. **Set the persona** in the CRM's Knowledge settings for the WhatsApp inbox:
   the Bahana relationship-assistant framing, language Bahasa Indonesia, and a
   guardrail forbidding specific buy/sell recommendations.

8. **Rehearse end to end from the pinned handset.** Verify in order: the
   contact is matched (not newly created); the sidebar shows the profile; the
   bot answers the question; the offer appears in context; an agent reply
   silences the bot; the `ai_actions` row exists.

9. **Demo script** — the six beats from design spec §5.8, including saying out
   loud that the data is synthetic and that authentication is not implemented.

10. **Fallbacks.** If WhatsApp fails on the day, the Chatwoot website widget
    gives an identical personalization story on a less impressive channel. If
    the contact isn't matched, the bot degrades to generic — still a working
    demo, just without the personalization beat.

- [ ] **Step 2: Verify the attribute keys against the code**

Run: `cd deploy/scripts/seed_demo_data && python3 -c "from nasabah import generate_nasabah; from client import build_nasabah_custom_attributes; print(sorted(build_nasabah_custom_attributes(generate_nasabah(1, batch_id='x')[0], 'x')))"`
Expected: the printed list matches the keys written in runbook step 4 exactly.

- [ ] **Step 3: Commit**

```bash
git add docs/bahana-demo-runbook.md
git commit -m "docs(bahana): Phase 0 provisioning and demo runbook"
```

---

## Verification

After all six tasks:

```bash
cd agent && pytest                                   # full agent suite
cd deploy/scripts/seed_demo_data && python3 -m pytest # full seeder suite
```

Both must be green. Then the live rehearsal in runbook step 8 is the real
verification — the unit tests prove the pieces, the rehearsal proves the demo.
