"""Publish a markdown PRD as a Google Doc via Drive v3 HTML conversion.

Drive converts an uploaded ``text/html`` part into a native Google Doc when the
metadata ``mimeType`` is ``application/vnd.google-apps.document``; that keeps
tables intact without needing the Docs API. Auth is ADC through google-auth's
``AuthorizedSession`` (no google-api-python-client). Errors carry only the HTTP
status, never headers or bodies, so no token can leak into a reason string.
"""

from __future__ import annotations

import json
import uuid

import markdown as _markdown

_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
_QUERY = "uploadType=multipart&supportsAllDrives=true&fields=id,webViewLink"
_SCOPES = ["https://www.googleapis.com/auth/drive"]

_CSS = (
    "body{font-family:Arial,sans-serif;font-size:11pt}"
    "table{border-collapse:collapse;width:100%}"
    "th,td{border:1px solid #999;padding:4px;vertical-align:top;font-family:Arial,sans-serif}"
    "th{background:#eee}"
)


class DriveError(Exception):
    """Non-2xx Drive response; ``status`` is the only detail kept."""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


def markdown_to_html(md: str) -> str:
    """Convert markdown to a full HTML document (tables, lists, <br> in cells)."""
    body = _markdown.markdown(md, extensions=["tables", "sane_lists", "nl2br"])
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<style>{_CSS}</style></head><body>{body}</body></html>"
    )


def _default_session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    creds, _ = google.auth.default(scopes=_SCOPES)
    return AuthorizedSession(creds)


def _multipart(meta: dict, html: str) -> tuple[bytes, str]:
    boundary = f"logisly-{uuid.uuid4().hex}"
    parts = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(meta)}\r\n"
        f"--{boundary}\r\nContent-Type: text/html; charset=UTF-8\r\n\r\n"
        f"{html}\r\n--{boundary}--"
    )
    return parts.encode("utf-8"), f"multipart/related; boundary={boundary}"


def publish(name, markdown, folder_id, file_id=None, session=None) -> dict:
    """Create (or, with ``file_id``, replace the content of) a Google Doc."""
    html = markdown_to_html(markdown)
    if file_id:
        method, url = "PATCH", f"{_UPLOAD}/{file_id}?{_QUERY}"
        meta = {"name": name}
    else:
        method, url = "POST", f"{_UPLOAD}?{_QUERY}"
        meta = {
            "name": name,
            "mimeType": "application/vnd.google-apps.document",
            "parents": [folder_id],
        }
    data, ctype = _multipart(meta, html)
    session = session or _default_session()
    resp = session.request(method, url, data=data, headers={"Content-Type": ctype}, timeout=30)
    if not 200 <= resp.status_code < 300:
        raise DriveError(resp.status_code)
    body = resp.json()
    return {"file_id": body["id"], "url": body["webViewLink"]}
