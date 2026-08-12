# Case Taxonomy Store Seeding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seed the Firestore Case Taxonomy store from the RFP Appendix A config on backend startup, so the taxonomy admin page renders the full 347-node tree instead of "No active taxonomy nodes yet".

**Architecture:** Three changes, all in `backend/`. `features/taxonomy/seed.py` gains a neutral level-1 root for divisions, a label→key map that fixes 100 silently-dropped detail values, warning logs for unresolvable parents, and a single bulk pre-read that makes re-seeding cost one Firestore read. `main.py` gains an `@app.on_event("startup")` hook that dispatches the seed as a background task when `TAXONOMY_ADMIN_ENABLED` is true. `deploy/tenants/example.env` documents the new boot behaviour. No fork patch and no Chatwoot image rebuild — patch `0060` is already live.

**Tech Stack:** Python 3.12, FastAPI, `google-cloud-firestore`, `structlog`, pytest (`asyncio_mode=auto`), `uv`.

## Global Constraints

- All commands run from `backend/apps/backend`.
- The test suite **requires** `GEMINI_API_KEY` set to any value: `GEMINI_API_KEY=dummy uv run pytest -q`. Without it, five modules fail at *collection* and the suite never runs — this reads exactly like a code regression but is an environment gap.
- Seeding stays **non-destructive**: never overwrite an operator-edited label, never reactivate a retired node, only create missing keys.
- Seeding must **never raise** out to the caller for expected "nothing to do" cases. It is a background task; an escaping exception produces only an unretrieved-exception log.
- Module docstrings in this codebase explain the *why* and the concurrency/idempotency reasoning. Preserve that style when editing.
- Tenant scope is **proton only**. Do not touch `default` or `wahchan`.
- Never merge to `main`. All work lands on `dev-yuda`.
- Neutral root identifiers, used verbatim in three places: key `type_case_divisions`, label `Case divisions`.

---

### Task 1: Fix the detail parent resolution and add the neutral divisions root

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/taxonomy/seed.py`
- Test: `backend/apps/backend/src/chatbot/features/taxonomy/test_seed.py`

**Interfaces:**
- Consumes: `TaxonomyStore.list_nodes(active_only: bool) -> list[TaxonomyNode]`, `TaxonomyStore.create_node(node: TaxonomyNode) -> bool`, `TaxonomyStore.get_node(key: str) -> TaxonomyNode | None` (all already exist in `features/taxonomy/store.py`).
- Produces: `seed_taxonomy_from_env(store: TaxonomyStore, settings: Settings) -> int` — same signature as today, returns the count of newly created nodes. Module constants `_DIVISIONS_ROOT_KEY = "type_case_divisions"` and `_DIVISIONS_ROOT_LABEL = "Case divisions"`.

**Background the implementer needs:**

`CASE_TAXONOMY_JSON` is keyed by slug with a display label inside:

```json
{"aftersales": {"label": "After Sales", "subcategories": ["Warranty", "Airbag"]}}
```

`CASE_DETAIL_OPTIONS_JSON` values are prefixed with the **display label**, not the key:

```
"After Sales: Warranty: Rejected"
```

Today `seed.py` resolves the parent by re-slugifying that first segment, producing `cat_after_sales_warranty`, but the division node was keyed `div_aftersales` and its children `cat_aftersales_warranty`. Measured against the shipped config defaults: **146 details matched, 100 orphaned across 18 missing parent keys, every one of them `after_sales_*`.** The orphans are dropped by an `if parent_node is not None:` with no `else`, so nothing is logged.

- [ ] **Step 1: Write the failing tests**

Add to `backend/apps/backend/src/chatbot/features/taxonomy/test_seed.py`:

```python
async def test_every_detail_option_finds_its_parent_including_after_sales(
    store: TaxonomyStore, settings
) -> None:
    """The division key is `aftersales`; the detail prefix is `After Sales`.

    Re-slugifying the prefix gives `after_sales`, which matches no division key,
    so 100 of 246 details used to be dropped with no log line. Resolution goes
    through a label -> key map now.
    """
    await seed_taxonomy_from_env(store, settings)

    detail_count = len(json.loads(settings.case_detail_options_json)["options"])
    nodes = await store.list_nodes(active_only=True)
    seeded_details = [n for n in nodes if n.level == 4]
    assert len(seeded_details) == detail_count

    after_sales_details = [
        n for n in seeded_details if n.parent is not None and n.parent.startswith("cat_aftersales_")
    ]
    assert after_sales_details, "no After Sales details were seeded"


