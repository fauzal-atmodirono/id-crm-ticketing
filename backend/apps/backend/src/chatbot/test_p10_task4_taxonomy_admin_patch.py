"""P10 task 4 -- the fork page that makes `/admin/taxonomy` reachable.

Task 4's brief names three files. Two landed in `56c3755`
(`features/taxonomy/router.py`, and `taxonomy.manage` in `authz/seed.py`); the
third, its fork patch, did not -- so four endpoints were mounted, permission-gated
and green, and no operator could reach any of them. That is the ninth-and-a-half
instance of this run's recurring failure, and it is what
`deploy/chatwoot-fork/patches/0060-taxonomy-admin.patch` closes.

**What these tests do and do not prove.** They replay `0057` onto the transcribed
synthetic reconstruction its own tests use, then apply `0060` with a real
`git apply`; where node is available they EXECUTE the shipped JavaScript extracted
from the applied patch, and where `@vue/compiler-sfc` is available they compile the
shipped SFC's script and template. So the behavioural claims are about the shipped
text, not a Python re-implementation of it -- the discipline 0057 established.

There is deliberately no `..._onto_the_pinned_upstream_ref` test here, because
pytest has no access to the upstream Chatwoot image: what this file verifies is
internal consistency with the transcribed context of the patches below this one.
That is a limit of this suite only. On 2026-08-11 the whole 59-patch stack was
applied in order inside a throwaway `chatwoot/chatwoot:v4.15.1` container,
committing after each patch so every patch met the correctly accumulated tree --
`0060` applies cleanly to the real upstream-derived tree, and the run reported
`=== FAILING:` with nothing after it (`.superpowers/sdd/fork-patch-verification.md`).
An earlier version of this paragraph said no such verification was possible; it is
now, and where a real pre-image has been extracted a test should use it directly
(see `test_p7_task7_faq_composer_patch.py`). Still owed: nothing here has been seen
rendering in a browser, no Cloud Build has run -- so the vite build of the patched
source is unproven -- and no retire has been performed against a real Firestore.

**The reachability assertions are the point of the module.** A page is only
reachable if the route exists, the nav offers it, and every path it calls is a path
the router really exposes -- so the endpoint list is harvested from the shipped
JavaScript and compared against `router.py`'s own decorators rather than retyped.
A page calling `/admin/taxonomy/nodes` against a router serving `/node` would be
just as unreachable as no page at all, and a hardcoded expectation could not tell
the difference.

**And one backend behaviour the page exists to compensate for**, exercised here
against the real store rather than described: retiring a parent leaves its children
`active` while removing them from `tree()`, so they become live nodes that no
surface shows. The page must consume the `active_children` the router returns, and
the test that says so drives `retire_node` and `tree()` for real.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, ClassVar

import pytest

from chatbot.features.taxonomy.store import TaxonomyNode, TaxonomyStore
from chatbot.platform.config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[5]
PATCH_DIR = _REPO_ROOT / "deploy" / "chatwoot-fork" / "patches"
PATCH_0057 = PATCH_DIR / "0057-inbound-alerts.patch"
PATCH_0060 = PATCH_DIR / "0060-taxonomy-admin.patch"
ROUTER_PY = Path(__file__).resolve().parent / "features" / "taxonomy" / "router.py"
SEED_PY = Path(__file__).resolve().parent / "features" / "authz" / "seed.py"

for _p in (PATCH_0057, PATCH_0060, ROUTER_PY, SEED_PY):
    assert _p.is_file(), f"not found: {_p}"

PATCH_0060_TEXT = PATCH_0060.read_text(encoding="utf-8")
ROUTER_TEXT = ROUTER_PY.read_text(encoding="utf-8")

SIDEBAR_REL_PATH = "app/javascript/dashboard/components-next/sidebar/Sidebar.vue"
ROUTES_REL_PATH = "app/javascript/dashboard/routes/dashboard/dashboard.routes.js"
API_REL_PATH = "app/javascript/dashboard/api/protonTaxonomy.js"
PAGE_REL_PATH = "app/javascript/dashboard/views/ProtonTaxonomyPage.vue"

_NODE = shutil.which("node")

# ---------------------------------------------------------------------------
# The synthetic pre-image, replayed through 0057
# ---------------------------------------------------------------------------
# Transcribed verbatim from already-merged patches' own diffs, identical to the
# reconstruction in test_p9_task236_inbound_alerts_patch.py -- 0060 stacks on
# 0057 for both modified files, so 0057's post-image IS 0060's pre-image, and
# replaying 0057 rather than hand-writing that post-image is what makes the
# stacking claim a check rather than an assertion.
_SIDEBAR_KNOWN: dict[int, str] = {
    11: "import { vOnClickOutside } from '@vueuse/components';",
    12: "import { FEATURE_FLAGS } from 'dashboard/featureFlags';",
    13: "import { useWindowSize, useEventListener } from '@vueuse/core';",
    14: "import { useProtonConfig } from 'dashboard/composables/useProtonConfig';",
    15: "import { useProtonPermissions } from 'dashboard/composables/useProtonPermissions';",
    16: "",
    17: "import Button from 'dashboard/components-next/button/Button.vue';",
    18: "import SidebarGroup from './SidebarGroup.vue';",
    65: "",
    66: "const { hasFeature: protonHasFeature } = useProtonConfig();",
    67: "const protonAiAssistEnabled = computed(() => protonHasFeature('ai_assist'));",
    68: "const { hasPermission: protonHasPermission } = useProtonPermissions();",
    69: "",
    70: "const hasAdvancedAssignment = computed(() => {",
    71: "  return isFeatureEnabledonAccount.value(",
    685: "    // Cases list — reads Chatwoot conversation data directly (see",
    686: "    // dashboard/api/protonCases.js), so there's no backend require_permission",
    687: "    // route to match the way RSA/SLA/escalation have one. Gate on",
    688: "    // `customer360.view`, the nearest fitting existing permission: both are",
    689: "    // read-only CRM case-lookup surfaces for the same operator cohort.",
    690: "    ...(protonHasPermission('customer360.view')",
    691: "      ? [",
    692: "          {",
    693: "            name: 'ProtonCases',",
    694: "            icon: 'i-lucide-list-checks',",
    695: "            label: 'Cases',",
    696: "            to: accountScopedRoute('proton_cases'),",
    697: "          },",
    698: "        ]",
    699: "      : []),",
    700: "    {",
    701: "      name: 'Contacts',",
    702: "      label: t('SIDEBAR.CONTACTS'),",
}
_SIDEBAR_LENGTH = 720

_ROUTES_KNOWN: dict[int, str] = {
    85: "        {",
    86: "          path: 'proton/workforce',",
    87: "          name: 'proton_workforce',",
    88: "          component: () => import('../../views/ProtonWorkforceDashboardPage.vue'),",
    89: "          meta: { permissions: ['administrator'] },",
    90: "        },",
    91: "        {",
    92: "          path: 'proton/my-status',",
    93: "          name: 'proton_my_status',",
    94: "          component: () => import('../../views/ProtonMyStatusPage.vue'),",
    95: "          meta: { permissions: ['administrator', 'agent'] },",
    96: "        },",
    97: "        {",
    98: "          path: 'proton/cases',",
    99: "          name: 'proton_cases',",
    100: "          component: () => import('../../views/ProtonCasesPage.vue'),",
    101: "          meta: { permissions: ['administrator'] },",
    102: "        },",
    103: "        ...inboxRoutes,",
    104: "        ...conversation.routes,",
    105: "        ...settings.routes,",
}
_ROUTES_LENGTH = 115


def _build(known: dict[int, str], length: int) -> str:
    return (
        "\n".join(
            known.get(i, f"// filler-transcribed-context-unknown-line-{i}")
            for i in range(1, length + 1)
        )
        + "\n"
    )


def _run(tree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Every argument is a hardcoded literal (git plumbing / node) -- never
    # untrusted input -- so the subprocess call is safe despite S603.
    return subprocess.run(args, cwd=tree, capture_output=True, text=True, check=False)  # noqa: S603


@pytest.fixture(scope="module")
def applied(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """0057 then 0060, both by real `git apply`, keyed by repo-relative path."""
    tree = tmp_path_factory.mktemp("patch0060-tree")
    for rel, content in (
        (SIDEBAR_REL_PATH, _build(_SIDEBAR_KNOWN, _SIDEBAR_LENGTH)),
        (ROUTES_REL_PATH, _build(_ROUTES_KNOWN, _ROUTES_LENGTH)),
    ):
        path = tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert _run(tree, "git", "init", "-q").returncode == 0
    assert _run(tree, "git", "add", "-A").returncode == 0
    assert (
        _run(
            tree, "git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base"
        ).returncode
        == 0
    )

    first = _run(tree, "git", "apply", str(PATCH_0057))
    assert first.returncode == 0, (
        f"0057 no longer applies to its own reconstruction: {first.stderr}"
    )

    check = _run(tree, "git", "apply", "--check", str(PATCH_0060))
    assert check.returncode == 0, (
        "0060 did not apply on top of 0057's own post-image (internal "
        f"consistency only, not a real fork): {check.stderr}"
    )
    assert _run(tree, "git", "apply", str(PATCH_0060)).returncode == 0

    return {
        rel: (tree / rel).read_text(encoding="utf-8")
        for rel in (SIDEBAR_REL_PATH, ROUTES_REL_PATH, API_REL_PATH, PAGE_REL_PATH)
    }


# ---------------------------------------------------------------------------
# Executing the shipped JavaScript
# ---------------------------------------------------------------------------


def _eval_api_module(api_source: str, script: str, tmp_path: Path) -> Any:
    """Import the shipped api module in node with `adminRequest` stubbed.

    Skips (never silently passes) without node, so a green run always means the
    shipped JavaScript actually ran.
    """
    if _NODE is None:  # pragma: no cover - environment dependent
        pytest.skip("node is not available; the shipped JavaScript cannot be executed here")
    # The one bare specifier the module imports cannot resolve outside the
    # bundler, so it is replaced with a stub the harness controls. Nothing else
    # about the module is altered.
    stubbed, count = re.subn(
        r"^import \{ adminRequest \} from 'dashboard/api/protonAdmin';$",
        "const adminRequest = globalThis.__adminRequest;",
        api_source,
        count=1,
        flags=re.M,
    )
    assert count == 1, "the api module's adminRequest import changed shape"
    module = tmp_path / "protonTaxonomy.mjs"
    module.write_text(stubbed, encoding="utf-8")
    harness = tmp_path / "harness.mjs"
    harness.write_text(
        # `opts ?? null` because JSON.stringify drops an undefined value, and a
        # GET passes none -- so without it the harness could not distinguish
        # "no options" from "the key was not echoed at all".
        "globalThis.__adminRequest = async (path, opts) => ({ path, opts: opts ?? null });\n"
        f"const api = await import({json.dumps(module.as_uri())});\n" + script,
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603
        [_NODE, str(harness)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"node failed on the shipped module: {result.stderr}"
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_the_patch_hunks_apply_onto_a_synthetic_reconstruction_replayed_through_0057(
    applied: dict[str, str],
) -> None:
    """Named for what it checks. `git apply --check` runs inside the fixture, so
    reaching this body at all means both patches applied; the assertions here are
    that the four files came out."""
    assert "proton/taxonomy" in applied[ROUTES_REL_PATH]
    assert "ProtonTaxonomy" in applied[SIDEBAR_REL_PATH]
    assert applied[API_REL_PATH].strip()
    assert applied[PAGE_REL_PATH].strip()
    # 0057's own additions survive underneath, which is what stacking means.
    assert "proton_alert_preferences" in applied[ROUTES_REL_PATH]
    assert "ProtonAlertPreferences" in applied[SIDEBAR_REL_PATH]


def test_the_page_is_routable_and_offered_in_the_nav(applied: dict[str, str]) -> None:
    """The reachability assertion the missing patch was the whole gap in.

    A route whose component path does not match the file the patch creates is a
    404 at runtime, so the component path is compared against the created file
    rather than eyeballed.
    """
    routes = applied[ROUTES_REL_PATH]
    match = re.search(
        r"\{\s*path: 'proton/taxonomy',\s*name: '(\w+)',\s*"
        r"component: \(\) => import\('([^']+)'\),\s*"
        r"meta: \{ permissions: \[([^\]]*)\] \},",
        routes,
    )
    assert match, "no proton/taxonomy route in the applied routes file"
    route_name, component, permissions = match.groups()

    resolved = (Path("app/javascript/dashboard/routes/dashboard") / component).resolve()
    expected = (Path("/") / PAGE_REL_PATH).resolve()
    assert resolved.name == expected.name, (resolved, expected)
    assert component.endswith("views/ProtonTaxonomyPage.vue"), component
    assert "'administrator'" in permissions and "'agent'" not in permissions

    # And the nav entry points at that route's NAME, not a hand-written path.
    sidebar = applied[SIDEBAR_REL_PATH]
    nav = re.search(
        r"\.\.\.\(protonHasPermission\('([\w.]+)'\)\s*\?\s*\[\s*\{\s*"
        r"name: 'ProtonTaxonomy',\s*icon: '([^']+)',\s*label: '([^']+)',\s*"
        r"to: accountScopedRoute\('(\w+)'\),",
        sidebar,
    )
    assert nav, "no taxonomy nav entry in the applied Sidebar"
    gate, _icon, label, nav_target = nav.groups()
    assert nav_target == route_name, (nav_target, route_name)
    assert gate == "taxonomy.manage"
    assert label


def test_the_nav_gate_is_the_permission_the_router_itself_requires(
    applied: dict[str, str],
) -> None:
    """Three gates, one key, and only one of them a boundary.

    A nav gated on a permission the router does not check would show a page whose
    every write 403s; a nav gated on a key `seed.py` never grants would show it to
    nobody. Both are ways to ship an unreachable feature, so the key is compared
    across all three sources.
    """
    assert 'require_permission("taxonomy.manage"' in ROUTER_TEXT
    assert '"taxonomy.manage"' in SEED_PY.read_text(encoding="utf-8")
    assert "protonHasPermission('taxonomy.manage')" in applied[SIDEBAR_REL_PATH]
    # The page's own read path is NOT gated on it -- reading the taxonomy is not
    # an admin action, and the page says so instead of showing a blank screen.
    page = applied[PAGE_REL_PATH]
    assert "read-only access" in page
    assert "canManage" in page


def test_every_endpoint_the_page_calls_is_one_the_router_exposes(
    applied: dict[str, str],
) -> None:
    """Harvested from both sides, never retyped.

    This is the assertion that would have caught the whole class of defect: a
    path the page calls and the router does not serve is exactly as unreachable
    as no page at all.
    """
    called = set(re.findall(r"['\"`](/admin/taxonomy[^'\"`]*)['\"`]", applied[API_REL_PATH]))
    # Template-literal paths keep their `${...}`; reduce them to the router's
    # own parameter form so the two sets are comparable.
    normalised = {re.sub(r"\$\{[^}]*\}", "{key}", path) for path in called}
    assert normalised, "the api module calls no /admin/taxonomy path at all"

    prefix = re.search(r'APIRouter\(prefix="([^"]+)"', ROUTER_TEXT)
    assert prefix and prefix.group(1) == "/admin/taxonomy"
    exposed = {
        prefix.group(1) + re.sub(r"\{[^}]*\}", "{key}", route)
        for route in re.findall(
            r'@router\.(?:get|post|put|delete)\(\s*\n?\s*"([^"]+)"', ROUTER_TEXT
        )
    }
    assert exposed, "no routes parsed out of router.py"
    assert normalised <= exposed, (
        f"the page calls paths the router does not serve: {sorted(normalised - exposed)}"
    )
    # Every one of the router's own endpoints has a caller, so nothing is
    # mounted-but-unreachable in the other direction either.
    assert exposed <= normalised, f"router endpoints no page calls: {sorted(exposed - normalised)}"


def test_the_shipped_predicate_tells_a_flag_off_404_from_a_real_failure(
    applied: dict[str, str], tmp_path: Path
) -> None:
    """Executed in node, not re-implemented.

    Every path in this router is static, so a 404 can only mean "switched off" --
    and the page's whole difference between "you have not enabled this" and "this
    is broken" rests on that. A predicate that also swallowed a 403 or a 500
    would report a permissions failure as a disabled feature.
    """
    got = _eval_api_module(
        applied[API_REL_PATH],
        "const d = api.isDisabledError;\n"
        "process.stdout.write(JSON.stringify({\n"
        "  s404: d({ status: 404 }),\n"
        "  nested404: d({ response: { status: 404 } }),\n"
        "  s403: d({ status: 403 }),\n"
        "  s500: d({ status: 500 }),\n"
        "  named: d(new Error('404: TAXONOMY_ADMIN_ENABLED=false')),\n"
        "  network: d(new Error('NetworkError')),\n"
        "  nullish: d(null),\n"
        "}));\n",
        tmp_path,
    )
    assert got == {
        "s404": True,
        "nested404": True,
        "s403": False,
        "s500": False,
        "named": True,
        "network": False,
        "nullish": False,
    }, got


def test_the_shipped_calls_hit_the_right_paths_and_methods(
    applied: dict[str, str], tmp_path: Path
) -> None:
    """The retire call in particular: POST, and the key percent-encoded.

    A key with a slash or a space in it would otherwise silently address a
    different path, and the store accepts any non-empty key.
    """
    got = _eval_api_module(
        applied[API_REL_PATH],
        "const calls = {};\n"
        "calls.tree = await (async () => { try { return await api.getTaxonomyTree(); }\n"
        "  catch (e) { return String(e); } })();\n"
        "calls.save = await api.saveTaxonomyNode({ level: 1, key: 'k' });\n"
        "calls.retire = await api.retireTaxonomyNode('cat a/b');\n"
        "calls.coverage = await api.getTaxonomyCoverage();\n"
        "process.stdout.write(JSON.stringify(calls));\n",
        tmp_path,
    )
    # getTaxonomyTree reads `.tree` off the stub's echo object, which has none,
    # so it must degrade to [] rather than throwing.
    assert got["tree"] == []
    assert got["save"]["path"] == "/admin/taxonomy/node"
    assert got["save"]["opts"]["method"] == "POST"
    assert got["save"]["opts"]["body"] == {"level": 1, "key": "k"}
    assert got["retire"]["path"] == "/admin/taxonomy/node/cat%20a%2Fb/retire"
    assert got["retire"]["opts"]["method"] == "POST"
    assert got["coverage"]["path"] == "/admin/taxonomy/coverage"
    assert got["coverage"]["opts"] is None


def test_a_disabled_feature_renders_its_variable_name_and_never_an_empty_table(
    applied: dict[str, str],
) -> None:
    """A zero is a claim; an empty table is a claim too.

    "No active taxonomy nodes yet" against a tenant that simply never set
    `TAXONOMY_ADMIN_ENABLED` is a false statement about their data. The disabled
    branch has to precede the table in the render, and it has to name the
    variable, because "contact your administrator" does not tell an operator
    which line to edit.
    """
    page = applied[PAGE_REL_PATH]
    assert "TAXONOMY_ADMIN_ENABLED=false" in page
    assert "CATEGORY_DEPARTMENT_MAPPING_ENABLED=false" in page
    # The disabled branch is a v-else-if BEFORE the template that holds the
    # table, so a disabled tenant cannot fall through to it.
    disabled_at = page.index('v-else-if="disabled"')
    empty_at = page.index("No active taxonomy nodes yet")
    assert disabled_at < empty_at
    # The two flags fail independently: a disabled coverage report must not read
    # as a broken taxonomy page.
    assert "coverageDisabled" in page
    assert "The taxonomy above is unaffected." in page


async def test_the_page_consumes_the_orphans_that_retiring_a_parent_really_leaves(
    applied: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backend behaviour the confirmation panel exists for, driven for real.

    `retire_node` marks only the node it is given; `tree()` attaches a child only
    to an ACTIVE parent. So after retiring a parent its children are still
    `active` in the store and absent from the tree -- live nodes no surface shows.
    This test proves that with the real store, and then asserts the page reads the
    `active_children` the router hands back. Without the page half, the store
    behaviour is a data-loss trap; without the store half, the page's panel would
    look like defensive noise.
    """

    class _Snapshot:
        def __init__(self, data: dict[str, Any] | None) -> None:
            self._data = data

        @property
        def exists(self) -> bool:
            return self._data is not None

        def to_dict(self) -> dict[str, Any] | None:
            return dict(self._data) if self._data is not None else None

    class _Doc:
        def __init__(self, docs: dict[str, dict[str, Any]], doc_id: str) -> None:
            self._docs = docs
            self._id = doc_id

        def get(self) -> _Snapshot:
            return _Snapshot(self._docs.get(self._id))

        def set(self, data: dict[str, Any]) -> None:
            self._docs[self._id] = dict(data)

    class _Collection:
        def __init__(self, docs: dict[str, dict[str, Any]]) -> None:
            self._docs = docs

        def document(self, doc_id: str) -> _Doc:
            return _Doc(self._docs, doc_id)

        def get(self) -> list[_Snapshot]:
            return [_Snapshot(data) for data in self._docs.values()]

    class _Client:
        docs: ClassVar[dict[str, dict[str, Any]]] = {}

        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def collection(self, _name: str) -> _Collection:
            return _Collection(_Client.docs)

    _Client.docs = {}
    monkeypatch.setattr("chatbot.features.taxonomy.store.firestore.Client", _Client)

    store = TaxonomyStore(get_settings())

    await store.create_node(TaxonomyNode(level=1, key="type_x", label="Type X"))
    await store.create_node(TaxonomyNode(level=2, key="div_x", label="Div X", parent="type_x"))
    await store.create_node(TaxonomyNode(level=3, key="cat_x", label="Cat X", parent="div_x"))
    orphans = [n.key for n in await store.retire_node("div_x")]
    still_active = [n.key for n in await store.list_nodes(active_only=True)]

    def keys(nodes: list[dict[str, Any]]) -> list[str]:
        out: list[str] = []
        for node in nodes:
            out.append(node["key"])
            out.extend(keys(node["children"]))
        return out

    in_tree = keys(await store.tree())

    assert orphans == ["cat_x"], orphans
    assert "cat_x" in still_active, "the child was retired after all; the panel is unnecessary"
    assert "cat_x" not in in_tree, (
        "the orphan is still visible in the tree, so the confirmation panel is no "
        "longer load-bearing and this test should be revisited"
    )

    # The page half: it reads that list, warns before retiring, and offers a
    # retire per orphan rather than only naming the problem.
    page = applied[PAGE_REL_PATH]
    assert "active_children" in page
    assert "retireOrphan" in page
    assert "does NOT retire them" in page
    # And it never offers a delete, because the store has none.
    assert "there is no " in page and "delete" in page
    assert "deleteTaxonomy" not in applied[API_REL_PATH]


