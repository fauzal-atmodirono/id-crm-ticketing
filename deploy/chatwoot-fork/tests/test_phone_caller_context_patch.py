"""Fork patch 0091 (2026-11-09 phone caller context): the ringing card shows
caller name / vehicle / dealer, and Accept opens the conversation plus the
caller's Customer 360 (which now searches its ?q= on load).

Every file 0091 touches is fork-created, so the chain for those files is
applied to an empty tree (no upstream needed), in order, exactly like
simulate_patch_chain.sh. Cannot prove the vite build; only Cloud Build does.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

FORK = Path(__file__).resolve().parent.parent
PATCHES = sorted((FORK / "patches").glob("*.patch"))
P0091 = FORK / "patches" / "0091-phone-caller-context.patch"
SP = "app/javascript/dashboard/composables/useProtonSoftphone.js"
PANEL = "app/javascript/dashboard/components-next/softphone/ProtonSoftphonePanel.vue"
C360 = "app/javascript/dashboard/views/ProtonCustomer360Page.vue"
FILES = (SP, PANEL, C360)


@pytest.fixture(scope="module")
def tree():
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    for p in PATCHES:
        text = p.read_text()
        includes = [f"--include={f}" for f in FILES if f"b/{f}" in text]
        if includes:
            subprocess.run(["git", "apply", *includes, str(p)], cwd=d, check=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_patch_is_additions_only():
    body = P0091.read_text()
    removed = [
        line for line in body.splitlines() if line.startswith("-") and not line.startswith("---")
    ]
    assert removed == []


def test_ring_card_reads_caller_context_params(tree):
    sp = (tree / SP).read_text()
    for key in ("caller_name", "vehicle", "dealer"):
        assert f"params.get('{key}')" in sp
    panel = (tree / PANEL).read_text()
    assert "incoming?.callerName" in panel
    assert "[incoming.vehicle, incoming.dealer].filter(Boolean).join(' · ')" in panel


def test_accept_opens_conversation_and_customer360_in_the_click(tree):
    panel = (tree / PANEL).read_text()
    assert '@click.capture="onPanelClickCapture"' in panel
    assert "data-proton-accept" in panel and "closest?.('[data-proton-accept]')" in panel
    assert "name: 'proton_customer360'" in panel and "query: { q }" in panel
    assert "window.open(href, '_blank', 'noopener')" in panel
    # a browser-softphone identity is not a phone number to search
    assert "q.startsWith('client:')" in panel
    # the original Accept handler is untouched
    assert '@click="accept"' in panel


def test_customer360_searches_its_query_on_load(tree):
    src = (tree / C360).read_text()
    assert "mounted() {" in src
    assert "this.$route?.query?.q" in src and "this.search();" in src


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_parses(tree, tmp_path):
    (tmp_path / "a.mjs").write_text((tree / SP).read_text())
    panel = (tree / PANEL).read_text()
    (tmp_path / "b.mjs").write_text(panel.split("<script setup>", 1)[1].split("</script>", 1)[0])
    c360 = (tree / C360).read_text()
    (tmp_path / "c.mjs").write_text(c360.split("<script>", 1)[1].split("</script>", 1)[0])
    for f in ("a.mjs", "b.mjs", "c.mjs"):
        subprocess.run(["node", "--check", str(tmp_path / f)], check=True)