async def test_divisions_hang_off_the_neutral_root_not_a_case_type(
    store: TaxonomyStore, settings
) -> None:
    """Appendix A's Case Category is orthogonal to Division.

    Parenting divisions to whichever case type sorts first made the page assert
    that every division belongs to Inquiry. The neutral root makes no such claim.
    """
    await seed_taxonomy_from_env(store, settings)

    nodes = await store.list_nodes(active_only=True)
    divisions = [n for n in nodes if n.level == 2]
    assert divisions
    assert {n.parent for n in divisions} == {"type_case_divisions"}

    root = await store.get_node("type_case_divisions")
    assert root is not None
    assert root.level == 1
    assert root.label == "Case divisions"
    assert root.parent is None


async def test_the_three_case_types_are_seeded_as_childless_roots(
    store: TaxonomyStore, settings
) -> None:
    await seed_taxonomy_from_env(store, settings)

    tree = await store.tree()
    by_label = {root["label"]: root for root in tree}
    assert set(by_label) == {
        "Inquiry",
        "Complaint",
        "Compliment & Feedback",
        "Case divisions",
    }
    for label in ("Inquiry", "Complaint", "Compliment & Feedback"):
        assert by_label[label]["children"] == []

    div_labels = {child["label"] for child in by_label["Case divisions"]["children"]}
    assert div_labels == {
        "Sales",
        "Product",
        "Network",
        "Charging",
        "Apps",
        "After Sales",
        "Others",
        "Marketing",
    }


async def test_a_detail_whose_parent_does_not_exist_is_skipped_not_raised(
    store: TaxonomyStore, settings
) -> None:
    updated = settings.model_copy(
        update={
            "case_detail_options_json": json.dumps(
                {"options": ["Nonexistent Division: Nonexistent Category: Some Detail"]}
            )
        }
    )

    created = await seed_taxonomy_from_env(store, updated)

    assert created > 0  # types, root and divisions still seeded
    nodes = await store.list_nodes(active_only=True)
    assert [n for n in nodes if n.level == 4] == []
```

Two existing tests in this file encode the old shape and must be updated in the same step, because the neutral root and the reparenting make their assertions false:

Replace `test_all_three_case_types_are_seeded` — it asserts the level-1 labels are exactly the three case types, and there are four level-1 nodes now:

```python
async def test_all_three_case_types_are_seeded(store: TaxonomyStore, settings) -> None:
    await seed_taxonomy_from_env(store, settings)

    nodes = await store.list_nodes(active_only=True)
    l1_labels = {n.label for n in nodes if n.level == 1}
    assert {"Inquiry", "Complaint", "Compliment & Feedback"} <= l1_labels
```

Delete `test_the_seeded_tree_matches_what_the_env_json_produces_today` entirely. It asserts `tree[0]["label"] == "Inquiry"` with divisions as its children — precisely the false relationship this task removes. `test_the_three_case_types_are_seeded_as_childless_roots` above replaces it and covers strictly more.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/apps/backend
GEMINI_API_KEY=dummy uv run pytest src/chatbot/features/taxonomy/test_seed.py -q
```

Expected: the four new tests FAIL. `test_every_detail_option_finds_its_parent_including_after_sales` fails with `146 != 246`; the two neutral-root tests fail because `type_case_divisions` does not exist; `test_a_detail_whose_parent_does_not_exist_is_skipped_not_raised` passes already (the silent skip is the current behaviour) — that is fine, it is a regression guard for the logging change.

- [ ] **Step 3: Add the module constants and the label map**

In `seed.py`, add below `_log = structlog.get_logger(__name__)`:

