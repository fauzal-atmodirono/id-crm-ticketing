# Term Dictionary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the CRM's vocabulary industry-neutral by default, with an `automotive` profile that mirrors Proton's current wording exactly, so the product can be sold outside automotive without a fork.

**Architecture:** A closed registry of eleven nouns, two profiles, and a resolution chain of `stored profile → TERM_PROFILE env → built-in default`. Terms ride on the custom-features response so the SPA takes no extra round trip, and render through a `t()` helper on the same composable. Display strings only — no data key, API field, warehouse column or identifier is renamed.

**Tech Stack:** FastAPI, pydantic-settings, google-cloud-firestore, pytest (`asyncio_mode=auto`), Vue 3 in the Chatwoot fork.

**Spec:** `docs/superpowers/specs/2026-08-22-term-dictionary-design.md`

**Depends on:** `docs/superpowers/plans/2026-08-22-custom-feature-switchboard.md` — Tasks 3, 4 and 6 of that plan must be merged first. This plan extends that store's document, that router's response and that composable.

## Global Constraints

- **Display strings only.** The dictionary changes what a human reads, never what a system stores. Do not touch `dealer_escalated_at` (a custom attribute on live conversations), `dealer_<slug>` labels, `category_by_vehicle_model` (a BigQuery column BI reads), `vehicle_no`/`vehicle_model`/`vehicle_plate`/`vehicle_chassis` (API contracts, 290+ refs), or any Python/JS identifier.
- **The noun list is closed.** A noun qualifies only if it is *wrong* — not merely suboptimal — for a tenant outside automotive. Do not add nouns while implementing.
- **`TERM_PROFILE` defaults to `automotive`.** This is default-preserving: unset means today's behaviour, byte-identical. Proton must need no row, no env var and no deploy.
- **Never derive the profile from a tenant name.** `config.py`'s `app_environment` already rejected that reasoning; there is no `{"proton": "automotive"}` lookup anywhere.
- **`lower` is stored, never derived** by `.lower()` — that yields "rsa incident" and "dms integration".
- **PIC stays.** It is ordinary business English across SEA and reads correctly to any tenant. It is not a dictionary key.
- **Run tests from `backend/apps/backend`** with `uv run pytest`. A whole-suite run needs `GOOGLE_API_KEY=test-dummy`.
- **Fork patches: no upstream checkout in this sandbox.** New-file patches are pure additions. Modifications must have context lines transcribed verbatim from an existing patch touching that file.

---

### Task 1: The term registry and resolution chain

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/tenant_config/term_dictionary.py`
- Modify: `backend/apps/backend/src/chatbot/platform/config.py`
- Test: `backend/apps/backend/src/chatbot/features/tenant_config/test_term_dictionary.py`

**Interfaces:**
- Consumes: nothing from the switchboard plan.
- Produces:
  - `Term` dataclass: `singular: str`, `plural: str`, `lower: str`.
  - `TERM_KEYS: tuple[str, ...]` — the eleven keys.
  - `PROFILES: dict[str, dict[str, Term]]` — `"generic"` and `"automotive"`.
  - `resolve_profile(stored: str | None, settings: Settings) -> str`
  - `resolve_terms(profile: str, overrides: dict | None) -> dict[str, dict[str, str]]`
  - `Settings.term_profile: Literal["generic", "automotive"] = "automotive"`

- [ ] **Step 1: Write the failing tests**

Create `test_term_dictionary.py`:

```python
from __future__ import annotations

