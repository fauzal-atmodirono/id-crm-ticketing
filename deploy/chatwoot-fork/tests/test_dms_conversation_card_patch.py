"""Fork patch 0099 (brief 11, R59-R60): a partner-system ("DMS/TSP") card
in the conversation's right panel. It reuses the backend Customer 360 DMS
path (one cached /admin/customer360/dms/status call per page, then
/admin/customer360/dms?conversation_id=N -- never a phone or plate in the
URL) and says "not connected" while the DMS is off. Shown only with the
customer360 feature and customer360.view; wording from the term dictionary. Cannot prove the vite build; only
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


def test_card_is_gated_like_customer360(card):
    assert (
        "import { useProtonPermissions } from 'dashboard/composables/useProtonPermissions';"
        in card
    )
    assert "import { useCustomFeatures } from 'dashboard/composables/useCustomFeatures';" in card
    assert "() => hasFeature('customer360') && hasPermission('customer360.view')" in card
    assert '<div v-if="permitted" class="px-2 pt-2" data-proton-dms-card>' in card
    # no request at all until permitted
    assert "if (!permitted.value) return;" in card


def test_card_uses_the_customer360_dms_endpoints_by_conversation(card):
    assert "import { adminRequest } from 'dashboard/api/protonAdmin';" in card
    assert "adminRequest('/admin/customer360/dms/status', {" in card
    assert "conversation_id: String(props.conversationId)," in card
    assert "`/admin/customer360/dms?${params.toString()}`" in card
    # R59: the card never puts a phone or plate in the URL
    assert "params.set(" not in card
    assert "'phone'" not in card and "'vehicle_no'" not in card


def test_status_is_cached_per_page_bounded_and_not_cached_on_failure(card):
    assert "let statusPromise = null;" in card
    assert "if (!statusPromise) {" in card
    assert "const STATUS_TIMEOUT_MS = 10000;" in card
    assert "setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS)" in card
    assert ".catch(e => {\n        statusPromise = null;\n        throw e;" in card


def test_card_says_not_connected_in_tenant_terms(card):
    assert "Not connected" in card  # CONV-019's regex
    assert "Proton" not in card.split("<template>", 1)[1]
    flat = " ".join(card.split())
    assert (
        "{{ t('partner_system') }} not connected — {{ t('asset', 'lower') }} and "
        "service history will appear here once the {{ t('partner_system') }} is "
        "connected."
    ) in flat
    assert "{{ t('partner_system') }} / {{ t('asset') }}" in flat
    assert "if (!status.connected) {\n    state.value = 'off';" in card


def test_card_states_no_record_data_and_error(card):
    flat = " ".join(card.split())
    assert "No {{ t('partner_system') }} record for this customer." in flat
    assert "{{ t('asset', 'plural') }}" in flat
    assert ">Service history<" in card
    assert "state.value = 'error';" in card
    assert "if (block.status !== 'ok') {" in card
    assert "if (!block.customer) {" in card
    assert "Mock data" in card
    assert ':key="`${index}-${vehicle.vehicle_no}`"' in card


def test_card_waits_for_the_contact(card):
    assert "if (!permitted.value || !contactId.value) return '';" in card
    assert "// No contact yet (or no longer permitted): stay in loading, no request." in card


def test_card_drops_stale_responses_and_bounds_the_request(card):
    assert "if (token !== requestToken) return;" in card
    assert "setTimeout(() => controller.abort(), LOOKUP_TIMEOUT_MS)" in card


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_card_script_parses(card, tmp_path):
    script = card.split("<script setup>", 1)[1].split("</script>", 1)[0]
    (tmp_path / "card.mjs").write_text(script)
    subprocess.run(["node", "--check", str(tmp_path / "card.mjs")], check=True)