```python
# Appendix A's Case Category (Complaint / Inquiry / Compliment & Feedback) is
# orthogonal to its Division -- any division can carry any type, and the fork's
# cascade chain (patch 0050) is case_category -> case_subcategory -> case_detail
# with case_type deliberately absent. The store cannot express that: every node
# above level 1 requires a parent. Divisions therefore hang off a neutral root
# that claims nothing, rather than off whichever case type happens to sort first.
_DIVISIONS_ROOT_KEY = "type_case_divisions"
_DIVISIONS_ROOT_LABEL = "Case divisions"
```

- [ ] **Step 4: Rewrite the body of `seed_taxonomy_from_env`**

Replace the whole function body (keep the signature and docstring, extending the docstring as shown):

```python
async def seed_taxonomy_from_env(store: TaxonomyStore, settings: Settings) -> int:
    """Non-destructively seed taxonomy from settings env JSON.

    Returns the number of newly created nodes.

    Existing keys are read once, up front, rather than probed per node: the
    store builds a fresh `firestore.Client` per operation and `create_node`
    issues an existence check plus a parent check before writing, so probing
    made every boot after the first cost ~700 round trips to create nothing.
    One read now, and a populated store writes nothing at all.
    """
    from chatbot.features.taxonomy.store import TaxonomyNode

    existing_keys = {node.key for node in await store.list_nodes(active_only=False)}
    created_count = 0

    async def _create(node: TaxonomyNode) -> None:
        """Create `node` unless its key is already known. Never raises.

        A retired parent makes `create_node` raise, and this runs as a
        background task where an escaping exception would abandon the rest of
        the seed and log nothing useful. Skip and record instead.
        """
        nonlocal created_count
        if node.key in existing_keys:
            return
        try:
            if await store.create_node(node):
                existing_keys.add(node.key)
                created_count += 1
        except ValueError as exc:
            _log.warning("taxonomy_seed_node_skipped", key=node.key, error=str(exc))

    # Level 1: Case Types
    case_types_data = parse_json_safely(settings.case_type_options_json, {"options": []})
    type_options = case_types_data.get("options", []) if isinstance(case_types_data, dict) else []

    for idx, type_label in enumerate(type_options):
        await _create(
            TaxonomyNode(
                level=1,
                key=f"type_{_slugify(type_label)}",
                label=type_label,
                parent=None,
                active=True,
                sort_order=idx * 10,
            )
        )

    # Level 2: Divisions & Level 3: Subcategories
    taxonomy_data = parse_json_safely(settings.case_taxonomy_json, {})
    label_to_div_slug: dict[str, str] = {}

    if isinstance(taxonomy_data, dict) and taxonomy_data:
        await _create(
            TaxonomyNode(
                level=1,
                key=_DIVISIONS_ROOT_KEY,
                label=_DIVISIONS_ROOT_LABEL,
                parent=None,
                active=True,
                sort_order=len(type_options) * 10,
            )
        )

        for div_idx, (div_slug, div_info) in enumerate(taxonomy_data.items()):
            if not isinstance(div_info, dict):
                continue
            div_label = div_info.get("label", div_slug.title())
            # A detail option is prefixed with the division LABEL; the division
            # node is keyed from the JSON KEY. "After Sales" -> "aftersales" is
            # only knowable from here.
            label_to_div_slug[_slugify(str(div_label))] = div_slug

            await _create(
                TaxonomyNode(
                    level=2,
                    key=f"div_{_slugify(div_slug)}",
                    label=div_label,
                    parent=_DIVISIONS_ROOT_KEY,
                    active=True,
                    sort_order=div_idx * 10,
                )
            )

            subcats = div_info.get("subcategories", [])
            if isinstance(subcats, list):
                for sub_idx, sub_label in enumerate(subcats):
                    await _create(
                        TaxonomyNode(
                            level=3,
                            key=f"cat_{_slugify(div_slug)}_{_slugify(sub_label)}",
                            label=sub_label,
                            parent=f"div_{_slugify(div_slug)}",
                            active=True,
                            sort_order=sub_idx * 10,
                        )
                    )

    # Level 4: Case Detail Options
    detail_data = parse_json_safely(settings.case_detail_options_json, {"options": []})
    detail_options = detail_data.get("options", []) if isinstance(detail_data, dict) else []
    unresolved: dict[str, int] = {}

    for det_idx, det_string in enumerate(detail_options):
        # Format "<Division>: <Subcategory>: <Detail>"
        parts = [p.strip() for p in det_string.split(":")]
        if len(parts) < 3:
            continue

        prefix_slug = _slugify(parts[0])
        div_slug = label_to_div_slug.get(prefix_slug, prefix_slug)
        sub_slug = _slugify(parts[1])
        det_label = ": ".join(parts[2:])
        parent_key = f"cat_{div_slug}_{sub_slug}"

        if parent_key not in existing_keys and await store.get_node(parent_key) is None:
            unresolved[parent_key] = unresolved.get(parent_key, 0) + 1
            continue

        await _create(
            TaxonomyNode(
                level=4,
                key=f"det_{div_slug}_{sub_slug}_{_slugify(det_label)}",
                label=det_label,
                parent=parent_key,
                active=True,
                sort_order=det_idx * 10,
            )
        )

    if unresolved:
        # Silence here is what hid the After Sales mismatch: 100 of 246 details
        # vanished with no log line at all.
        _log.warning(
            "taxonomy_seed_details_unresolved",
            dropped=sum(unresolved.values()),
            parents=sorted(unresolved),
        )

    _log.info("taxonomy_seeded", newly_created=created_count)
    return created_count
```

