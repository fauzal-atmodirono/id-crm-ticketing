from __future__ import annotations

from scraper.config import ScraperSettings
from scraper.transport import Transport, needs_render

SETTINGS = ScraperSettings()


def test_needs_render_true_for_empty_main() -> None:
    html = "<html><body><main></main></body></html>"
    assert needs_render(html, SETTINGS) is True


def test_needs_render_false_for_rich_main() -> None:
    html = "<html><body><main>" + ("content " * 100) + "</main></body></html>"
    assert needs_render(html, SETTINGS) is False


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def get(self, url: str) -> _FakeResponse:          return self._response

    def close(self) -> None:
        pass


def _make_transport(fake_client: _FakeClient) -> Transport:
    t = Transport.__new__(Transport)
    t._settings = SETTINGS
    t._client = fake_client  # type: ignore[assignment]
    t._driver = None
    return t


def test_get_bytes_returns_content_on_200() -> None:
    payload = b"PDF_BYTES"
    t = _make_transport(_FakeClient(_FakeResponse(200, payload)))
    assert t.get_bytes("https://example.com/file.pdf") == payload


def test_get_bytes_returns_none_on_non_200() -> None:
    t = _make_transport(_FakeClient(_FakeResponse(404, b"")))
    assert t.get_bytes("https://example.com/missing.pdf") is None


def test_get_bytes_returns_none_on_exception() -> None:
    class _ErrorClient:
        def get(self, url: str) -> _FakeResponse:              raise ConnectionError("network down")

        def close(self) -> None:
            pass

    t = _make_transport(_ErrorClient())  # type: ignore[arg-type]
    assert t.get_bytes("https://example.com/file.pdf") is None
