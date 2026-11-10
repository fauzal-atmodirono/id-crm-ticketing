"""Fork patch 0099 (brief 11): a "DMS / Vehicle" card in the conversation's
right panel. It reuses the backend Customer 360 DMS path (one cached
/admin/customer360/dms/status call per page, then /admin/customer360/dms for
the open conversation's contact) and says "not connected" while Proton's DMS
is off. Hidden without customer360.view. Cannot prove the vite build; only
Cloud Build does (the whole chain is applied on a v4.15.1 checkout by hand).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

FORK = Path(__file__).resolve().parent.parent
P0099 = FORK / "patches" / "0099-dms-conversation-card.patch"
CARD = "app/javascript/dashboard/components/proton/ProtonDmsCard.vue"
CONTACT_PANEL = "app/javascript/dashboard/routes/dashboard/conversation/ContactPanel.vue"


def _added(path: str) -> str:
    out, cur = [], None
    for line in P0099.read_text().splitlines():
        if line.startswith("diff --git "):
            cur = line.split(" b/", 1)[1]
        elif cur == path and line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return "\n".join(out)


@pytest.fixture(scope="module")
def card() -> str:
    return _added(CARD)


def test_patch_is_additions_only_and_touches_only_two_files():
    text = P0099.read_text()
    removed = [
        line for line in text.splitlines() if line.startswith("-") and not line.startswith("---")
    ]
    assert removed == []
    files = [line.split(" b/", 1)[1] for line in text.splitlines() if line.startswith("diff --git ")]
    assert sorted(files) == sorted([CARD, CONTACT_PANEL])


def test_contact_panel_mounts_the_card_after_the_call_recording_panel():
    added = _added(CONTACT_PANEL)
    assert "import ProtonDmsCard from 'dashboard/components/proton/ProtonDmsCard.vue';" in added
    assert '<ProtonDmsCard :contact="contact" :conversation-id="conversationId" />' in added
    hunk = P0099.read_text().split(f"b/{CONTACT_PANEL}", 2)[-1]
    assert hunk.index('<ProtonCallRecordingPanel :conversation-id="conversationId" />') < hunk.index(
        "<ProtonDmsCard"
    )


def test_card_is_hidden_without_customer360_view(card):
    assert (
        "import { useProtonPermissions } from 'dashboard/composables/useProtonPermissions';"
        in card
    )
    assert "hasPermission('customer360.view')" in card
    assert '<div\n    v-if="permitted"' in card
    # no request at all until permitted
    assert "if (!permitted.value) return;" in card


def test_card_uses_the_customer360_dms_endpoints(card):
    assert "import { adminRequest } from 'dashboard/api/protonAdmin';" in card
    assert "adminRequest('/admin/customer360/dms/status')" in card
    assert "`/admin/customer360/dms?${params.toString()}`" in card
    # status is cached per page: one call however many conversations open
    assert "let statusPromise = null;" in card
    assert "if (!statusPromise) {" in card
    # a failed status call is not cached as "not connected" forever
    assert "statusPromise = null;" in card


def test_card_says_not_connected_while_dms_is_off(card):
    assert "Not connected" in card
    flat = " ".join(card.split())
    assert (
        "DMS not connected — vehicle and service history will appear here once "
        "Proton's DMS is connected."
    ) in flat
    assert "if (!status.connected) {\n    state.value = 'off';" in card


def test_card_states_no_record_data_and_error(card):
    assert "No DMS/TSP record for this customer" in card
    assert ">Vehicles<" in card
    assert ">Service history<" in card
    assert "state.value = 'error';" in card
    assert "if (block.status !== 'ok') {" in card
    assert "if (!block.customer) {" in card
    assert "Mock data" in card


def test_card_looks_up_by_phone_and_plate(card):
    assert "props.contact?.phone_number" in card
    assert "custom_attributes?.vehicle_no" in card
    assert "custom_attributes?.vehicle_plate" in card
    assert "params.set('phone', phone.value);" in card
    assert "params.set('vehicle_no', plate.value);" in card


def test_card_drops_stale_responses_and_bounds_the_request(card):
    assert "if (token !== requestToken) return;" in card
    assert "new AbortController()" in card
    assert "setTimeout(() => controller.abort(), LOOKUP_TIMEOUT_MS)" in card


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_card_script_parses(card, tmp_path):
    script = card.split("<script setup>", 1)[1].split("</script>", 1)[0]
    (tmp_path / "card.mjs").write_text(script)
    subprocess.run(["node", "--check", str(tmp_path / "card.mjs")], check=True)