def test_the_department_field_is_described_as_a_mapping_and_not_as_routing(
    applied: dict[str, str],
) -> None:
    """Docs must be exactly as true as the code.

    Nothing applies a `dept_*` label from this field and nothing routes a case by
    it -- the coverage report is its only reader. "Department" beside a category
    reads as automatic routing, and this programme has already had to correct
    client material that implied a mapping did more than it did.
    """
    page = applied[PAGE_REL_PATH]
    assert "mapping only" in page
    assert "nothing applies a" in page.replace("\n", " ")
    assert "coverage report" in page


@pytest.mark.parametrize("rel", [PAGE_REL_PATH])
def test_the_shipped_sfc_compiles(applied: dict[str, str], rel: str, tmp_path: Path) -> None:
    """Compiled with Vue's own compiler where it is installed, skipped where it
    is not -- never quietly passed. A template that does not compile is a blank
    page after a 20-minute Cloud Build, which is the slowest possible way to find
    a typo."""
    if _NODE is None:  # pragma: no cover - environment dependent
        pytest.skip("node is not available")
    probe = subprocess.run(  # noqa: S603
        [_NODE, "-e", "require.resolve('@vue/compiler-sfc')"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:  # pragma: no cover - environment dependent
        pytest.skip(
            "@vue/compiler-sfc is not resolvable from this directory, so the SFC "
            "cannot be compiled. `npm install @vue/compiler-sfc` somewhere on "
            "node's resolution path makes this test run; it was run that way "
            "when 0060 was written and reported zero parse and template errors."
        )
    sfc = tmp_path / "Page.vue"
    sfc.write_text(applied[rel], encoding="utf-8")
    script = tmp_path / "compile.cjs"
    script.write_text(
        "const { parse, compileScript, compileTemplate } = require('@vue/compiler-sfc');\n"
        "const src = require('fs').readFileSync(process.argv[2], 'utf8');\n"
        "const { descriptor, errors } = parse(src, { filename: 'Page.vue' });\n"
        "const out = { parse: errors.map(String) };\n"
        "compileScript(descriptor, { id: 'x' });\n"
        "const t = compileTemplate({ source: descriptor.template.content, "
        "filename: 'Page.vue', id: 'x' });\n"
        "out.template = t.errors.map(String);\n"
        "process.stdout.write(JSON.stringify(out));\n",
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603
        [_NODE, str(script), str(sfc)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    got = json.loads(result.stdout)
    assert got == {"parse": [], "template": []}, got


def test_the_patch_declares_its_stacking_and_its_unverified_claims() -> None:
    """A patch header that overstates its evidence is how an unverified change
    gets cited as verified in a client review."""
    header = " ".join(PATCH_0060_TEXT.split("diff --git", 1)[0].split())
    assert "Stacks on **0057**" in header, "the header must declare what it stacks on"
    assert "Cloud Build" in header
    assert "Never applied to a real Chatwoot checkout" in header
    assert "git diff" in header, "the header must say the hunk arithmetic is git's"
    lowered = header.lower()
    assert "seen rendering in a browser" not in lowered.replace(
        "nothing here has been seen rendering in a browser", ""
    )
    assert "screenshot" not in lowered
    # Four files, and no unrelated JavaScript swept in.
    assert PATCH_0060_TEXT.count("diff --git") == 4
    for rel in (SIDEBAR_REL_PATH, ROUTES_REL_PATH, API_REL_PATH, PAGE_REL_PATH):
        assert f"diff --git a/{rel} b/{rel}" in PATCH_0060_TEXT
