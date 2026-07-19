"""Smoke: verify knowledge-manager/index.html exists and contains required patterns."""
from pathlib import Path

HTML_PATH = Path(__file__).parent.parent.parent / "apps" / "knowledge-manager" / "index.html"


def test_html_file_exists():
    assert HTML_PATH.exists(), f"Expected {HTML_PATH} to exist"


def test_html_contains_url_param_bootstrap():
    content = HTML_PATH.read_text()
    assert "URLSearchParams" in content, "Should read config from URL params"
    assert "backend" in content, "Should read backend from URL params"
    assert "key" in content, "Should read key from URL params"


def test_html_contains_kb_faq_fetch():
    content = HTML_PATH.read_text()
    assert "/kb/faq" in content, "Should call the /kb/faq endpoint"


def test_html_uses_x_api_key_header():
    content = HTML_PATH.read_text()
    assert "x-api-key" in content, "Should use x-api-key header for backend auth"