from chatbot.features.tenant_config.term_dictionary import (
    PROFILES,
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


def test_resolve_terms_returns_a_flat_serialisable_map() -> None:
    terms = resolve_terms("generic", None)
    assert terms["partner"] == {
        "singular": "Partner",
        "plural": "Partners",
        "lower": "partner",
    }


def test_overrides_apply_on_top_of_the_profile() -> None:
    terms = resolve_terms("generic", {"partner": {"singular": "Branch", "plural": "Branches"}})
    assert terms["partner"]["singular"] == "Branch"
    assert terms["partner"]["plural"] == "Branches"
    # Unspecified fields keep the profile's value rather than becoming empty.
    assert terms["partner"]["lower"] == "partner"


def test_unknown_override_keys_are_ignored_not_raised() -> None:
    terms = resolve_terms("generic", {"nonsense": {"singular": "X"}})
    assert "nonsense" not in terms
    assert terms["partner"]["singular"] == "Partner"


def test_pic_is_not_a_dictionary_key() -> None:
    """Deliberate: PIC reads correctly to a bank as readily as to a
    dealership, and generalising it would make every tenant's UI worse."""
    assert "pic" not in TERM_KEYS
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/tenant_config/test_term_dictionary.py -v
```

Expected: FAIL — `ModuleNotFoundError: ...term_dictionary`.

- [ ] **Step 3: Add the setting**

In `platform/config.py`, beside the other feature settings:

```python
    # --- Term dictionary -------------------------------------------------
    # Which vocabulary this tenant's UI renders. Default "automotive" is
    # DEFAULT-PRESERVING, not a product statement: unset means today's
    # wording, byte-identical, which is what every tenant deployed before the
    # dictionary existed needs. proton has no value set and must keep saying
    # Dealer/Vehicle/RSA/WIP with nothing written and nothing deployed.
    #
    # The cost is that the product's default vocabulary is a vertical, so a
    # tenant provisioned without TERM_PROFILE=generic would show a bank the
    # word "Dealer". That is the safer failure: it is loud and immediate,
    # caught the first time anyone opens the new tenant. The other direction
    # is silent and lands on a live customer during an unrelated deploy.
    # add-tenant.sh writes TERM_PROFILE=generic so neither happens.
    #
    # A superadmin's stored choice overrides this; see term_dictionary.py.
    term_profile: Literal["generic", "automotive"] = "automotive"
```

- [ ] **Step 4: Implement the registry**

Create `term_dictionary.py`:

```python
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
        "partner": Term("Partner", "Partners", "partner"),
        "partner_principal": Term("Partner Manager", "Partner Managers", "partner manager"),
        "partner_owner": Term("Partner Owner", "Partner Owners", "partner owner"),
        "partner_rep": Term("Partner Rep", "Partner Reps", "partner rep"),
        "asset": Term("Asset", "Assets", "asset"),
        "asset_model": Term("Asset Type", "Asset Types", "asset type"),
        "asset_id": Term("Asset ID", "Asset IDs", "asset ID"),
        "asset_serial": Term("Serial No.", "Serial Nos.", "serial no."),
        "field_incident": Term("Field Incident", "Field Incidents", "field incident"),
        "job_no": Term("Job No.", "Job Nos.", "job no."),
        "partner_system": Term("Business System", "Business Systems", "business system"),
    },
    # Mirrors the strings the fork ships today, so the next automotive
    # customer is a profile selection rather than a fork.
    "automotive": {
        "partner": Term("Dealer", "Dealers", "dealer"),
        "partner_principal": Term("Dealer Principal", "Dealer Principals", "dealer principal"),
        "partner_owner": Term("Dealer Owner", "Dealer Owners", "dealer owner"),
        "partner_rep": Term("Dealer CRE", "Dealer CREs", "dealer CRE"),
        "asset": Term("Vehicle", "Vehicles", "vehicle"),
        "asset_model": Term("Vehicle Model", "Vehicle Models", "vehicle model"),
        "asset_id": Term("Vehicle No.", "Vehicle Nos.", "vehicle no."),
        "asset_serial": Term("Chassis No.", "Chassis Nos.", "chassis no."),
        "field_incident": Term("RSA Incident", "RSA Incidents", "RSA incident"),
        "job_no": Term("WIP No.", "WIP Nos.", "WIP no."),
        "partner_system": Term("DMS/TSP", "DMS/TSP", "DMS/TSP"),
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
        }
        for key, term in table.items()
    }
    for key, patch in (overrides or {}).items():
        if key not in resolved or not isinstance(patch, dict):
            continue
        for field in ("singular", "plural", "lower"):
            value = patch.get(field)
            if isinstance(value, str) and value:
                resolved[key][field] = value
    return resolved
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/tenant_config/test_term_dictionary.py -v
```

Expected: PASS, 12 tests.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/tenant_config/term_dictionary.py \
        backend/apps/backend/src/chatbot/features/tenant_config/test_term_dictionary.py \
        backend/apps/backend/src/chatbot/platform/config.py
git commit -m "feat(tenant-config): term registry with generic and automotive profiles"
```