- [ ] **Step 5: Run the taxonomy tests**

```bash
cd backend/apps/backend
GEMINI_API_KEY=dummy uv run pytest src/chatbot/features/taxonomy/ -q
```

Expected: PASS, including `test_re_seeding_never_overwrites_an_operator_edited_label`, `test_re_seeding_never_reactivates_a_retired_node` and `test_re_seeding_adds_a_node_that_appeared_in_the_env_json`, which are unchanged and must stay green.

- [ ] **Step 6: Verify the node count against the real config**

```bash
cd backend/apps/backend
GEMINI_API_KEY=dummy uv run python -c "
import asyncio, sys
sys.path.insert(0, 'src')
from unittest.mock import patch
from chatbot.platform.config import get_settings
docs = {}
class FakeSnap:
    def __init__(s, d): s._d = d
    @property
    def exists(s): return s._d is not None
    def to_dict(s): return dict(s._d) if s._d is not None else None
class FakeDoc:
    def __init__(s, i): s._i = i
    def get(s): return FakeSnap(docs.get(s._i))
    def set(s, d): docs[s._i] = dict(d)
class FakeCol:
    def document(s, i): return FakeDoc(i)
    def get(s): return [FakeSnap(d) for d in docs.values()]
class FakeClient:
    def __init__(s, *a, **k): pass
    def collection(s, n): return FakeCol()
with patch('chatbot.features.taxonomy.store.firestore.Client', FakeClient):
    from chatbot.features.taxonomy.seed import seed_taxonomy_from_env
    from chatbot.features.taxonomy.store import TaxonomyStore
    st = TaxonomyStore(get_settings())
    n = asyncio.run(seed_taxonomy_from_env(st, get_settings()))
    print('created', n)
    from collections import Counter
    print(Counter(d['level'] for d in docs.values()))
"
```

Expected output: `created 346` and `Counter({4: 245, 3: 89, 2: 8, 1: 4})`.

245, not 246: `"Charging: Public Charging: others"` and `"Charging: Public Charging: Others"` in the shipped config differ only by letter case and slugify to the same key. That is pre-existing transcription data, unrelated to this task, and collapsing them is correct — the ruling was to leave `config.py` verbatim.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/taxonomy/seed.py \
        backend/apps/backend/src/chatbot/features/taxonomy/test_seed.py
git commit -m "fix(taxonomy): resolve detail parents by division label, add neutral root

The division key is 'aftersales' but detail options are prefixed with the
'After Sales' display label, so re-slugifying the prefix orphaned 100 of 246
details -- silently, because the skip had no else branch. Resolve through a
label->key map and warn on anything still unresolved.

