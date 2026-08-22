"""Per-tenant display vocabulary: a closed set of nouns, two profiles.

The product's words are automotive because its first customer is. A tenant in
banking or logistics is shown "Dealer Escalation Turnaround" and "Vehicle
Model" -- not merely off-brand for them but meaningless.

SCOPE IS DISPLAY STRINGS. This module changes what a human reads, never what
a system stores. `dealer_escalated_at` is a custom attribute already written
onto live conversations, `dealer_<slug>` are labels in production, and
`category_by_vehicle_model` is a BigQuery column BI reads -- renaming those is
a data migration plus a BI break, bought for text nobody sees.

THE LIST IS CLOSED. A term dictionary's known failure mode is growing until
every string is a lookup, leaving screens half-translated and text nobody can
grep for. A noun qualifies only if it is WRONG, not merely suboptimal, for a
tenant outside the originating industry: "Dealer" shown to a bank is wrong,
"Partner" shown to a dealership is plainer. PIC is deliberately absent -- it
is ordinary business English across SEA and generalising it would make every
tenant's UI worse to serve a problem no tenant has.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chatbot.platform.config import Settings


@dataclass(frozen=True)
class Term:
    singular: str
    plural: str
    # Stored, never derived. `.lower()` on "RSA Incident" gives "rsa
    # incident" and on "DMS/TSP" gives "dms/tsp" -- precisely the
    # half-broken output that makes people stop trusting a terminology layer.
    lower: str
    # Same reasoning as `lower`, for the plural. `.lower()` on "RSA
    # Incidents" is just as broken as it is on the singular -- an acronym
    # doesn't stop being an acronym because there's more than one of it.
    plural_lower: str


TERM_KEYS: tuple[str, ...] = (
    "partner",
    "partner_principal",
    "partner_owner",
    "partner_rep",
    "asset",
    "asset_model",
    "asset_id",
    "asset_serial",
    "field_incident",
    "job_no",
    "partner_system",
)

PROFILES: dict[str, dict[str, Term]] = {
    "generic": {
        "partner": Term("Partner", "Partners", "partner", "partners"),
        "partner_principal": Term(
            "Partner Manager", "Partner Managers", "partner manager", "partner managers"
        ),
        "partner_owner": Term(
            "Partner Owner", "Partner Owners", "partner owner", "partner owners"
        ),
        "partner_rep": Term("Partner Rep", "Partner Reps", "partner rep", "partner reps"),
        "asset": Term("Asset", "Assets", "asset", "assets"),
        "asset_model": Term("Asset Type", "Asset Types", "asset type", "asset types"),
        "asset_id": Term("Asset ID", "Asset IDs", "asset ID", "asset IDs"),
        "asset_serial": Term("Serial No.", "Serial Nos.", "serial no.", "serial nos."),
        "field_incident": Term(
            "Field Incident", "Field Incidents", "field incident", "field incidents"
        ),
        "job_no": Term("Job No.", "Job Nos.", "job no.", "job nos."),
        "partner_system": Term(
            "Business System", "Business Systems", "business system", "business systems"
        ),
    },
    # Mirrors the strings the fork ships today, so the next automotive
    # customer is a profile selection rather than a fork.
    "automotive": {
        "partner": Term("Dealer", "Dealers", "dealer", "dealers"),
        "partner_principal": Term(
            "Dealer Principal", "Dealer Principals", "dealer principal", "dealer principals"
        ),
        "partner_owner": Term(
            "Dealer Owner", "Dealer Owners", "dealer owner", "dealer owners"
        ),
        "partner_rep": Term("Dealer CRE", "Dealer CREs", "dealer CRE", "dealer CREs"),
        "asset": Term("Vehicle", "Vehicles", "vehicle", "vehicles"),
        "asset_model": Term(
            "Vehicle Model", "Vehicle Models", "vehicle model", "vehicle models"
        ),
        "asset_id": Term("Vehicle No.", "Vehicle Nos.", "vehicle no.", "vehicle nos."),
        "asset_serial": Term(
            "Chassis No.", "Chassis Nos.", "chassis no.", "chassis nos."
        ),
        "field_incident": Term(
            "RSA Incident", "RSA Incidents", "RSA incident", "RSA incidents"
        ),
        "job_no": Term("WIP No.", "WIP Nos.", "WIP no.", "WIP nos."),
        "partner_system": Term("DMS/TSP", "DMS/TSP", "DMS/TSP", "DMS/TSP"),
    },
}

_DEFAULT_PROFILE = "automotive"


def resolve_profile(stored: str | None, settings: Settings) -> str:
    """stored -> TERM_PROFILE -> built-in default.

    Deliberately NOT keyed on the tenant name. `config.py`'s `app_environment`
    already settled that argument: a guard whose answer is guessed from a name
    someone chose for unrelated reasons is one rename away from being wrong in
    the dangerous direction.

    An unknown stored value falls back rather than raising -- a typo in one
    tenant's config must not 500 every page load in that tenant.
    """
    if stored in PROFILES:
        return stored  # type: ignore[return-value]
    env_choice = getattr(settings, "term_profile", _DEFAULT_PROFILE)
    return env_choice if env_choice in PROFILES else _DEFAULT_PROFILE


def resolve_terms(
    profile: str, overrides: dict | None = None
) -> dict[str, dict[str, str]]:
    """Flatten a profile to JSON, applying per-noun overrides on top.

    Overrides are partial: a tenant renaming only the singular keeps the
    profile's plural and lowercase rather than blanking them. Unknown keys are
    ignored, so a stale override left by a retired noun cannot break a page.
    """
    table = PROFILES.get(profile) or PROFILES[_DEFAULT_PROFILE]
    resolved: dict[str, dict[str, str]] = {
        key: {
            "singular": term.singular,
            "plural": term.plural,
            "lower": term.lower,
            "plural_lower": term.plural_lower,
        }
        for key, term in table.items()
    }
    for key, patch in (overrides or {}).items():
        if key not in resolved or not isinstance(patch, dict):
            continue
        for field in ("singular", "plural", "lower", "plural_lower"):
            value = patch.get(field)
            if isinstance(value, str) and value:
                resolved[key][field] = value
    return resolved