---

### Task 2: Persist the profile and overrides

Extends the switchboard's store document rather than adding a second one, so one Firestore read still serves the whole page load.

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/tenant_config/custom_features.py`
- Test: `backend/apps/backend/src/chatbot/features/tenant_config/test_custom_features.py`

**Interfaces:**
- Consumes: `CustomFeatureStore` (switchboard plan, Task 3).
- Produces:
  - `CustomFeatureStore.get_document() -> dict` — the whole doc, `{}` on failure.
  - `CustomFeatureStore.set_terms(profile: str | None, overrides: dict | None) -> None`
  - `stored_terms(document: dict) -> tuple[str | None, dict]` — `(profile, overrides)`.

- [ ] **Step 1: Write the failing tests**

Append to `test_custom_features.py`:

```python
from chatbot.features.tenant_config.custom_features import stored_terms


def test_stored_terms_of_an_empty_document_is_unset() -> None:
    """Unset, NOT "generic" — the caller must be able to tell "nobody chose"
    from "somebody chose generic", because those resolve differently."""
    assert stored_terms({}) == (None, {})


def test_stored_terms_reads_profile_and_overrides() -> None:
    doc = {"terms": {"profile": "generic", "overrides": {"partner": {"singular": "Branch"}}}}
    profile, overrides = stored_terms(doc)
    assert profile == "generic"
    assert overrides == {"partner": {"singular": "Branch"}}


def test_stored_terms_tolerates_a_malformed_terms_block() -> None:
    assert stored_terms({"terms": "nonsense"}) == (None, {})


def test_features_and_terms_share_one_document() -> None:
    doc = {"features": {"knowledge": True}, "terms": {"profile": "generic"}}
    assert enabled_features(doc.get("features") or {}) == ["knowledge"]
    assert stored_terms(doc)[0] == "generic"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/tenant_config/test_custom_features.py -v
```

Expected: FAIL — `ImportError: cannot import name 'stored_terms'`.

- [ ] **Step 3: Implement**

Add to `custom_features.py`:

```python
def stored_terms(document: dict) -> tuple[str | None, dict]:
    """Pull the term block out of the shared document.

    Returns `None` for an unset profile rather than a default, because the
    caller's fallback chain needs to distinguish "nobody has chosen" from
    "somebody chose generic" -- those resolve to different vocabularies.
    """
    block = document.get("terms")
    if not isinstance(block, dict):
        return None, {}
    profile = block.get("profile")
    overrides = block.get("overrides")
    return (
        profile if isinstance(profile, str) else None,
        overrides if isinstance(overrides, dict) else {},
    )
