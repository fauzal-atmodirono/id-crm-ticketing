"""An in-memory Chatwoot Application API for the demo seeder tests.

Stateful (contacts, conversations, labels, messages) so seed -> reset ->
seed can be exercised end to end, and it records every request so the
safety tests can prove no mutating call ever named a real record's id.
Response shapes follow Chatwoot v4 (contacts wrap in ``payload``, a
conversation is returned bare, ``POST /contacts`` returns
``payload.contact``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from chatbot.features.demo.crm import DemoCrmClient
from chatbot.platform.config import Settings

API = "http://chatwoot.test"
ACCOUNT = 7
INBOX = 42


@dataclass
class Call:
    method: str
    path: str
    body: Any


@dataclass
class FakeChatwoot:
    inbox_channel: str = "Channel::Api"
    bot_on_inbox: bool = True
    contacts: dict[int, dict[str, Any]] = field(default_factory=dict)
    conversations: dict[int, dict[str, Any]] = field(default_factory=dict)
    labels: set[str] = field(default_factory=lambda: {"audit_test", "dealer_petaling_jaya"})
    calls: list[Call] = field(default_factory=list)
    fail_conversation_delete: bool = False
    _next_id: int = 1000

    # --- helpers for tests ---------------------------------------------------
    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def add_contact(self, name: str, phone: str | None = None, **attrs: Any) -> int:
        cid = self._id()
        self.contacts[cid] = {
            "id": cid,
            "name": name,
            "phone_number": phone,
            "custom_attributes": dict(attrs),
        }
        return cid

    def add_conversation(
        self, contact_id: int, labels: list[str] | None = None, status: str = "open", **attrs: Any
    ) -> int:
        vid = self._id()
        self.conversations[vid] = {
            "id": vid,
            "inbox_id": INBOX,
            "contact_id": contact_id,
            "status": status,
            "labels": list(labels or []),
            "custom_attributes": dict(attrs),
            "additional_attributes": {},
            "messages": [],
            "created_at": vid,
        }
        return vid

    def mutations(self) -> list[Call]:
        return [c for c in self.calls if c.method != "GET" and not c.path.endswith("/filter")]

    def mutated_ids(self) -> set[int]:
        ids: set[int] = set()
        for c in self.mutations():
            for m in re.finditer(r"/(?:contacts|conversations)/(\d+)", c.path):
                ids.add(int(m.group(1)))
        return ids

    # --- shapes ----------------------------------------------------------------
    def _conv_json(self, conv: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in conv.items() if k not in {"contact_id", "messages"}}
        out["meta"] = {"sender": dict(self.contacts.get(conv["contact_id"], {}))}
        return out

    # --- transport -------------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911, PLR0912
        url = urlparse(str(request.url))
        prefix = f"/api/v1/accounts/{ACCOUNT}"
        assert url.path.startswith(prefix), url.path
        path = url.path[len(prefix) :]
        query = parse_qs(url.query)
        body = json.loads(request.content) if request.content else None
        method = request.method
        self.calls.append(Call(method, path, body))

        def ok(data: Any, status: int = 200) -> httpx.Response:
            return httpx.Response(status, json=data)

        if method == "GET" and path == f"/inboxes/{INBOX}":
            return ok({"id": INBOX, "channel_type": self.inbox_channel})
        if method == "GET" and path == "/labels":
            return ok(
                {"payload": [{"id": i, "title": t} for i, t in enumerate(sorted(self.labels))]}
            )
        if method == "POST" and path == "/labels":
            self.labels.add(body["title"])
            return ok({"id": 1, "title": body["title"]})
        if method == "GET" and path == "/contacts/search":
            q = (query.get("q") or [""])[0].lower()
            page = int((query.get("page") or ["1"])[0])
            hits = [
                c
                for c in self.contacts.values()
                if q in str(c.get("name") or "").lower() or q in str(c.get("phone_number") or "")
            ]
            return ok({"payload": hits if page == 1 else [], "meta": {"count": len(hits)}})
        if method == "POST" and path == "/contacts":
            phone = body.get("phone_number")
            if phone and any(c.get("phone_number") == phone for c in self.contacts.values()):
                return ok({"message": "Phone number has already been taken"}, 422)
            cid = self.add_contact(body["name"], phone, **(body.get("custom_attributes") or {}))
            return ok(
                {"payload": {"contact": self.contacts[cid], "contact_inbox": {"source_id": "x"}}}
            )
        m = re.fullmatch(r"/contacts/(\d+)", path)
        if m:
            cid = int(m.group(1))
            contact = self.contacts.get(cid)
            if contact is None:
                return ok({"error": "not found"}, 404)
            if method == "GET":
                return ok({"payload": contact})
            if method == "PUT":
                contact["custom_attributes"] = {
                    **contact.get("custom_attributes", {}),
                    **(body.get("custom_attributes") or {}),
                }
                return ok({"payload": contact})
            if method == "DELETE":
                del self.contacts[cid]
                for vid in [v for v, c in self.conversations.items() if c["contact_id"] == cid]:
                    del self.conversations[vid]
                return ok({})
        m = re.fullmatch(r"/contacts/(\d+)/conversations", path)
        if m and method == "GET":
            cid = int(m.group(1))
            convs = [
                self._conv_json(c) for c in self.conversations.values() if c["contact_id"] == cid
            ]
            return ok({"payload": convs})
        if method == "POST" and path == "/conversations/filter":
            page = int((query.get("page") or ["1"])[0])
            rule = body["payload"][0]
            assert rule["attribute_key"] == "labels"
            hits = [
                self._conv_json(c)
                for c in self.conversations.values()
                if set(rule["values"]) & set(c["labels"])
            ]
            return ok({"payload": hits if page == 1 else [], "meta": {"all_count": len(hits)}})
        if method == "POST" and path == "/conversations":
            vid = self.add_conversation(
                body["contact_id"],
                status="pending" if self.bot_on_inbox else body.get("status", "open"),
                **(body.get("custom_attributes") or {}),
            )
            conv = self.conversations[vid]
            conv["inbox_id"] = body["inbox_id"]
            conv["additional_attributes"] = body.get("additional_attributes") or {}
            conv["source_id"] = body["source_id"]
            return ok(self._conv_json(conv))
        m = re.fullmatch(r"/conversations/(\d+)(/[a-z_]+)?", path)
        if m:
            vid = int(m.group(1))
            conv = self.conversations.get(vid)
            if conv is None:
                return ok({"error": "not found"}, 404)
            sub = m.group(2)
            if sub is None and method == "GET":
                return ok(self._conv_json(conv))
            if sub is None and method == "DELETE":
                if self.fail_conversation_delete:
                    return ok({"error": "forbidden"}, 401)
                del self.conversations[vid]
                return ok({})
            if sub == "/labels" and method == "POST":
                conv["labels"] = list(body["labels"])
                return ok({"payload": conv["labels"]})
            if sub == "/toggle_status" and method == "POST":
                conv["status"] = body["status"]
                return ok({"payload": {"current_status": body["status"]}})
            if sub == "/messages" and method == "POST":
                conv["messages"].append(body)
                return ok({"id": self._id(), "content": body["content"]})
        return ok({"error": f"unhandled {method} {path}"}, 404)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        chatwoot_api_url=API,
        chatwoot_account_id=ACCOUNT,
        chatwoot_inbox_id=INBOX,
        chatwoot_api_token="test-token",
        demo_console_enabled=True,
    )


@pytest.fixture
def fake() -> FakeChatwoot:
    return FakeChatwoot()


@pytest.fixture
def crm(settings: Settings, fake: FakeChatwoot) -> DemoCrmClient:
    return DemoCrmClient(settings, transport=httpx.MockTransport(fake.handler))
