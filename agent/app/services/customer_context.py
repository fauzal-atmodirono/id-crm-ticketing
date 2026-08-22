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
