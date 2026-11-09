"""Turn the model's answers to the four profiling questions into a fixed
vocabulary of contact attributes.

Pure and side-effect free, for the same reason `customer_context` is: the
orchestrator does the writing, this decides what is allowed to be written.

Two decisions are load-bearing:

**An unrecognised answer is dropped, never guessed.** The model is asked for an
enum and will occasionally return prose. Mapping "something like retirement I
guess" onto `Dana pensiun` invents a fact about a customer; leaving the field
empty asks the question again next turn, which is the cheaper mistake.

**`risk_profile` cannot be written from here.** There is no mapping for it and
`canonical_attributes` returns only keys in `ATTRIBUTE_KEYS`, so no prompt, no
model output and no future caller can reach the field that gates product
eligibility (design spec §2.3, §5.3). `implied_risk_tier` exists to tell a
human that the customer's answers and their KYC record disagree -- it is a
notification, not a write.
"""

from __future__ import annotations

_GOALS = {
    "retirement": "Dana pensiun",
    "education": "Pendidikan",
    "house": "Membeli rumah",
    "wealth_growth": "Pertumbuhan aset",
    "emergency_fund": "Dana darurat",
    "other": "Lainnya",
}

_HORIZONS = {
    "short": "< 1 tahun",
    "medium": "1-3 tahun",
    "long": "3-10 tahun",
    "very_long": "> 10 tahun",
}

_DRAWDOWN = {
    "sell_all": "Menjual seluruhnya",
    "sell_some": "Menjual sebagian",
    "hold": "Tetap menahan",
    "buy_more": "Menambah posisi",
}

_EXPERIENCE = {
    "beginner": "Pemula",
    "intermediate": "Menengah",
    "experienced": "Berpengalaman",
}

# Which risk tier each drawdown answer implies. Used ONLY to raise a review
# flag for a human when it disagrees with the KYC record -- see the module
# docstring. 1 = Konservatif, 2 = Moderat, 3 = Agresif.
_IMPLIED_TIER = {"sell_all": 1, "sell_some": 1, "hold": 2, "buy_more": 3}

# The tier each recorded risk profile sits at, so a caller can compare the two
# without re-deriving the ordering. Lives here rather than in the orchestrator
# because it is the same vocabulary `_IMPLIED_TIER` maps onto, and the two
# drifting apart is exactly how a divergence check silently stops firing.
RECORDED_TIER = {"Konservatif": 1, "Moderat": 2, "Agresif": 3}

_FIELDS: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("goal", "investor_goal", _GOALS),
    ("horizon", "investor_horizon", _HORIZONS),
    ("drawdown_reaction", "investor_drawdown_reaction", _DRAWDOWN),
    ("experience", "investor_experience", _EXPERIENCE),
)

ATTRIBUTE_KEYS: tuple[str, ...] = tuple(
    attribute for _, attribute, _ in _FIELDS
) + ("preference_captured_at",)


def _lookup(args: dict, arg_name: str, vocabulary: dict[str, str]) -> str | None:
    raw = args.get(arg_name)
    if not isinstance(raw, str):
        return None
    return vocabulary.get(raw.strip().lower())


def canonical_attributes(args: object, captured_at: str) -> dict[str, str]:
    """The contact attributes to merge for this capture, or `{}`.

    Accepts `object` rather than `dict` because this is fed straight from a
    model's function-call arguments, where a null or a scalar can appear where
    an object belongs. Returning `{}` is the fail-open path.
    """
    if not isinstance(args, dict):
        return {}

    out: dict[str, str] = {}
    for arg_name, attribute, vocabulary in _FIELDS:
        value = _lookup(args, arg_name, vocabulary)
        if value is not None:
            out[attribute] = value

    if not out:
        return {}
    out["preference_captured_at"] = captured_at
    return out


def implied_risk_tier(args: object) -> int | None:
    """The risk tier this customer's drawdown answer implies, or None.

    Never written anywhere near `risk_profile`. Its only consumer applies a
    review label so a licensed human can reconcile the disagreement.
    """
    if not isinstance(args, dict):
        return None
    raw = args.get("drawdown_reaction")
    if not isinstance(raw, str):
        return None
    return _IMPLIED_TIER.get(raw.strip().lower())


def recorded_risk_tier(risk_profile: object) -> int | None:
    """The tier of a `risk_profile` value as stored on the contact, or None
    for anything unrecognised. Unrecognised must mean "no comparison", never
    "assume conservative" -- a wrong guess here fires a review flag at a
    customer whose profile was fine."""
    if not isinstance(risk_profile, str):
        return None
    return RECORDED_TIER.get(risk_profile.strip().title())
