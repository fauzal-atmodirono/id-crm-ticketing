"""Render a Chatwoot contact's stored profile into a prompt section.

Pure and side-effect free: the orchestrator does the fetching, this decides
what the model is allowed to see and told to do with it.

Four decisions are load-bearing:

**An unrecognised profile yields the empty string, not an empty heading.**
Every other tenant's contacts carry a different attribute set (`vehicle_no`,
`vehicle_model`, ...). Those must produce today's prompt byte for byte --
`_build_system_prompt` appends nothing when this returns "". A "Customer
profile:" heading with no fields under it would be a behaviour change for
every existing tenant, and a confusing one for the model.

**The eligible set is handed over, never widened.** The catalogue and the
suitability rule live in the seeder (`nasabah.offer_for`) and the warehouse
(`dim_offer_eligibility`); by the time the model sees anything, the decision
of *which products are legal for this customer* is already made. What the
model chooses is only which of those already-legal products fits the turn.

That is a deliberate change from the original rule, which named exactly one
product and forbade every other. A live replay of the demo transcript
(`deploy/scripts/bahana_replay.py`, 2026-08-25) showed all three personas
dead-ending on the same turn -- the one where the customer declines the staged
offer -- with the model citing the single-product rule as its reason for
handing off. Konservatif's handoff reason quoted it almost verbatim: *"tidak
dapat merekomendasikan produk di luar penawaran hubungan yang sudah
ditentukan"*. A rule that makes the bot quit the moment a customer says "not
that one" is not a safety property, it is a bug with a compliance-shaped
excuse. The guarantee a reviewer actually needs -- that the model can never
reach a product outside this customer's suitability-checked set -- is
unchanged, and is pinned by `test_forbids_reaching_outside_the_eligible_set`.

**Answering the customer comes first.** A model handed an offer will lead with
it unless told not to. The offer is a thing to weave in when it fits, not the
purpose of the reply -- and in the demo, a bot that ignores the actual question
to pitch a product is the failure mode everyone in the room will notice.

**The profile is context, not a script.** Handed a labelled field list and no
instruction, the model reads it back as a labelled field list -- which is
exactly what the demo transcript showed, and why the output read as templated
despite being generated fresh every turn. `_PROFILE_INSTRUCTIONS` says to
quote only what the question calls for.
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
    ("holdings_sectors", "Holdings by sector"),
    ("days_since_last_transaction", "Days since last transaction"),
    ("product_gaps", "Products not yet held"),
    # Captured in conversation by `record_investor_preference`, not from the
    # back office. Appended rather than interleaved so the portfolio fields
    # above keep the order they already render in.
    ("investor_goal", "Stated goal"),
    ("investor_horizon", "Investment horizon"),
    ("investor_experience", "Investing experience"),
)

# How both writers of `product_gaps` -- the seeder's
# `build_nasabah_custom_attributes` and the warehouse's `v_nasabah_profile`
# view -- spell "none". They write the phrase rather than an empty value on
# purpose (a blank field is ambiguous between "none" and "no data"), so this
# module has to recognise it rather than treating it as a product name.
_NO_ITEMS = "tidak ada"

_PROFILE_INSTRUCTIONS = (
    "Use this profile to make the conversation specific to this customer, but "
    "do not read it back as a list of fields -- quote only the one or two "
    "details that bear on what they actually asked. The figures above are the "
    "CRM's record: never invent, round, or adjust them. Prefer closing with a "
    "question that moves the conversation forward over a generic "
    "\"is there anything else?\". "
    "Where an investing-experience level is recorded, match your explanation "
    "to it: explain what a product actually is to a beginner, and do not "
    "explain the basics to an experienced investor. The stated goal and "
    "horizon are what the customer told us in conversation, not their "
    "official risk profile -- use them for framing, never to justify a "
    "product their risk profile does not allow."
)

_ELIGIBLE_INSTRUCTIONS = (
    "Answer the customer's actual question first and completely; a product "
    "only ever enters the reply once that is done, and may be left out "
    "entirely. Lead with the offer named above when a product does fit the "
    "moment. A greeting, a pleasantry, or a question with no product angle is "
    "not such a moment -- answer it and stop, and let the customer say "
    "something you can build on.\n"
    "If the customer is lukewarm about that offer, declines it, or asks for "
    "something different, you MAY name any product from the eligible list "
    "above instead -- every product on these lists has already been "
    "suitability-checked against this customer's recorded risk profile. "
    "Never name, invent, or imply a product that is not on one of those "
    "lists.\n"
    "If what the customer wants falls outside those lists, say plainly that "
    "it does not match their recorded risk profile, and offer to have a "
    "relationship manager review that profile with them. Do not simply close "
    "the topic.\n"
    "The rationale above is the CRM's internal note on why this offer was "
    "selected. It is working material, not customer-facing copy -- never "
    "quote or paraphrase it back at the customer as though it were something "
    "you are telling them.\n"
    "If, and only if, the customer asks you to CARRY OUT a transaction or an "
    "account change -- subscribe, place an order, transfer funds, change "
    "their bank details -- hand off to a human rather than explaining that "
    "you cannot. Declining without routing anywhere leaves them stuck.\n"
    "That rule does not reach ordinary questions. Describing what the "
    "customer's own record says -- what they hold, how concentrated it is, "
    "which sectors, how long since they traded, what a product is -- is "
    "reporting their own data back to them, not giving advice. Answer those "
    "yourself; handing them to a human is a worse answer, not a safer one.\n"
    "Never quote a return, yield, price, or fee. These are relationship "
    "offers, not investment advice: do not recommend buying or selling any "
    "specific security."
)


def _eligible_alternatives(attributes: dict, offer: str) -> list[str]:
    """The suitability-checked products this customer does not hold, minus the
    one already staged as the primary offer.

    `product_gaps` is already exactly "eligible for this risk profile, and not
    owned" at both writers -- the seeder intersects the risk catalogue with
    holdings (`nasabah._gaps_for`), and the view joins `dim_offer_eligibility`
    against `fact_product_ownership`. So the alternatives need no new data and
    no second suitability rule; subtracting the staged offer is the whole
    computation.

    Dropping the offer matters for how it reads, not just for tidiness: listed
    twice under two headings, it looks like two separate products, and the
    model pitches the "alternative" that is the same thing it just offered.
    """
    raw = str(attributes.get("product_gaps") or "")
    seen: set[str] = set()
    out: list[str] = []
    for item in raw.split(","):
        name = item.strip()
        key = name.lower()
        if not name or key == _NO_ITEMS or key == offer.strip().lower():
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


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
    parts.append(_PROFILE_INSTRUCTIONS)

    if offer:
        offer_block = f"## Relationship offer selected for this customer\n- {offer}"
        if rationale:
            offer_block += f"\n- Why it was selected: {rationale}"
        parts.append(offer_block)

        alternatives = _eligible_alternatives(attributes, offer)
        if alternatives:
            parts.append(
                "## Other products this customer is eligible for\n"
                + "\n".join(f"- {name}" for name in alternatives)
            )

        parts.append(_ELIGIBLE_INSTRUCTIONS)

    return "\n\n".join(parts)