Divisions now hang off a neutral 'Case divisions' root instead of whichever
case type sorted first, which had the page asserting every division belongs
to Inquiry. Existing keys are read once up front so a re-seed costs one read."
```

---

### Task 2: Seed on backend startup

**Files:**
- Modify: `backend/apps/backend/src/chatbot/main.py` (immediately after the taxonomy router mount at line 1167)
- Test: `backend/apps/backend/src/chatbot/test_p10_wiring.py`

**Interfaces:**
- Consumes: `seed_taxonomy_from_env(store, settings) -> int` and `build_taxonomy_store(settings) -> TaxonomyStore` from Task 1.
- Produces: `app.state.taxonomy_seed_task` — an `asyncio.Task` when `taxonomy_admin_enabled` is true, absent otherwise. Tests await it to observe the seed.

**Background the implementer needs:**

`main.py` is an app factory: `bootstrap_application()` at line 346 builds and returns `app`, and line 1367 calls it. `_log` already exists at line 114. `asyncio` is **not** imported at module level — import it inside the hook, matching how the surrounding startup hooks import their dependencies locally.

The seed is **dispatched, not awaited**. 347 sequential Firestore writes take 15–30 seconds; awaiting them inside a startup hook holds the container below its health check on every cold start of a tenant whose store is empty.

Keep the task on `app.state`. A bare `asyncio.create_task(...)` whose result nobody holds can be garbage-collected mid-flight, and the wiring test needs a handle to await.

`TestClient(app)` used bare does **not** run startup events — that is why the existing tests in this file never triggered one. The new test must use `with TestClient(app):`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/apps/backend/src/chatbot/test_p10_wiring.py`:

```python
def test_startup_seeds_the_taxonomy_store_when_the_admin_flag_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seeder existed and had no caller but its own tests.

    The admin page rendered "No active taxonomy nodes yet" on a tenant with the
    flag on, the router mounted and the Appendix A data sitting in config --
    because nothing ever wrote it to Firestore.
    """
    import time

    app = _boot(monkeypatch, TAXONOMY_ADMIN_ENABLED="true")

    with TestClient(app) as client:
        # The seed runs on the TestClient's own event loop, in its own thread.
        # Poll rather than await: this test function is sync and cannot drive
        # that loop. Against `_FakeFirestore` the seed is in-memory and finishes
        # almost immediately.
        for _ in range(200):
            if app.state.taxonomy_seed_task.done():
                break
            time.sleep(0.01)
        assert app.state.taxonomy_seed_task.done(), "seed task did not finish"

        res = client.get("/admin/taxonomy/tree")
        assert res.status_code == 200
        roots = res.json()["tree"]
        assert {root["label"] for root in roots} == {
            "Inquiry",
            "Complaint",
            "Compliment & Feedback",
            "Case divisions",
        }


def test_startup_does_not_seed_when_the_admin_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _boot(monkeypatch, TAXONOMY_ADMIN_ENABLED="false")

    with TestClient(app):
        assert not hasattr(app.state, "taxonomy_seed_task")
    assert _FakeFirestore.documents == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend/apps/backend
GEMINI_API_KEY=dummy uv run pytest src/chatbot/test_p10_wiring.py -q
```

Expected: `test_startup_seeds_the_taxonomy_store_when_the_admin_flag_is_on` FAILS with `AttributeError: 'State' object has no attribute 'taxonomy_seed_task'`. The flag-off test passes already.

- [ ] **Step 3: Add the startup hook**

In `main.py`, immediately after `app.include_router(build_taxonomy_admin_router(settings))` (line 1167):

```python
    @app.on_event("startup")
    async def _seed_taxonomy_store() -> None:
        """Seed the taxonomy store from the three CASE_*_JSON settings.

        Dispatched, never awaited. A first boot against an empty store is ~347
        sequential Firestore writes -- 15-30s -- and awaiting that here holds the
        container below its health check. A populated store costs one read, so
        the steady state is nearly free either way.

        `example.env` already documents these vars as "the seed only" once a
        tenant's store is populated. Until this hook existed no tenant's store
        ever was, and the taxonomy admin page rendered empty on a tenant whose
        config held the full Appendix A taxonomy.
        """
        if not settings.taxonomy_admin_enabled:
            return

        import asyncio

        from chatbot.features.taxonomy.seed import seed_taxonomy_from_env
        from chatbot.features.taxonomy.store import build_taxonomy_store

        async def _run() -> None:
            try:
                created = await seed_taxonomy_from_env(build_taxonomy_store(settings), settings)
                _log.info("taxonomy_startup_seed_complete", newly_created=created)
            except Exception as exc:  # noqa: BLE001 -- fail-open, never block boot
                _log.warning("taxonomy_startup_seed_failed", error=str(exc))

        # Held on app.state so the task is not garbage-collected mid-flight.
        app.state.taxonomy_seed_task = asyncio.create_task(_run())
```

