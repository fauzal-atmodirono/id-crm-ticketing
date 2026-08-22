"""Deterministic synthetic-nasabah generator for the Bahana Phase 0 demo.

Pure, in-memory generation only: no I/O, no network. Produces `DemoNasabah`
records that `client.create_nasabah_contact` writes to Chatwoot as contact
custom attributes, which Chatwoot renders in the conversation sidebar with
no fork patch (see the design spec §5.1 for why that matters).

Three things here are deliberate rather than incidental:

**The offer is selected in code, from the risk profile.** Suitability written
as a prompt instruction leaks eventually; suitability written as a lookup
cannot. The language model is later handed one offer and asked to phrase it,
never to choose one -- so a Konservatif nasabah is structurally incapable of
being pitched an equity product, whatever the model is prompted with. See
design spec §4.3.

**The offer also prefers a product the nasabah doesn't already hold.**
Re-pitching a held product is a believability bug, not just a wasted offer:
the rationale text asserts things like "belum terdiversifikasi lewat reksa
dana" ("not yet diversified into mutual funds"), which is simply false for a
nasabah who already holds that fund. `offer_for` takes an `exclude` set for
exactly this, applied by `_make_nasabah` from `held_products`. The exclusion
never widens the search to another risk profile's catalogue, even as a
fallback -- suitability is the one rule this module will not bend to find a
novel product. A nasabah who already holds everything suitable for their
profile simply gets re-offered from that same (unfiltered) catalogue; there
is nothing else safe to offer them.

**`product_gaps` is filtered to that same risk-profile catalogue, not the
whole product universe.** Offer *selection* was always suitability-safe --
that's the paragraph above -- but until this filter existed, `product_gaps`
(what the rendered prompt calls "products not yet held") was the complement
of `held_products` across every entry in `_PRODUCTS`, unfiltered. A
Konservatif nasabah's prompt could therefore name `Reksa Dana Saham`, an
Agresif-only catalogue entry -- nothing prevented the model from mentioning
it except a sentence of prompt instruction, and design spec §7.4 is explicit
that a suitability rule enforced only in a prompt eventually leaks. `_gaps_for`
reuses `_catalogue_for` -- the exact lookup `offer_for` draws from, not a
second copy of it -- so the set of products a nasabah's context can ever
mention and the set `offer_for` could ever choose from are, structurally,
the same set. Falls back to the Konservatif catalogue for an unrecognised
risk profile, same as `offer_for`, for the same reason: degrade to the safest
set, never to unfiltered.

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

# Product universe. `product_gaps` (see `_gaps_for`) is this set minus what
# the nasabah holds and minus whatever falls outside that nasabah's own
# risk-profile catalogue -- which is what makes "belum memiliki X" both a
# fact and a suitable one, rather than merely a fact.
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


def _catalogue_for(risk_profile: str) -> list[tuple[str, str]]:
    """The `(offer, rationale)` catalogue for one risk profile -- the single
    source of truth both `offer_for` (what can be chosen) and `_gaps_for`
    (what can be mentioned as not-yet-held) draw from, so the two can never
    diverge. Falls back to the Konservatif catalogue for a profile value
    this module doesn't recognise: degrade to "the safest catalogue", never
    to "unfiltered" and never to "crashed the seeder"."""
    return _OFFERS_BY_RISK.get(risk_profile, _OFFERS_BY_RISK["Konservatif"])


def offer_for(
    risk_profile: str,
    rnd: random.Random,
    exclude: frozenset[str] = frozenset(),
) -> tuple[str, str]:
    """The (offer, rationale) appropriate for one risk profile.

    Public and pure so a test can assert the suitability rule directly
    without generating a population. An unknown profile falls back to the
    most conservative catalogue rather than raising -- a profile value this
    module doesn't recognise must degrade to "offered something safe", never
    to "crashed the seeder" and never to "offered something aggressive".

    `exclude` (default empty, so callers that don't pass it get today's
    behaviour byte-for-byte) drops any catalogue entry whose offer name is
    in it -- `_make_nasabah` passes the nasabah's already-held products, so
    they aren't re-offered something they already hold. If every entry in
    the profile's catalogue is excluded, the filter is dropped and the
    unfiltered catalogue is used instead: suitability is never relaxed to
    find a novel product, so a Konservatif nasabah who holds both
    Konservatif offers is simply re-offered one of the two, never something
    from another profile's catalogue.
    """
    catalogue = _catalogue_for(risk_profile)
    candidates = [item for item in catalogue if item[0] not in exclude] or catalogue
    return rnd.choice(candidates)


def _gaps_for(risk_profile: str, held_products: list[str]) -> list[str]:
    """The products to render as "not yet held": `_PRODUCTS` intersected
    with `risk_profile`'s own suitable catalogue (via `_catalogue_for`),
    minus whatever's already held.

    Pure, like `offer_for`, for the same reason: a test can assert the
    suitability rule directly rather than hoping a generated population
    happens to exercise it. Order follows `_PRODUCTS`, matching the
    pre-filter behaviour byte-for-byte for whichever entries survive."""
    suitable = {name for name, _ in _catalogue_for(risk_profile)}
    return [p for p in _PRODUCTS if p not in held_products and p in suitable]


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
    product_gaps = _gaps_for(risk_profile, held_products)
    holdings = (
        rnd.sample(_TICKERS, k=rnd.randint(1, 4)) if "Saham" in held_products else []
    )

    days_since_last_transaction = rnd.randint(0, 400)
    offer, rationale = offer_for(risk_profile, rnd, exclude=frozenset(held_products))

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
