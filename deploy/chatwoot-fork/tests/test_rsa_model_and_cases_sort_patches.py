"""Fork patches 0097 + 0098 (Playwright run 1, 10 Nov; tracker RSA-001, CASE-002).

0097: the RSA Incident Log form marks Vehicle model required (label asterisk,
`required` input, a client-side check that names the field) and an inline edit
cannot blank it; the backend's 422 detail reaches the toast via httpFailure.

0098: the Cases page gets a sort control -- Newest first (created date desc,
the default), Oldest first, Recently updated -- passed to Chatwoot's own
`sort_by` on /conversations so the order holds across every fetched page.

Both files are fork-created, so the chain is replayed onto an empty repo for
just those paths (same idea as simulate_patch_chain.sh) and the final text is
asserted on.
"""
import subprocess
from pathlib import Path

import pytest

FORK = Path(__file__).resolve().parent.parent
PATCHES = sorted((FORK / "patches").glob("*.patch"))
P0097 = FORK / "patches" / "0097-rsa-vehicle-model-required.patch"
P0098 = FORK / "patches" / "0098-cases-sort.patch"
D = "app/javascript/dashboard/"
RSA_PAGE = D + "views/ProtonRsaPage.vue"
CASES_PAGE = D + "views/ProtonCasesPage.vue"
CASES_API = D + "api/protonCases.js"
CASES_TABLE = D + "components/widgets/ProtonCasesTable.vue"


def _final(tmp_path_factory, path: str) -> str:
    repo = tmp_path_factory.mktemp("chain")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for p in PATCHES:
        if f"b/{path}" not in p.read_text():
            continue
        subprocess.run(
            ["git", "apply", f"--include={path}", str(p)], cwd=repo, check=True
        )
    return (repo / path).read_text()


@pytest.fixture(scope="module")
def rsa_page(tmp_path_factory):
    return _final(tmp_path_factory, RSA_PAGE)


@pytest.fixture(scope="module")
def cases_page(tmp_path_factory):
    return _final(tmp_path_factory, CASES_PAGE)


@pytest.fixture(scope="module")
def cases_table(tmp_path_factory):
    return _final(tmp_path_factory, CASES_TABLE)


@pytest.fixture(scope="module")
def cases_api(tmp_path_factory):
    return _final(tmp_path_factory, CASES_API)


def _removed(patch: Path) -> list[str]:
    return [
        ln for ln in patch.read_text().splitlines()
        if ln.startswith("-") and not ln.startswith("---")
    ]


@pytest.mark.parametrize("patch", [P0097, P0098])
def test_patches_exist_and_only_touch_fork_files(patch):
    files = [
        ln.split(" b/", 1)[1]
        for ln in patch.read_text().splitlines()
        if ln.startswith("diff --git ")
    ]
    assert files
    for f in files:
        assert "/proton" in f.lower() or "Proton" in f, f


def test_patches_remove_almost_nothing():
    # New last patches may edit a couple of lines they must change; keep it tiny.
    assert len(_removed(P0097)) <= 4
    assert len(_removed(P0098)) <= 4


# --- 0097: RSA vehicle model required ---------------------------------------


def test_vehicle_model_label_has_asterisk(rsa_page):
    assert ">{{ t('asset_model') }} *</label" in rsa_page


def test_vehicle_model_create_input_is_required(rsa_page):
    block = rsa_page.split('v-model="form.vehicle_model"', 1)[1].split("/>", 1)[0]
    assert "required" in block


def test_client_check_names_the_field_before_submit(rsa_page):
    assert "missingVehicleModel(" in rsa_page
    assert "`${this.t('asset_model')} is required.`" in rsa_page
    create = rsa_page.split("async create() {", 1)[1].split("async ", 1)[0]
    assert create.index("missingVehicleModel(this.form)") < create.index(
        "createRsaIncident("
    )
    save = rsa_page.split("async saveEdit(id) {", 1)[1].split("async ", 1)[0]
    assert save.index("missingVehicleModel(this.editForm, 'editError')") < save.index(
        "updateRsaIncident("
    )


def test_inline_message_is_rendered(rsa_page):
    assert 'v-if="formError"' in rsa_page


# --- 0098: Cases sort --------------------------------------------------------


def test_sort_options_newest_first_default(cases_api, cases_page):
    assert "export const CASE_SORTS = [" in cases_api
    sorts = cases_api.split("export const CASE_SORTS = [", 1)[1].split("];", 1)[0]
    order = [sorts.index(k) for k in (
        "'created_at_desc'", "'created_at_asc'", "'last_activity_at_desc'"
    )]
    assert order == sorted(order)
    assert "label: 'Newest first'" in sorts
    assert "label: 'Oldest first'" in sorts
    assert "label: 'Recently updated'" in sorts
    assert "sortBy: DEFAULT_CASE_SORT," in cases_page
    assert "export const DEFAULT_CASE_SORT = 'created_at_desc';" in cases_api


def test_sort_goes_to_chatwoot_server_side(cases_api):
    assert "export async function fetchAllCases({ sortBy } = {}) {" in cases_api
    assert "ConversationApi.get({ status: 'all', page, sortBy })" in cases_api


def test_rows_are_ordered_and_deduped_client_side_too(cases_api):
    # Paging while cases arrive can shift a row across pages; the final list
    # is re-sorted and deduped by id so it really is in the chosen order.
    assert "export function sortCaseRows(" in cases_api
    assert "lastActivityAt: blankToNull(conversation.last_activity_at)," in cases_api
    fetch = cases_api.split("export async function fetchAllCases(", 1)[1]
    assert "if (sortBy) rows.splice(0, rows.length, ...sortCaseRows(rows, sortBy));" in fetch


def test_page_has_sort_control_and_passes_it(cases_page):
    assert 'v-model="sortBy"' in cases_page
    assert "Sort by" in cases_page
    assert 'v-for="option in sortOptions"' in cases_page
    assert "await fetchAllCases({ sortBy: this.sortBy });" in cases_page
    assert '@change="resort"' in cases_page


def test_weekly_report_still_calls_without_sort():
    weekly = (FORK / "patches" / "0044-weekly-report.patch").read_text()
    assert "await fetchAllCases();" in weekly
    assert "ProtonWeeklyReport" not in P0098.read_text()


# --- review follow-ups (10 Nov) ---------------------------------------------


def test_edit_row_has_its_own_error(rsa_page):
    """The inline-edit message renders in the edit row, not the create form."""
    assert "editError: ''," in rsa_page
    edit_row = rsa_page.split('v-model="editForm.vehicle_model"', 1)[1].split("</td>", 1)[0]
    assert 'v-if="editError"' in edit_row
    assert "{{ editError }}" in edit_row
    start = rsa_page.split("startEdit(incident) {", 1)[1].split("},\n", 1)[0]
    assert "this.editError = '';" in start
    cancel = rsa_page.split("cancelEdit() {", 1)[1].split("},", 1)[0]
    assert "this.editError = '';" in cancel
    create_form = rsa_page.split("<!-- (b) Data-entry form -->", 1)[1].split("</form>", 1)[0]
    assert "editError" not in create_form


def test_cases_table_shows_created_column(cases_table):
    heads = cases_table.split("<thead>", 1)[1].split("</thead>", 1)[0]
    assert ">Created</th>" in heads
    for kept in ("Case ID", "Division", "Concern", "Car Plate", "Status"):
        assert kept in heads
    assert heads.index("Case ID") < heads.index(">Created</th>")
    assert "{{ formatCreated(row.createdAt) }}" in cases_table
    assert "formatCreated(createdAt) {" in cases_table