- [ ] **Step 4: Run the wiring tests**

```bash
cd backend/apps/backend
GEMINI_API_KEY=dummy uv run pytest src/chatbot/test_p10_wiring.py -q
```

Expected: PASS, all six tests.

- [ ] **Step 5: Run the full backend suite**

```bash
cd backend/apps/backend
GEMINI_API_KEY=dummy uv run pytest -q
```

Expected: PASS. The last recorded green state is 2998 passed / 2 skipped; this plan adds 6 tests and deletes 1, so expect 3003 passed / 2 skipped. Any *other* failure is a regression from these two tasks — do not proceed past it.

- [ ] **Step 6: Lint and typecheck**

```bash
cd backend/apps/backend
.venv/bin/ruff format .
.venv/bin/ruff check . --fix
.venv/bin/mypy src/chatbot/features/taxonomy/ --strict
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/backend/src/chatbot/main.py \
        backend/apps/backend/src/chatbot/test_p10_wiring.py
git commit -m "feat(taxonomy): seed the store on startup when the admin flag is on

seed_taxonomy_from_env had no caller but its own tests, so the taxonomy
admin page rendered empty on a tenant with TAXONOMY_ADMIN_ENABLED=true,
the router mounted and the full Appendix A taxonomy sitting in config.

Dispatched rather than awaited: a first boot is ~347 sequential Firestore
writes and awaiting them holds the container below its health check."
```

---

### Task 3: Document the boot behaviour in the tenant env reference

**Files:**
- Modify: `deploy/tenants/example.env:834` (the `TAXONOMY_ADMIN_ENABLED` block)

**Interfaces:**
- Consumes: nothing. Produces: nothing. Documentation only.

**Background the implementer needs:**

The block already contains this paragraph:

```
# ONCE A TENANT'S STORE HAS BEEN SEEDED FROM THOSE THREE VARS (one-time,
# non-destructive), THEY BECOME THE SEED ONLY -- editing them afterwards has
# no further effect on that tenant. Edit the taxonomy through the admin
# endpoints/page instead.
```

It described an intention. No code performed that seeding. Task 2 makes it true, and the comment should say when it happens.

- [ ] **Step 1: Insert the boot note**

Immediately **before** that `ONCE A TENANT'S STORE...` paragraph in `deploy/tenants/example.env`, add:

```
# Turning this on seeds the store from CASE_TYPE_OPTIONS_JSON,
# CASE_TAXONOMY_JSON and CASE_DETAIL_OPTIONS_JSON on the next backend boot --
# with the shipped RFP 2026_028 Appendix A defaults that is 346 nodes (3 case
# types + a neutral "Case divisions" root + 8 divisions + 89 categories + 245
# details -- 246 detail strings, two of which differ only by letter case and
# collapse to one node). The seed runs in the background so it never delays the container's
# health check, and it re-runs on every boot: after the first it reads once and
# writes nothing. Watch for `taxonomy_startup_seed_complete` in the backend log.
#
```

- [ ] **Step 2: Verify the block reads correctly end to end**

```bash
sed -n '/^# true -> mounts the Firestore-backed taxonomy admin/,/^TAXONOMY_ADMIN_ENABLED=/p' deploy/tenants/example.env
```

Expected: the new paragraph sits between the "Off = those endpoints 404" paragraph and the `ONCE A TENANT'S STORE` paragraph, and `TAXONOMY_ADMIN_ENABLED=false` still closes the block.

- [ ] **Step 3: Commit**

```bash
git add deploy/tenants/example.env
git commit -m "docs(env): TAXONOMY_ADMIN_ENABLED now seeds the store on boot"
```

---

### Task 4: Deploy to the proton tenant and turn on the coverage report

