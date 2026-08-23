from __future__ import annotations

from chatbot.features.tenant_config.term_dictionary import (
    PROFILES,
    TERM_FIELDS,
    TERM_KEYS,
    resolve_profile,
    resolve_terms,
)
from chatbot.platform.config import Settings


def test_unset_everything_resolves_to_automotive() -> None:
    """Proton's exact situation: no stored row, no TERM_PROFILE. It must keep
    saying Dealer/Vehicle/RSA/WIP with nothing written and nothing deployed.
    If this test ever goes green on "generic", proton's vocabulary flips on
    its next image pull."""
    assert resolve_profile(None, Settings()) == "automotive"


def test_env_var_beats_the_builtin_default() -> None:
    assert resolve_profile(None, Settings(term_profile="generic")) == "generic"


def test_stored_profile_beats_the_env_var() -> None:
    assert resolve_profile("generic", Settings(term_profile="automotive")) == "generic"


def test_unknown_stored_profile_falls_back_rather_than_raising() -> None:
    assert resolve_profile("banking", Settings()) == "automotive"


def test_both_profiles_define_every_key() -> None:
    """A key added to one column and forgotten in the other renders as a
    literal key name on somebody's screen."""
    for name, table in PROFILES.items():
        assert set(table) == set(TERM_KEYS), name


def test_automotive_mirrors_the_wording_shipped_today() -> None:
    """Asserted against the literal current strings so the preset is provably
    a mirror rather than an approximation."""
    auto = PROFILES["automotive"]
    assert auto["partner"].singular == "Dealer"
    assert auto["partner"].plural == "Dealers"
    assert auto["asset"].singular == "Vehicle"
    assert auto["asset_model"].singular == "Vehicle Model"
    assert auto["asset_id"].singular == "Vehicle No."
    assert auto["asset_serial"].singular == "Chassis No."
    assert auto["field_incident"].singular == "RSA Incident"
    assert auto["job_no"].singular == "WIP No."
    assert auto["partner_system"].singular == "DMS/TSP"
    assert auto["partner_principal"].singular == "Dealer Principal"
    assert auto["partner_owner"].singular == "Dealer Owner"
    assert auto["partner_rep"].singular == "Dealer CRE"


def test_generic_is_industry_neutral() -> None:
    gen = PROFILES["generic"]
    assert gen["partner"].singular == "Partner"
    assert gen["asset"].singular == "Asset"
    assert gen["field_incident"].singular == "Field Incident"
    assert gen["job_no"].singular == "Job No."


def test_acronym_lowercase_is_stored_not_derived() -> None:
    """`.lower()` on "RSA Incident" gives "rsa incident", which is exactly the
    half-broken output that makes people distrust a terminology layer."""
    assert PROFILES["automotive"]["field_incident"].lower == "RSA incident"
    assert PROFILES["automotive"]["partner_system"].lower == "DMS/TSP"


def test_plural_acronym_lowercase_is_stored_not_derived() -> None:
    """The plural has exactly the same trap: `.lower()` on "RSA Incidents"
    gives "rsa incidents". `plural_lower` must be a stored field, not a
    `.lower()` of `plural` computed at call time -- this test must fail
    against a derived implementation."""
    assert PROFILES["automotive"]["field_incident"].plural_lower == "RSA incidents"
    assert PROFILES["automotive"]["partner_system"].plural_lower == "DMS/TSP"
    assert PROFILES["automotive"]["partner_rep"].plural_lower == "dealer CREs"
    assert PROFILES["automotive"]["job_no"].plural_lower == "WIP nos."


def test_resolve_terms_returns_a_flat_serialisable_map() -> None:
    terms = resolve_terms("generic", None)
    assert terms["partner"] == {
        "singular": "Partner",
        "plural": "Partners",
        "lower": "partner",
        "plural_lower": "partners",
    }


def test_overrides_apply_on_top_of_the_profile() -> None:
    terms = resolve_terms("generic", {"partner": {"singular": "Branch", "plural": "Branches"}})
    assert terms["partner"]["singular"] == "Branch"
    assert terms["partner"]["plural"] == "Branches"
    # Unspecified fields keep the profile's value rather than becoming empty.
    assert terms["partner"]["lower"] == "partner"
    assert terms["partner"]["plural_lower"] == "partners"


def test_unknown_override_keys_are_ignored_not_raised() -> None:
    terms = resolve_terms("generic", {"nonsense": {"singular": "X"}})
    assert "nonsense" not in terms
    assert terms["partner"]["singular"] == "Partner"


def test_pic_is_not_a_dictionary_key() -> None:
    """Deliberate: PIC reads correctly to a bank as readily as to a
    dealership, and generalising it would make every tenant's UI worse."""
    assert "pic" not in TERM_KEYS


def test_term_fields_is_derived_not_hand_mirrored() -> None:
    """`resolve_terms` and `custom_features_router._OVERRIDE_FIELDS` both
    read this tuple instead of hand-copying it. If a field is ever added to
    (or removed from) `Term` without updating this expectation, this test
    goes red instead of the two call sites silently drifting apart again --
    that already happened once, for `plural_lower`."""
    assert TERM_FIELDS == ("singular", "plural", "lower", "plural_lower")


def test_add_tenant_script_writes_generic_for_new_tenants() -> None:
    """The product's default vocabulary is a vertical, which is only safe
    because provisioning always overrides it. If this line is ever dropped, a
    new bank tenant opens saying "Dealer"."""
    from pathlib import Path

    script = Path(__file__).parents[7] / "deploy" / "scripts" / "add-tenant.sh"
    assert "TERM_PROFILE=generic" in script.read_text()


def test_add_tenant_script_pins_the_custom_chatwoot_image() -> None:
    """A new tenant must boot OUR fork, not upstream Chatwoot.

    `docker-compose.tenant.yml` falls back to `chatwoot/chatwoot:v4.15.1` when
    `CHATWOOT_IMAGE` is empty OR unset (`:-` fires on both), and `example.env`
    ships it blank. So a tenant provisioned without this line silently comes up
    on stock upstream: Captain and the SAML settings that patches 0029/0032
    remove are back, and none of the Knowledge / RBAC / SLA / Audit Log pages
    this platform is built around exist at all. It looks like a working CRM,
    which is why it went unnoticed on the bahana tenant.
    """
    from pathlib import Path

    script = Path(__file__).parents[7] / "deploy" / "scripts" / "add-tenant.sh"
    text = script.read_text()
    assert "CHATWOOT_IMAGE=" in text, "add-tenant.sh must write CHATWOOT_IMAGE"
    assert "proton-chatwoot:" in text, "it must pin OUR fork image, not upstream"
    # Guard the direction of the mistake: never provision onto upstream.
    assert "CHATWOOT_IMAGE=chatwoot/chatwoot" not in text
