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
    assert "<ProtonAiPerformanceSection />" in ADDED


def test_all_eight_reports_are_rendered():
    for title in (
        "AI Case Resolution",
        "AI vs Human Handling",
        "AI Response Satisfaction",
        "AI Escalation Reasons",
        "AI Accuracy",
        "AI Deflection Rate",
        "Recurring Issues",
        "Knowledge Base Gaps",
    ):
        assert title in ADDED, title


def test_definitions_are_shown_and_no_raw_html_is_injected():
    for col in ("deflection_definition", "accuracy_basis", "root_cause_basis", "kb_basis"):
        assert col in ADDED
    assert "v-html" not in ADDED
