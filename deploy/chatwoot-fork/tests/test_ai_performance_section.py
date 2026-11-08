"""Fork patch 0083 (Wave 2 task 27, FIX-47, RFP 2026_028 4.56): AI performance
section on the Bot report page. Content-level checks on the patch's added
lines; the chain-apply against upstream v4.15.1 is run by the SDD task."""
from pathlib import Path

PATCH = Path(__file__).resolve().parent.parent / "patches" / "0083-ai-performance-reports.patch"
ADDED = "\n".join(
    line[1:] for line in PATCH.read_text().split("\n") if line.startswith("+") and not line.startswith("+++")
)


def test_api_client_and_mount_are_added():
    assert "export function getAiPerformance(period)" in ADDED
    assert "/metrics/ai-performance" in ADDED
    assert "<ProtonAiPerformanceSection" in ADDED


def test_all_eight_reports_are_rendered():
    for title in (
        "AI Case Resolution (assignment-based)",
        "AI vs Human Handling",
        "AI Response Satisfaction",
        "AI Escalation Reasons",
        "AI Resolutions Not Reopened (proxy)",
        "AI Deflection Rate",
        "Recurring Issues",
        "Knowledge Base Gaps",
    ):
        assert title in ADDED, title


def test_definitions_are_shown_and_no_raw_html_is_injected():
    for col in ("deflection_definition", "accuracy_basis", "root_cause_basis", "kb_basis"):
        assert col in ADDED
    assert "v-html" not in ADDED


def test_bot_page_date_filter_is_passed_to_the_api():
    assert ':period="aiPeriod"' in ADDED
    assert "aiPeriod()" in ADDED
    assert "granularity: 'day'" in ADDED
    assert "getAiPerformance(props.period)" in ADDED
    # a changed range reloads, and un-scoped blocks are captioned
    assert "watch(" in ADDED and "props.period?.from" in ADDED
    assert "status === 'unfiltered'" in ADDED and "All time" in ADDED


def test_root_cause_compares_the_last_complete_week():
    assert "filter(r => r.week_complete)" in ADDED
    assert "last complete week" in ADDED


def test_labels_are_honest_and_definitions_shown():
    assert "Case resolution and deflection use the same" in ADDED
    assert "weighted by" in ADDED  # CSAT definition under the card
    assert "Not a measure of answer correctness" in ADDED
    assert "sample_case_detail" not in ADDED
