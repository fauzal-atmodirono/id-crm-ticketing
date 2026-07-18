"""Smoke: verify taxonomy-manager/index.html exists and contains required patterns."""
from pathlib import Path

HTML_PATH = Path(__file__).parent.parent.parent / "apps" / "taxonomy-manager" / "index.html"


def test_html_file_exists():
    assert HTML_PATH.exists(), f"Expected {HTML_PATH} to exist"


def test_html_contains_url_param_bootstrap():
    content = HTML_PATH.read_text()
    assert "URLSearchParams" in content, "Should read config from URL params"
    assert "chatwootUrl" in content or "chatwoot_url" in content.lower(), \
        "Should read chatwootUrl from URL params"
    assert "apiToken" in content or "api_token" in content.lower(), \
        "Should read apiToken from URL params"
    assert "accountId" in content or "account_id" in content.lower(), \
        "Should read accountId from URL params"


def test_html_contains_postmessage_handshake():
    content = HTML_PATH.read_text()
    assert "chatwoot-dashboard-app:fetch-info" in content, \
        "Should send the Chatwoot Dashboard App postMessage handshake"


def test_html_contains_labels_api_call():
    content = HTML_PATH.read_text()
    assert "/api/v1/accounts/" in content, "Should call Chatwoot accounts API"
    assert "labels" in content, "Should reference labels endpoint"


def test_html_uses_x_api_token_or_api_access_token_header():
    content = HTML_PATH.read_text()
    # Chatwoot Application API uses api_access_token header
    assert "api_access_token" in content, \
        "Should use api_access_token header (Chatwoot Application API auth)"