**Files:** none in the repo. This task operates on the VM.

**Interfaces:**
- Consumes: everything from Tasks 1–3, committed on `dev-yuda`.
- Produces: a proton backend serving a seeded taxonomy with `CATEGORY_DEPARTMENT_MAPPING_ENABLED=true`.

**Background the implementer needs:**

- VM: `gcloud compute ssh crm-ticketing --zone=asia-southeast2-a`. **Do not** pass `--tunnel-through-iap` — this account is not authorized for it and it fails with `4033: not authorized`.
- `docker compose` works **unprivileged**. Writes under `/opt/platform/deploy` **do** need `sudo` (root-owned dir, passwordless).
- `/opt/platform` is synced source, **not** a git repo.
- **Never copy a single file** into `/opt/platform` — a partial copy imports that file's entire future import graph and has crash-looped this backend before. Sync the whole tree.
- No Chatwoot image rebuild. Patch `0060` is already in the live `proton-chatwoot:v4.15.1-custom-rc1` (`.git_sha` `3006906`, all 59 patches). Only the `backend` service is rebuilt; `agent` reads none of these settings.
- `TAXONOMY_ADMIN_ENABLED` is **already true** on this tenant — that is why the page renders at all today.

- [ ] **Step 1: Back up the VM state**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command "
  sudo tar czf /tmp/platform-src-backup-20260812.tgz -C /opt/platform backend agent &&
  sudo cp /opt/platform/deploy/tenants/proton.env /opt/platform/deploy/tenants/proton.env.bak-20260812 &&
  ls -la /tmp/platform-src-backup-20260812.tgz /opt/platform/deploy/tenants/proton.env.bak-20260812
"
```

Expected: both files listed with non-zero size.

- [ ] **Step 2: Sync the full source tree**

From the repo root:

```bash
COPYFILE_DISABLE=1 tar czf - --exclude=.venv --exclude=__pycache__ \
  --exclude=.pytest_cache --exclude=.ruff_cache backend agent \
  | gcloud compute ssh crm-ticketing --zone asia-southeast2-a \
    --command "sudo tar xzf - -C /opt/platform && sudo find /opt/platform/backend /opt/platform/agent -name '._*' -delete"
```

`LIBARCHIVE.xattr.com.apple.provenance` warnings from GNU tar are harmless macOS xattrs.

- [ ] **Step 3: Verify the sync by file count, not by eye**

```bash
find backend/apps/backend/src/chatbot -name '*.py' | wc -l
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a \
  --command "find /opt/platform/backend/apps/backend/src/chatbot -name '*.py' | wc -l"
```

Expected: identical counts. If the VM count is lower, the sync did not land — stop and re-run Step 2.

- [ ] **Step 4: Set the coverage flag**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command "
  grep -n 'CATEGORY_DEPARTMENT_MAPPING_ENABLED\|TAXONOMY_ADMIN_ENABLED' /opt/platform/deploy/tenants/proton.env
"
```

Read what is actually there first. If `CATEGORY_DEPARTMENT_MAPPING_ENABLED` is present, flip it in place; if it is absent, append it:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command "
  sudo sed -i 's/^CATEGORY_DEPARTMENT_MAPPING_ENABLED=.*/CATEGORY_DEPARTMENT_MAPPING_ENABLED=true/' /opt/platform/deploy/tenants/proton.env &&
  grep -c '^CATEGORY_DEPARTMENT_MAPPING_ENABLED=true' /opt/platform/deploy/tenants/proton.env
"
```

Expected: `1`. If it prints `0`, the var was absent — append it with `echo 'CATEGORY_DEPARTMENT_MAPPING_ENABLED=true' | sudo tee -a` and re-check.

Confirm `TAXONOMY_ADMIN_ENABLED=true` is also present. If it is not, the page the user is looking at could not be rendering — stop and report that discrepancy rather than guessing.

- [ ] **Step 5: Rebuild and recreate the backend**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command "
  cd /opt/platform/deploy &&
  docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --build backend
"
```

- [ ] **Step 6: Confirm the seed ran**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a \
  --command "docker logs proton-backend 2>&1 | grep -E 'taxonomy_seeded|taxonomy_startup_seed|taxonomy_seed_details_unresolved' | tail -5"
