import json
from types import SimpleNamespace

import pytest

from logisly_agents.uc3 import gdoc, tools

PRD = """# PRD

| # | Story | Acceptance | Jira # |
|---|-------|------------|--------|
| 1 | As Ops, I want a | 1. satu<br>2. dua | |
| 2 | As Ops, I want b | 1. satu | |
| 3 | As Ops, I want c | 1. satu | |
"""


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {"id": "F1", "webViewLink": "https://docs.google.com/document/d/F1"}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, status=200):
        self.status = status
        self.patch_status = None  # overrides status for PATCH calls when set
        self.calls = []

    def request(self, method, url, data=None, headers=None, **kw):
        self.calls.append({"method": method, "url": url, "data": data, "headers": headers})
        if method == "PATCH" and self.patch_status:
            return FakeResp(self.patch_status)
        if method == "POST" and getattr(self, "new_id", None):
            return FakeResp(
                self.status,
                {"id": self.new_id, "webViewLink": f"https://docs.google.com/document/d/{self.new_id}"},
            )
        return FakeResp(self.status)


def _ctx():
    return SimpleNamespace(state={})


@pytest.fixture
def fake(monkeypatch):
    s = FakeSession()
    monkeypatch.setattr(gdoc, "_default_session", lambda: s)
    monkeypatch.setattr(tools, "get_settings", lambda: SimpleNamespace(prd_folder_id="FOLDER1"))
    return s


def test_table_converts_to_four_rows():
    html = gdoc.markdown_to_html(PRD)
    assert html.count("<tr>") == 4
    assert "<table" in html and "<br" in html and "Arial" in html


def test_create_posts_with_folder_and_table(fake):
    ctx = _ctx()
    res = tools.publish_prd(ctx, "uc3", PRD)
    assert res["status"] == "published" and res["updated"] is False
    assert res["url"].endswith("F1")
    c = fake.calls[0]
    body = c["data"].decode() if isinstance(c["data"], bytes) else c["data"]
    assert c["method"] == "POST" and "uploadType=multipart" in c["url"]
    assert "FOLDER1" in body and "<table" in body and "PRD - uc3" in body
    assert "uc3" in ctx.state[tools.DOCS_KEY]


def test_second_call_patches_same_file(fake):
    ctx = _ctx()
    tools.publish_prd(ctx, "uc3", PRD)
    res = tools.publish_prd(ctx, "uc3", PRD + "\nmore")
    assert res["updated"] is True
    assert [c["method"] for c in fake.calls] == ["POST", "PATCH"]
    assert "/files/F1?" in fake.calls[1]["url"]
    assert ctx.state["uc3:gdocs"]["uc3"]["file_id"] == "F1"


def test_no_folder_unavailable_but_saved(monkeypatch):
    monkeypatch.setattr(tools, "get_settings", lambda: SimpleNamespace(prd_folder_id=""))
    monkeypatch.setattr(gdoc, "_default_session", lambda: pytest.fail("network"))
    ctx = _ctx()
    res = tools.publish_prd(ctx, "uc3", PRD)
    assert res == {
        "status": "unavailable",
        "reason": "LOGISLY_PRD_FOLDER_ID belum diisi",
        "saved": True,
    }
    assert ctx.state[tools.DOCS_KEY]["uc3"] == PRD


def test_403_unavailable_no_secrets(fake):
    fake.status = 403
    ctx = _ctx()
    res = tools.publish_prd(ctx, "uc3", PRD)
    assert res["status"] == "unavailable" and res["saved"] is True
    assert "403" in res["reason"] and "Bearer" not in res["reason"]
    assert ctx.state[tools.DOCS_KEY]["uc3"] == PRD


def test_multipart_boundary_and_patch_metadata(fake):
    ctx = _ctx()
    tools.publish_prd(ctx, "uc3", PRD)
    tools.publish_prd(ctx, "uc3", PRD + "\nmore")
    for c in fake.calls:
        boundary = c["headers"]["Content-Type"].split("boundary=")[1]
        assert f"--{boundary}" in c["data"].decode() and f"--{boundary}--" in c["data"].decode()
    patch = fake.calls[1]["data"].decode()
    meta = json.loads(patch.split("\r\n\r\n")[1].split("\r\n--")[0])
    assert meta == {"name": "PRD - uc3"}
    assert "parents" not in meta and "mimeType" not in meta


def test_patch_404_recreates_once_and_stores_new_id(fake):
    ctx = _ctx()
    tools.publish_prd(ctx, "uc3", PRD)
    fake.patch_status = 404
    fake.new_id = "F2"
    res = tools.publish_prd(ctx, "uc3", PRD + "\nmore")
    assert res["status"] == "published" and res["url"].endswith("F2")
    assert [c["method"] for c in fake.calls] == ["POST", "PATCH", "POST"]
    assert ctx.state["uc3:gdocs"]["uc3"]["file_id"] == "F2"
    assert res["updated"] is False


def test_patch_other_error_does_not_retry(fake):
    ctx = _ctx()
    tools.publish_prd(ctx, "uc3", PRD)
    fake.patch_status = 500
    res = tools.publish_prd(ctx, "uc3", PRD + "\nmore")
    assert res["status"] == "unavailable"
    assert [c["method"] for c in fake.calls] == ["POST", "PATCH"]
