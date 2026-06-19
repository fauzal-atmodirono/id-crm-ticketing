from __future__ import annotations

from scraper.html_utils import clean_html_for_storage

_SOURCE_URL = "https://www.proton.com/models/all-new-x50"

_SAMPLE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Proton X50</title>
  <script>var secret = "should be gone";</script>
  <style>body { color: red; }</style>
</head>
<body>
  <p>Welcome to the X50 page.</p>
  <script>alert("also gone");</script>
</body>
</html>
"""


def test_clean_html_body_text_preserved() -> None:
    result = clean_html_for_storage(_SAMPLE_HTML, _SOURCE_URL)
    assert "Welcome to the X50 page." in result
    assert result.count("<body") == 1


def test_clean_html_canonical_present() -> None:
    result = clean_html_for_storage(_SAMPLE_HTML, _SOURCE_URL)
    assert f'<link rel="canonical" href="{_SOURCE_URL}">' in result


def test_clean_html_script_contents_removed() -> None:
    result = clean_html_for_storage(_SAMPLE_HTML, _SOURCE_URL)
    assert "should be gone" not in result
    assert "also gone" not in result


def test_clean_html_style_contents_removed() -> None:
    result = clean_html_for_storage(_SAMPLE_HTML, _SOURCE_URL)
    assert "color: red" not in result


def test_clean_html_has_doctype_and_title() -> None:
    result = clean_html_for_storage(_SAMPLE_HTML, _SOURCE_URL)
    assert result.startswith("<!DOCTYPE html>")
    assert "<title>Proton X50</title>" in result


def test_clean_html_fallback_title_when_missing() -> None:
    html_no_title = "<html><body><p>Hi</p></body></html>"
    result = clean_html_for_storage(html_no_title, _SOURCE_URL)
    assert "<title>PROTON Page</title>" in result