```

Expected: `taxonomy_seeded newly_created=346` and `taxonomy_startup_seed_complete`. **`taxonomy_seed_details_unresolved` must not appear** — if it does, the label→key map missed a division and Task 1 is not finished; report the `parents` list rather than continuing.

- [ ] **Step 7: Verify both endpoints from inside the container**

Every weaker check has passed before while a feature was still dark, so read what the API actually serves:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command "
docker exec proton-backend python -c \"
import json, urllib.request
def get(p):
    return json.load(urllib.request.urlopen('http://127.0.0.1:8080' + p))
tree = get('/admin/taxonomy/tree')['tree']
print('roots:', [r['label'] for r in tree])
def count(nodes):
    return sum(1 + count(n['children']) for n in nodes)
print('nodes:', count(tree))
cov = get('/admin/taxonomy/coverage')
print('unmapped:', len(cov['unmapped_categories']))
print('unreferenced:', cov['unreferenced_departments'])
\"
"
```

Expected: four roots including `Case divisions`, `nodes: 346`, `unmapped: 97`, and a list of `dept_*` slugs. A 404 from `/admin/taxonomy/coverage` means the flag did not reach the container — re-check Step 4 and that the recreate in Step 5 actually replaced the container.

Note the image has `wget`, not `curl`, and `localhost` resolves to IPv6 while the server binds IPv4 — hence `127.0.0.1` and Python's `urllib` above.

- [ ] **Step 8: Confirm the seed is idempotent on the live tenant**

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --command "
  cd /opt/platform/deploy &&
  docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env restart backend &&
  sleep 30 &&
  docker logs proton-backend 2>&1 | grep taxonomy_seeded | tail -1
"
```

Expected: `newly_created=0`. Anything else means the pre-read is not matching keys it just wrote, and the store is accumulating duplicates.

- [ ] **Step 9: Open the page and look at it**

Navigate to the proton CRM → the Case Taxonomy admin page. Confirm the table lists the tree with `Case divisions` as a root, and that the coverage section now renders the two-column report instead of "The coverage report is switched off on this tenant".

Rails takes 60–90s to go `health: starting` → `healthy` after a recreate; the backend is quicker but do not read the page immediately and conclude a step failed.

- [ ] **Step 10: Push the branch**

```bash
git push origin dev-yuda
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Defect 1 — label→key map for details | Task 1, Steps 3–4 |
| Defect 1 — warn on unresolvable parents | Task 1, Step 4 (`taxonomy_seed_details_unresolved`) |
| Defect 2 — neutral root, divisions reparented | Task 1, Steps 3–4 |
| Bulk pre-read of existing keys | Task 1, Step 4 (`existing_keys`) |
| `main.py` startup hook, dispatched not awaited | Task 2, Step 3 |
| `example.env` documentation | Task 3 |
| Tests: divisions parent to neutral root | Task 1, Step 1 |
| Tests: no detail dropped, incl. After Sales | Task 1, Step 1 |
| Tests: unresolvable detail skipped, not raised | Task 1, Step 1 |
| Tests: re-seed creates 0, no writes | Task 1, Step 5 (existing tests, kept green) |
| Tests: operator edit and retired node survive | Task 1, Step 5 (existing tests, kept green) |
| Tests: startup wiring on/off | Task 2, Step 1 |
| Deploy: backup, sync, rebuild, flag, verify | Task 4, Steps 1–9 |
| Coverage report reads 97 unmapped | Task 4, Step 7 |

No spec requirement is unimplemented. Out-of-scope items in the spec (escalation routing wiring, `retired_department_categories`, BigQuery `case_detail` column, other tenants) intentionally have no task.

**Type consistency:** `seed_taxonomy_from_env(store, settings) -> int` keeps its signature across Tasks 1, 2 and their tests. `_DIVISIONS_ROOT_KEY`/`type_case_divisions` and `_DIVISIONS_ROOT_LABEL`/`Case divisions` are used identically in `seed.py`, `test_seed.py`, `test_p10_wiring.py` and `example.env`. `app.state.taxonomy_seed_task` is named the same in Task 2's implementation and both of its tests.