```

Then refactor `CustomFeatureStore` so both readers share one fetch:

```python
    async def get_document(self) -> dict:
        """The whole config document. Features and terms are one Firestore
        read because the SPA fetches both on every page load."""
        try:
            snap = await asyncio.to_thread(self._doc_ref().get)
        except Exception as e:
            _log.error("custom_feature_store_get_failed", error=str(e))
            return {}
        if not snap.exists:
            return {}
        return snap.to_dict() or {}

    async def get_all(self) -> dict[str, bool]:
        """Kept after the router moved to `get_document`. It is the store's
        narrow public read and is directly tested; a caller wanting only the
        feature map should not have to know the document's shape."""
        raw = (await self.get_document()).get("features") or {}
        return {str(k): bool(v) for k, v in raw.items()}

    async def set_terms(self, profile: str | None, overrides: dict | None) -> None:
        """Merge-write the term block. Only the fields given are touched, so
        setting a profile does not wipe a tenant's overrides."""
        block: dict = {}
        if profile is not None:
            block["profile"] = profile
        if overrides is not None:
            block["overrides"] = overrides
        if not block:
            return
        await asyncio.to_thread(self._doc_ref().set, {"terms": block}, merge=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/tenant_config/ -v
```

Expected: PASS — the four new tests plus every switchboard test still green.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/tenant_config/
git commit -m "feat(tenant-config): store the term profile alongside features"
```

---

### Task 3: Serve terms on the custom-features response

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/tenant_config/custom_features_router.py`
- Test: `backend/apps/backend/src/chatbot/features/tenant_config/test_custom_features_router.py`

**Interfaces:**
- Consumes: `resolve_profile`, `resolve_terms`, `PROFILES` (Task 1); `stored_terms`, `get_document`, `set_terms` (Task 2).
- Produces: `terms`, `term_profile` and `term_profiles` on the `GET` body; `POST /admin/custom-features/terms`.

- [ ] **Step 1: Write the failing tests**

Extend `_FakeStore` in `test_custom_features_router.py` to back a whole document:

```python
class _FakeDocStore:
    def __init__(self, document: dict | None = None) -> None:
        self.document = dict(document or {})

    async def get_document(self) -> dict:
        return dict(self.document)

    async def get_all(self) -> dict[str, bool]:
        return dict(self.document.get("features") or {})

    async def set(self, key: str, enabled: bool) -> None:
        self.document.setdefault("features", {})[key] = enabled

    async def set_terms(self, profile, overrides) -> None:
        block = self.document.setdefault("terms", {})
        if profile is not None:
            block["profile"] = profile
        if overrides is not None:
            block["overrides"] = overrides
```

Then add:

```python
def test_unconfigured_tenant_gets_automotive_terms() -> None:
    """Proton's situation. Nothing stored, nothing in env — it must read
    Dealer, not Partner."""
    res = _client(_FakeDocStore(), (9, False)).get("/admin/custom-features", headers=_SESSION)
    body = res.json()
    assert body["term_profile"] == "automotive"
    assert body["terms"]["partner"]["singular"] == "Dealer"


def test_stored_generic_profile_is_served() -> None:
    store = _FakeDocStore({"terms": {"profile": "generic"}})
    body = _client(store, (9, False)).get("/admin/custom-features", headers=_SESSION).json()
    assert body["term_profile"] == "generic"
    assert body["terms"]["partner"]["singular"] == "Partner"
    assert body["terms"]["field_incident"]["singular"] == "Field Incident"


def test_overrides_are_applied_to_the_served_terms() -> None:
    store = _FakeDocStore(
        {"terms": {"profile": "generic", "overrides": {"partner": {"singular": "Branch"}}}}
    )
    body = _client(store, (9, False)).get("/admin/custom-features", headers=_SESSION).json()
    assert body["terms"]["partner"]["singular"] == "Branch"


def test_terms_are_served_to_a_non_superadmin() -> None:
    """Unlike the registry, vocabulary is not privileged — every agent's UI
    needs it to render at all."""
    body = _client(_FakeDocStore(), (9, False)).get(
        "/admin/custom-features", headers=_SESSION
    ).json()
    assert body["terms"]
    assert body["is_superadmin"] is False


def test_superadmin_sees_the_selectable_profiles() -> None:
    body = _client(_FakeDocStore(), (1, False)).get(
        "/admin/custom-features", headers=_SESSION
    ).json()
    assert sorted(body["term_profiles"]) == ["automotive", "generic"]


def test_setting_the_profile_requires_superadmin() -> None:
    res = _client(_FakeDocStore(), (9, False)).post(
        "/admin/custom-features/terms", json={"profile": "generic"}, headers=_SESSION
    )
    assert res.status_code == 403


def test_superadmin_sets_the_profile() -> None:
    store = _FakeDocStore()
    res = _client(store, (1, False)).post(
        "/admin/custom-features/terms", json={"profile": "generic"}, headers=_SESSION
    )
    assert res.status_code == 200
    assert store.document["terms"]["profile"] == "generic"


def test_unknown_profile_is_rejected() -> None:
    res = _client(_FakeDocStore(), (1, False)).post(
        "/admin/custom-features/terms", json={"profile": "banking"}, headers=_SESSION
    )
    assert res.status_code == 400


def test_override_of_an_unknown_noun_is_rejected() -> None:
    """The list is closed. Accepting arbitrary keys is how a capped dictionary
    becomes an uncapped one."""
    res = _client(_FakeDocStore(), (1, False)).post(
        "/admin/custom-features/terms",
        json={"profile": "generic", "overrides": {"nonsense": {"singular": "X"}}},
        headers=_SESSION,
    )
    assert res.status_code == 400
```

`_FakeDocStore` REPLACES `_FakeStore` outright — rename it and update every
reference in the file, not just the `_client(...)` calls. In particular the
switchboard plan's Task 4 left a `_BrokenStore(_FakeStore)` subclass behind
(the one asserting a failed write returns 503); it must become
`_BrokenStore(_FakeDocStore)` or that test dies with a `NameError` the moment
the old class disappears.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/tenant_config/test_custom_features_router.py -v
```

Expected: FAIL — `KeyError: 'term_profile'`.

- [ ] **Step 3: Implement**

Add the imports and body model:

```python
from chatbot.features.tenant_config.custom_features import stored_terms
from chatbot.features.tenant_config.term_dictionary import (
    PROFILES,
    TERM_KEYS,
    resolve_profile,
    resolve_terms,
)


class TermsBody(BaseModel):
    profile: str | None = None
    overrides: dict | None = None
```

Rewrite `read` to fetch the document once and serve both halves:

```python
    @router.get("")
    async def read(identity: tuple[int, bool] = Depends(_identity)) -> dict:
        user_id, is_super_admin_type = identity
        superadmin = is_platform_superadmin(user_id, is_super_admin_type)

        document = await store.get_document()
        stored = {str(k): bool(v) for k, v in (document.get("features") or {}).items()}
        stored_profile, overrides = stored_terms(document)
        profile = resolve_profile(stored_profile, settings)

        registry: list[dict] = []
        behavior: dict[str, bool] = {}
        if superadmin:
            registry = [
                {
                    "key": f.key,
                    "label": f.label,
                    "group": f.group,
                    "enabled": bool(stored.get(f.key, False)),
                }
                for f in CUSTOM_FEATURE_REGISTRY.values()
            ]
            behavior = {
                key: bool(getattr(settings, attr, False))
                for key, attr in BEHAVIOR_FLAGS.items()
            }

        return {
            "features": enabled_features(stored),
            "is_superadmin": superadmin,
            "registry": registry,
            "behavior": behavior,
            # Vocabulary is served to EVERY session, unlike the registry: the
            # UI cannot render a heading without it.
            "terms": resolve_terms(profile, overrides),
            "term_profile": profile,
            "term_profiles": sorted(PROFILES) if superadmin else [],
        }
```

Add the write endpoint:

```python
    @router.post("/terms")
    async def set_terms(
        body: TermsBody,
        _user_id: int = Depends(superadmin_only),
    ) -> dict:
        if body.profile is not None and body.profile not in PROFILES:
            raise HTTPException(status_code=400, detail=f"Unknown profile: {body.profile}")
        if body.overrides is not None:
            unknown = sorted(set(body.overrides) - set(TERM_KEYS))
            if unknown:
                # The noun list is closed on purpose. Accepting arbitrary keys
                # is exactly how a capped dictionary stops being capped.
                raise HTTPException(
                    status_code=400, detail=f"Unknown term keys: {', '.join(unknown)}"
                )
        await store.set_terms(body.profile, body.overrides)
        return {"profile": body.profile, "status": "ok"}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend/apps/backend
GOOGLE_API_KEY=test-dummy uv run pytest src/chatbot/features/tenant_config/ -v
```

Expected: PASS.

- [ ] **Step 5: Run the whole backend suite**

```bash
cd backend/apps/backend
GOOGLE_API_KEY=test-dummy uv run pytest src/chatbot -q
```

Expected: PASS, no failures.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/tenant_config/
git commit -m "feat(tenant-config): serve per-tenant terms on the switchboard response"
```

---

### Task 4: Document the setting and default new tenants to generic

**Files:**
- Modify: `deploy/tenants/example.env`
- Modify: `deploy/scripts/add-tenant.sh`
- Test: `backend/apps/backend/src/chatbot/features/tenant_config/test_term_dictionary.py`

**Interfaces:**
- Consumes: `Settings.term_profile` (Task 1).
- Produces: `TERM_PROFILE` documented, and written as `generic` for every newly provisioned tenant.

- [ ] **Step 1: Write the failing test**

Append to `test_term_dictionary.py`:

```python
def test_add_tenant_script_writes_generic_for_new_tenants() -> None:
    """The product's default vocabulary is a vertical, which is only safe
    because provisioning always overrides it. If this line is ever dropped, a
    new bank tenant opens saying "Dealer"."""
    from pathlib import Path

    script = Path(__file__).parents[5] / "deploy" / "scripts" / "add-tenant.sh"
    assert "TERM_PROFILE=generic" in script.read_text()
```

Verify the `parents[5]` depth resolves to the repo root before relying on it:

```bash
cd backend/apps/backend
uv run python -c "from pathlib import Path; print(Path('src/chatbot/features/tenant_config/test_term_dictionary.py').resolve().parents[5])"
```

Adjust the index until it prints the repo root, then use that number.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/tenant_config/test_term_dictionary.py::test_add_tenant_script_writes_generic_for_new_tenants -v
```

Expected: FAIL — the string is absent.

- [ ] **Step 3: Implement**

In `deploy/tenants/example.env`, beside the other feature settings:

```bash
# --- Term dictionary ------------------------------------------------------
# Which vocabulary this tenant's UI renders.
#   generic     industry-neutral: Partner, Asset, Field Incident, Job No.
#               Correct for every tenant outside automotive, and what
#               add-tenant.sh writes for a new tenant.
#   automotive  Dealer, Vehicle, RSA Incident, WIP No.
#
# UNSET MEANS `automotive`, and that is deliberate rather than a product
# statement: every tenant deployed before this setting existed is rendering
# automotive wording today, and an unset value must keep it that way
# byte-identically. proton in particular has no value here and must not get
# one — it keeps its vocabulary by falling through this default.
#
# A platform superadmin's choice in the Custom Features page overrides this.
# TERM_PROFILE=generic
```

In `deploy/scripts/add-tenant.sh`, find where the script writes the new tenant's env file and add the line with a comment:

```bash
# New tenants are industry-neutral. The backend's TERM_PROFILE default is
# `automotive` for backwards compatibility with tenants that predate the term
# dictionary, so provisioning must say `generic` explicitly or a non-
# automotive customer opens their CRM reading "Dealer" and "Vehicle".
TERM_PROFILE=generic
```

Read the script first to match how it writes other variables — whether it appends to a heredoc, `sed`s a template, or copies `example.env`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend/apps/backend
uv run pytest src/chatbot/features/tenant_config/test_term_dictionary.py -v
```

Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add deploy/tenants/example.env deploy/scripts/add-tenant.sh \
        backend/apps/backend/src/chatbot/features/tenant_config/test_term_dictionary.py
git commit -m "feat(deploy): provision new tenants with the generic vocabulary"
```

---

### Task 5: Expose `t()` on the composable and a profile picker on the page

**Files:**
- Create: `deploy/chatwoot-fork/patches/0075-term-dictionary-composable.patch`

**Interfaces:**
- Consumes: `useCustomFeatures` (switchboard plan, Task 6); `CustomFeaturesPage.vue` (switchboard plan, Task 7); `GET`/`POST /admin/custom-features/terms` (Task 3).
- Produces: `t(key, form)` on `useCustomFeatures`; `setTermProfile(profile)` in `protonAdmin.js`; a profile selector on the switchboard page.

- [ ] **Step 1: Write the patch**

Both `useCustomFeatures.js` and `CustomFeaturesPage.vue` are files this repo's own patches created, so their context lines come from `0072` and `0073` — read those, do not guess.

```diff
diff --git a/app/javascript/dashboard/api/protonAdmin.js b/app/javascript/dashboard/api/protonAdmin.js
--- a/app/javascript/dashboard/api/protonAdmin.js
+++ b/app/javascript/dashboard/api/protonAdmin.js
@@ -80,3 +80,10 @@ export async function setCustomFeature(key, enabled) {
     body: { key, enabled },
   });
 }
+
+export async function setTermProfile(profile) {
+  return adminRequest('/admin/custom-features/terms', {
+    method: 'POST',
+    body: { profile },
+  });
+}
```

In `useCustomFeatures.js`, add a `terms` ref, populate it in both the success and failure branches, and export `t`:

```diff
 const behavior = ref({});
+const terms = ref({});
+const termProfile = ref('');
+const termProfiles = ref([]);
```

```diff
       behavior.value = data.behavior || {};
+      terms.value = data.terms || {};
+      termProfile.value = data.term_profile || '';
+      termProfiles.value = Array.isArray(data.term_profiles) ? data.term_profiles : [];
       loadFailed.value = false;
```

```diff
       behavior.value = {};
+      // Terms are NOT failed closed the way features are. A feature rendering
+      // when it should not is a licensing leak; a heading rendering its key
+      // name is just broken text. Keeping the last known vocabulary — or the
+      // key itself as a last resort — is strictly better than a blank label.
       loadFailed.value = true;
```

```diff
     refresh,
+    terms,
+    termProfile,
+    termProfiles,
+    // `form` is 'singular' (default), 'plural' or 'lower'. Falls back to the
+    // key so a missing term renders something greppable rather than empty.
+    t: (key, form = 'singular') => (terms.value[key] || {})[form] || key,
     hasFeature: key => (features.value || []).includes(key),
```

Then add the profile selector to `CustomFeaturesPage.vue`, inside the `v-else` superadmin branch, above the feature groups:

```diff
+      <div class="mb-6">
+        <h2 class="text-sm font-semibold text-n-slate-12 mb-2">Vocabulary</h2>
+        <p class="text-xs text-n-slate-11 mb-2">
+          Which words this tenant's CRM uses. Applies to headings and labels
+          only — stored data is unaffected.
+        </p>
+        <select
+          class="text-sm rounded-lg border border-n-container bg-n-solid-2 px-3 py-1.5"
+          :value="termProfile"
+          @change="changeProfile($event.target.value)"
+        >
+          <option v-for="p in termProfiles" :key="p" :value="p">{{ p }}</option>
+        </select>
+      </div>
```

with the handler in the `<script setup>` block:

```diff
+async function changeProfile(profile) {
+  error.value = '';
+  try {
+    await setTermProfile(profile);
+    await refresh();
+  } catch (e) {
+    error.value = e.message || 'Could not save';
+  }
+}
```

and `setTermProfile` added to that file's import from `dashboard/api/protonAdmin`, plus `termProfile`/`termProfiles` added to its `useCustomFeatures()` destructuring.

- [ ] **Step 2: Verify structurally**

```bash
cd deploy/chatwoot-fork/patches
rtk proxy grep -n 'setCustomFeature' 0072-custom-features-composable.patch
rtk proxy grep -n 'useCustomFeatures()' 0073-custom-features-page.patch
```

Expected: the context lines quoted in `0075` are character-identical to what `0072` and `0073` produce.

- [ ] **Step 3: Commit**

```bash
git add deploy/chatwoot-fork/patches/0075-term-dictionary-composable.patch
git commit -m "feat(chatwoot-fork): t() helper and vocabulary picker"
```

---

### Task 6: Render the nouns through `t()`

**Files:**
- Create: `deploy/chatwoot-fork/patches/0076-term-dictionary-call-sites.patch`

**Interfaces:**
- Consumes: `t()` (Task 5), the eleven keys (Task 1).
- Produces: every hardcoded domain noun in the fork's own components resolved at render time.

- [ ] **Step 1: Enumerate the call sites before editing**

```bash
cd deploy/chatwoot-fork/patches
rtk proxy grep -rn '^+.*>\s*\(Dealer\|Vehicle\|RSA\|WIP\|Chassis\)' *.patch | head -60
```

Work through them file by file. The components carrying the highest counts are `ProtonDealerEscalationSection.vue` (`0034`), the RSA incident log (`0035`), the Cases list (`0043`), Customer 360 (`0041`), escalation routing admin (`0039`), and the escalation ladder pages (`0070`, `0071`).

- [ ] **Step 2: Convert each site**

Import the composable and destructure `t` in each component's `<script setup>`:

```diff
+import { useCustomFeatures } from 'dashboard/composables/useCustomFeatures';
+const { t } = useCustomFeatures();
```

Convert headings and labels:

```diff
-      <h3 class="text-sm font-semibold text-n-slate-12">
-        Dealer Escalation Turnaround
-      </h3>
+      <h3 class="text-sm font-semibold text-n-slate-12">
+        {{ t('partner') }} Escalation Turnaround
+      </h3>
```

```diff
-          <th>Vehicle Model</th>
+          <th>{{ t('asset_model') }}</th>
```

Mid-sentence text uses the `lower` form:

```diff
-      No dealers configured yet.
+      No {{ t('partner', 'plural').toLowerCase() }} configured yet.
```

Prefer `t('partner', 'lower')` over `.toLowerCase()` wherever a lowercase singular is what is wanted — the stored `lower` is why acronyms survive.

**Do not touch** anything matching `dealer_`, `vehicle_`, `rsa_` as an identifier, a `v-model` target, an API field name, a query parameter, or a Chatwoot label string. Those are data. If a string is both a label and a value sent to the backend, convert the label and leave the value.

- [ ] **Step 3: Verify no data key was converted**

```bash
cd deploy/chatwoot-fork/patches
rtk proxy grep -nE "t\('(partner|asset|asset_model|asset_id|asset_serial|field_incident|job_no|partner_system)'\)" 0076-term-dictionary-call-sites.patch | wc -l
rtk proxy grep -nE "^\+.*(dealer_[a-z]|vehicle_[a-z])" 0076-term-dictionary-call-sites.patch
```

Expected: the first count is substantial; the second returns **nothing added or changed** on lines that rename an identifier. Any hit in the second command must be an untouched context line — check each one.

- [ ] **Step 4: Commit**

```bash
git add deploy/chatwoot-fork/patches/0076-term-dictionary-call-sites.patch
git commit -m "feat(chatwoot-fork): render domain nouns through the term dictionary"
```

---

## Rollout (after all tasks pass)

1. **Backend image** to every tenant. Nothing renders differently — there are no `t()` call sites in any deployed SPA yet.
2. **`default`**: add `TERM_PROFILE=generic` to `default.env`, restart its backend, then build and pull the Chatwoot image. Walk every screen and confirm neutral wording. This is the acceptance gate for the whole plan.
3. **aeon360**: add `TERM_PROFILE=generic`, restart backend, pull the image.
4. **proton: nothing.** No env var, no store row, no image pull. It falls through to `automotive` and keeps Dealer, Vehicle, RSA and WIP whenever it eventually takes a later image.

Steps 2 and 3 need a production env edit, which must be handed to Yuda — the classifier blocks it from an agent session.
