from __future__ import annotations

from typing import Any

import pytest

from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.chat.models import HandoffOpenPayload
from chatbot.platform.config import Settings


class _FakeClient:
    """Records requests and returns canned responses keyed by (method, path substring)."""

    def __init__(self, responses: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._responses = responses

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        self.calls.append((method, path, payload))
        for (m, sub), resp in self._responses.items():
            if m == method and sub in path:
                return resp
        return {}


def _adapter(fake: _FakeClient) -> ChatwootAdapter:
    a = ChatwootAdapter(
        Settings(chatwoot_account_id=1, chatwoot_inbox_id=7, chatwoot_agent_team_id=3)
    )
    a._request = fake._request  # type: ignore[method-assign]
    return a


@pytest.mark.asyncio
async def test_send_message_posts_outgoing() -> None:
    fake = _FakeClient({})
    await _adapter(fake).send_message(conversation_id="42", text="hi")
    method, path, payload = fake.calls[-1]
    assert method == "POST"
    assert "/conversations/42/messages" in path
    assert payload == {"content": "hi", "message_type": "outgoing"}


@pytest.mark.asyncio
async def test_create_ticket_creates_prioritizes_labels_assigns() -> None:
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    ticket_id = await _adapter(fake).create_ticket(
        session_id="whatsapp-+60123", title="Refund", body="help", urgency="high"
    )
    assert ticket_id == "99"
    paths = [p for _, p, _ in fake.calls]
    assert any(p.endswith("/conversations") for p in paths)  # create POST
    assert any("toggle_priority" in p for p in paths)
    assert any("assignments" in p for p in paths)
    assert any("labels" in p for p in paths)


@pytest.mark.asyncio
async def test_create_ticket_creates_contact_before_conversation() -> None:
    # Chatwoot's Application API POST /conversations needs a contact_inbox: without
    # a contact_id the ConversationBuilder errors. So we must create/find a contact
    # first and pass its id (source_id stays == session_id so the mapping holds).
    fake = _FakeClient(
        {
            ("POST", "/contacts"): {"payload": {"contact": {"id": 55}}},
            ("POST", "/conversations"): {"id": 99},
        }
    )
    adapter = _adapter(fake)
    ticket_id = await adapter.create_ticket(
        session_id="whatsapp-+60123",
        title="Refund",
        body="help",
        urgency="high",
        customer_name="Aina",
        customer_phone="+60123",
    )
    assert ticket_id == "99"

    methods_paths = [(m, p) for m, p, _ in fake.calls]
    contact_idx = next(
        i for i, (m, p) in enumerate(methods_paths) if m == "POST" and p.endswith("/contacts")
    )
    conv_idx = next(
        i for i, (m, p) in enumerate(methods_paths) if m == "POST" and p.endswith("/conversations")
    )
    assert contact_idx < conv_idx, "contact must be created before the conversation"

    conv_payload = next(
        pl for m, p, pl in fake.calls if m == "POST" and p.endswith("/conversations")
    )
    assert conv_payload is not None
    assert conv_payload["contact_id"] == 55
    assert conv_payload["source_id"] == "whatsapp-+60123"
    assert "contact" not in conv_payload  # the old undocumented inline key is gone


@pytest.mark.asyncio
async def test_create_ticket_falls_back_to_contact_search_on_duplicate() -> None:
    # A repeat escalation for the same identifier 422s on create (Chatwoot enforces
    # identifier uniqueness); the adapter recovers by searching for the contact.
    fake = _FakeClient(
        {
            # POST /contacts returns {} (simulated 422 -> _request None-ish path)
            ("GET", "/contacts/search"): {"payload": [{"id": 77, "identifier": "whatsapp-+60123"}]},
            ("POST", "/conversations"): {"id": 42},
        }
    )
    adapter = _adapter(fake)
    ticket_id = await adapter.create_ticket(
        session_id="whatsapp-+60123", title="Refund", body="help", urgency="high"
    )
    assert ticket_id == "42"
    assert any("/contacts/search" in p for _m, p, _pl in fake.calls)
    conv_payload = next(
        pl for m, p, pl in fake.calls if m == "POST" and p.endswith("/conversations")
    )
    assert conv_payload is not None
    assert conv_payload["contact_id"] == 77


@pytest.mark.asyncio
async def test_reuses_existing_active_conversation_after_cache_loss() -> None:
    # After a restart the in-memory cache is empty. If the contact still has an
    # OPEN conversation we must reuse it, not create a duplicate.
    fake = _FakeClient(
        {
            ("POST", "/contacts"): {},  # duplicate identifier -> no id
            ("GET", "/contacts/search"): {"payload": [{"id": 55, "identifier": "whatsapp-+60123"}]},
            ("GET", "/contacts/55/conversations"): {
                "payload": [{"id": 99, "status": "open", "inbox_id": 7}]
            },
        }
    )
    adapter = _adapter(fake)
    conv_id = await adapter._find_or_create_conversation("whatsapp-+60123")
    assert conv_id == "99"
    assert not any(m == "POST" and p.endswith("/conversations") for m, p, _ in fake.calls), (
        "must not create a duplicate conversation"
    )


@pytest.mark.asyncio
async def test_starts_new_conversation_when_prior_is_resolved() -> None:
    # A prior RESOLVED conversation is a closed ticket — the next contact opens a
    # fresh conversation rather than re-escalating into the resolved thread.
    fake = _FakeClient(
        {
            ("POST", "/contacts"): {},
            ("GET", "/contacts/search"): {"payload": [{"id": 55, "identifier": "whatsapp-+60123"}]},
            ("GET", "/contacts/55/conversations"): {
                "payload": [{"id": 99, "status": "resolved", "inbox_id": 7}]
            },
            ("POST", "/conversations"): {"id": 123},
        }
    )
    adapter = _adapter(fake)
    conv_id = await adapter._find_or_create_conversation("whatsapp-+60123")
    assert conv_id == "123"
    assert any(m == "POST" and p.endswith("/conversations") for m, p, _ in fake.calls)


@pytest.mark.asyncio
async def test_contact_search_fallback_url_encodes_session_id() -> None:
    # WhatsApp session ids contain '+' (e.g. whatsapp-+60123); a raw '+' in a
    # query string decodes to a space server-side and breaks the recovery search.
    fake = _FakeClient({("GET", "/contacts/search"): {"payload": []}})
    adapter = _adapter(fake)
    await adapter._find_or_create_contact("whatsapp-+60123")
    search_paths = [p for m, p, _ in fake.calls if m == "GET" and "/contacts/search" in p]
    assert search_paths, "expected a /contacts/search fallback call"
    assert "whatsapp-%2B60123" in search_paths[0]
    assert "+60123" not in search_paths[0]


@pytest.mark.asyncio
async def test_create_ticket_complaint_urgency_adds_ticketing_label() -> None:
    # High urgency marks the case a complaint, so the complaint label rides on
    # top of the Chatwoot-only escalation marker.
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    adapter = ChatwootAdapter(
        Settings(
            chatwoot_account_id=1,
            chatwoot_inbox_id=7,
            chatwoot_escalation_label="ai-escalation",
            chatwoot_complaint_label="escalate",
        )
    )
    adapter._request = fake._request  # type: ignore[method-assign]
    await adapter.create_ticket(
        session_id="whatsapp-+60123", title="Refund", body="help", urgency="high"
    )
    # Whole-branch review fix (Important 9): the single labels write is now
    # preceded by a GET of the current set (merge-safe union). Filter on the
    # POST -- the "exactly one labels POST" batching invariant is unchanged.
    labels_payload = next(pl for m, p, pl in fake.calls if p.endswith("/labels") and m == "POST")
    assert labels_payload == {"labels": ["ai-escalation", "escalate"]}


@pytest.mark.asyncio
async def test_create_ticket_writes_dimension_labels_in_single_final_call() -> None:
    # division/department/sla still land as labels using the SAME convention the
    # metrics mapping parses (division_/dept_/sla_), and must ride in the ONE
    # final labels call alongside the escalation labels — a second labels POST
    # would needlessly re-fire the webhook. category/subcategory have moved to
    # custom attributes (see the test below).
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    adapter = ChatwootAdapter(
        Settings(
            chatwoot_account_id=1,
            chatwoot_inbox_id=7,
            chatwoot_escalation_label="ai-escalation",
            chatwoot_complaint_label="escalate",
        )
    )
    adapter._request = fake._request  # type: ignore[method-assign]
    await adapter.create_ticket(
        session_id="whatsapp-+60123",
        title="Battery fault",
        body="help",
        urgency="high",
        category="Aftersales",
        subcategory="Battery Health",
        division="Aftersales",
        department="Service Center",
        sla_minutes=480,
    )
    labels_calls = [pl for m, p, pl in fake.calls if p.endswith("/labels") and m == "POST"]
    assert len(labels_calls) == 1, "exactly one labels call (no duplicate-ticket trigger)"
    labels = labels_calls[0]["labels"]  # type: ignore[index]
    assert labels == [
        "division_aftersales",
        "dept_service_center",
        "sla_480",
        "ai-escalation",
        "escalate",
    ]
    assert not any(lbl.startswith("category_") for lbl in labels)
    assert not any(lbl.startswith("subcat_") for lbl in labels)
    # category/subcategory/sla_minutes persisted as custom attributes, in the
    # SAME single custom_attributes call.
    custom_attrs_calls = [pl for _m, p, pl in fake.calls if p.endswith("/custom_attributes")]
    assert len(custom_attrs_calls) == 1, "ONE custom_attributes call, not two"
    ca = custom_attrs_calls[0]
    assert ca == {
        "custom_attributes": {
            "sla_minutes": 480,
            "case_category": "Aftersales",
            "case_subcategory": "Battery Health",
        }
    }


@pytest.mark.asyncio
async def test_create_ticket_writes_case_category_as_custom_attribute() -> None:
    # case_category/case_subcategory are Chatwoot custom attribute definitions
    # (List-type, single-select) — they must be written via custom_attributes,
    # not labels, and merged into the SAME call that already writes sla_minutes.
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    adapter = _adapter(fake)
    await adapter.create_ticket(
        session_id="s1",
        title="t",
        body="b",
        urgency="high",
        category="sales",
        subcategory="Test Drive Booking",
        division="Sales",
        department="dept_sales",
        sla_minutes=60,
    )

    custom_attrs_calls = [pl for _m, p, pl in fake.calls if p.endswith("/custom_attributes")]
    assert len(custom_attrs_calls) == 1  # ONE call, not two — merged with sla_minutes
    body = custom_attrs_calls[0]
    assert body is not None
    assert body["custom_attributes"]["case_category"] == "sales"
    assert body["custom_attributes"]["case_subcategory"] == "Test Drive Booking"
    assert body["custom_attributes"]["sla_minutes"] == 60

    labels_calls = [pl for m, p, pl in fake.calls if p.endswith("/labels") and m == "POST"]
    labels = labels_calls[0]["labels"]  # type: ignore[index]
    assert not any(lbl.startswith("category_") for lbl in labels)
    assert not any(lbl.startswith("subcat_") for lbl in labels)
    assert any(lbl.startswith("division_") for lbl in labels)  # unaffected


@pytest.mark.asyncio
async def test_create_ticket_writes_case_type_and_vehicle_model_as_custom_attributes() -> None:
    # case_type/vehicle_model are additional custom attributes (analogous to
    # case_category/case_subcategory) written in the SAME custom_attributes call.
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    adapter = _adapter(fake)
    await adapter.create_ticket(
        session_id="s1",
        title="t",
        body="b",
        urgency="high",
        category="sales",
        subcategory="Test Drive Booking",
        division="Sales",
        department="dept_sales",
        sla_minutes=60,
        case_type="Inquiry",
        vehicle_model="e.MAS 7",
    )

    custom_attrs_calls = [pl for _m, p, pl in fake.calls if p.endswith("/custom_attributes")]
    assert len(custom_attrs_calls) == 1  # ONE call, merged with the rest
    body = custom_attrs_calls[0]
    assert body is not None
    assert body["custom_attributes"]["case_category"] == "sales"
    assert body["custom_attributes"]["case_subcategory"] == "Test Drive Booking"
    assert body["custom_attributes"]["case_type"] == "Inquiry"
    assert body["custom_attributes"]["vehicle_model"] == "e.MAS 7"
    assert body["custom_attributes"]["sla_minutes"] == 60


@pytest.mark.asyncio
async def test_create_ticket_custom_attributes_merge_not_clobber_on_reused_conversation() -> None:
    """Critical fix (Package C Task 5 review): _find_or_create_conversation
    can REUSE an existing active conversation (e.g. a customer
    re-escalating on the same session, search_existing defaults True) --
    a plain custom_attributes assign there would blank whatever a prior
    escalation already wrote, exactly the bug caught in set_call_recording/
    set_ticket_classification/set_ticket_external_id."""
    fake = _FakeClient(
        {("GET", "/conversations/99"): {"custom_attributes": {"external_id": "whatsapp-+60123"}}}
    )
    adapter = _adapter(fake)
    adapter._conv_by_session["s1"] = "99"  # simulate an existing, cached conversation
    await adapter.create_ticket(
        session_id="s1", title="t", body="b", urgency="high", case_type="Inquiry"
    )
    custom_attrs_calls = [pl for _m, p, pl in fake.calls if p.endswith("/custom_attributes")]
    assert len(custom_attrs_calls) == 1
    body = custom_attrs_calls[0]
    assert body is not None
    assert body["custom_attributes"]["external_id"] == "whatsapp-+60123"  # survived the write
    assert body["custom_attributes"]["case_type"] == "Inquiry"  # new value still written


@pytest.mark.asyncio
async def test_create_ticket_non_complaint_stays_chatwoot_only() -> None:
    # Medium urgency (non-complaint) -> only the Chatwoot escalation marker; the
    # complaint label is NOT applied.
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    adapter = ChatwootAdapter(
        Settings(
            chatwoot_account_id=1,
            chatwoot_inbox_id=7,
            chatwoot_escalation_label="ai-escalation",
            chatwoot_complaint_label="escalate",
        )
    )
    adapter._request = fake._request  # type: ignore[method-assign]
    await adapter.create_ticket(
        session_id="whatsapp-+60123", title="Refund", body="help", urgency="medium"
    )
    labels_calls = [pl for m, p, pl in fake.calls if p.endswith("/labels") and m == "POST"]
    assert len(labels_calls) == 1
    assert labels_calls[0]["labels"] == ["ai-escalation"]  # type: ignore[index]
    assert not any(p.endswith("/custom_attributes") for _m, p, _pl in fake.calls)


@pytest.mark.asyncio
async def test_create_ticket_does_not_cache_on_failed_create() -> None:
    # /conversations returns no id (e.g. network/auth failure) -> fallback used,
    # nothing cached so a later attempt can retry.
    fake = _FakeClient({})  # every request returns {} (no "id")
    adapter = _adapter(fake)
    ticket_id = await adapter.create_ticket(
        session_id="whatsapp-+60123", title="Refund", body="help", urgency="high"
    )
    assert ticket_id == "whatsapp-+60123"
    assert adapter._conv_by_session == {}


@pytest.mark.asyncio
async def test_unpause_evicts_conversation_cache() -> None:
    # Resolving (unpause) closes the ticket. The cached conversation is now
    # resolved, so it must be forgotten — the next contact opens a fresh one
    # rather than re-escalating into a resolved (queue-invisible) thread.
    fake = _FakeClient({})
    adapter = _adapter(fake)
    adapter._conv_by_session["whatsapp-+60123"] = "99"
    await adapter.unpause_ai_for_session("whatsapp-+60123")
    assert "whatsapp-+60123" not in adapter._conv_by_session


@pytest.mark.asyncio
async def test_request_sends_dash_and_underscore_token_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Some reverse proxies strip request headers with underscores, so the token
    # must ALSO be sent as `Api-Access-Token` (dashes) — Rails maps both forms to
    # the same value, so the dash header survives the proxy.
    captured: dict[str, Any] = {}

    class _FakeResp:
        content = b'{"ok": true}'

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"ok": True}

    class _FakeClient:
        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_: Any) -> bool:
            return False

        async def request(self, method: str, url: str, **kwargs: Any) -> _FakeResp:
            captured["headers"] = kwargs.get("headers")
            return _FakeResp()

    monkeypatch.setattr(
        "chatbot.features.chat.adapters.chatwoot.httpx.AsyncClient",
        lambda *_a, **_k: _FakeClient(),
    )
    adapter = ChatwootAdapter(Settings(chatwoot_api_token="tok123", chatwoot_enabled=True))
    await adapter._request("GET", "/profile")
    assert captured["headers"]["Api-Access-Token"] == "tok123"
    assert captured["headers"]["api_access_token"] == "tok123"


@pytest.mark.asyncio
async def test_contact_created_with_synthesized_email() -> None:
    # Downstream systems key customers by email; web/WhatsApp contacts have none,
    # so we synthesize a deterministic, format-valid one.
    fake = _FakeClient(
        {
            ("POST", "/contacts"): {"payload": {"contact": {"id": 55}}},
            ("POST", "/conversations"): {"id": 99},
        }
    )
    adapter = _adapter(fake)
    await adapter._find_or_create_conversation("whatsapp-+60123")
    contact_payload = next(pl for m, p, pl in fake.calls if m == "POST" and p.endswith("/contacts"))
    assert contact_payload is not None
    email = contact_payload["email"]
    assert email.endswith("@" + adapter._settings.chatwoot_customer_email_domain)
    assert "+" not in email  # local part sanitized


@pytest.mark.asyncio
async def test_create_ticket_posts_incoming_message_before_labels() -> None:
    # The escalation must leave an INCOMING customer message on the conversation
    # (so a downstream sync can identify the customer), posted before the labels
    # that fire the webhook.
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    await _adapter(fake).create_ticket(
        session_id="s1", title="Broken clasp", body="details", urgency="high"
    )
    calls = fake.calls
    inc = [
        i
        for i, (_m, p, pl) in enumerate(calls)
        if p.endswith("/messages") and (pl or {}).get("message_type") == "incoming"
    ]
    lbl = [i for i, (m, p, _pl) in enumerate(calls) if p.endswith("/labels") and m == "POST"]
    assert inc, "expected an incoming customer message"
    assert inc[0] < lbl[0], "incoming message must precede the label (webhook trigger)"
    inc_payload = calls[inc[0]][2]
    assert inc_payload is not None and inc_payload["content"] == "Broken clasp"


@pytest.mark.asyncio
async def test_add_private_note_sets_private_true() -> None:
    fake = _FakeClient({})
    await _adapter(fake).add_private_note(ticket_id="99", text="internal")
    method, path, payload = fake.calls[-1]
    assert method == "POST"
    assert "/conversations/99/messages" in path
    assert payload == {"content": "internal", "message_type": "outgoing", "private": True}


class _StatefulLabelsClient:
    """Chatwoot's real labels persistence: a GET reflects the last POST.

    A stateless fake structurally cannot catch a bug that only appears
    across a call sequence -- the Task 5 round-3 lesson, applied again here.
    """

    def __init__(self, seeded: list[str], conv_id: int = 99) -> None:
        self.labels: list[str] = list(seeded)
        self._conv_id = conv_id

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if path.endswith("/labels"):
            if method == "GET":
                return {"payload": list(self.labels)}
            if method == "POST" and payload is not None:
                self.labels = [str(v) for v in payload.get("labels", [])]
                return {}
        if method == "POST" and path.endswith("/conversations"):
            return {"id": self._conv_id}
        return {}


def _reusing_adapter(fake: _StatefulLabelsClient, session_id: str) -> ChatwootAdapter:
    a = ChatwootAdapter(
        Settings(
            chatwoot_account_id=1,
            chatwoot_inbox_id=7,
            chatwoot_escalation_label="ai-escalation",
            chatwoot_complaint_label="escalate",
        )
    )
    a._request = fake._request  # type: ignore[method-assign]
    # Pre-seed the session->conversation cache so _find_or_create_conversation
    # REUSES conversation 99 rather than creating a fresh one -- a fresh
    # conversation has no labels to lose, which is exactly why the earlier
    # merge-safety test in this package did not discriminate.
    a._conv_by_session[session_id] = "99"
    return a


@pytest.mark.asyncio
async def test_create_ticket_does_not_wipe_labels_on_a_reused_conversation() -> None:
    """Whole-branch review fix (Important 9): `create_ticket` POSTed the
    whole labels array, so a SECOND escalation on a session whose
    conversation is reused deleted every label already on it -- csat_*,
    nps_*, a prior division_*/dealer_*. Fifth instance of this footgun."""
    fake = _StatefulLabelsClient(seeded=["csat_5", "division_sales", "dealer_kl_pj"])
    adapter = _reusing_adapter(fake, "whatsapp-+60123")
    await adapter.create_ticket(
        session_id="whatsapp-+60123", title="Refund", body="help", urgency="high"
    )
    assert {"csat_5", "division_sales", "dealer_kl_pj"} <= set(fake.labels)
    assert {"ai-escalation", "escalate"} <= set(fake.labels)


@pytest.mark.asyncio
async def test_open_handoff_does_not_wipe_labels_on_a_reused_conversation() -> None:
    """Same defect, same fix, on the other bare-assign writer."""
    fake = _StatefulLabelsClient(seeded=["nps_9", "division_sales"])
    adapter = _reusing_adapter(fake, "whatsapp-+60123")
    await adapter.open_handoff(
        HandoffOpenPayload(
            session_id="whatsapp-+60123",
            customer_name="Customer",
            customer_email="c@example.test",
            ai_summary="double charge",
            transcript=(),
            urgency="high",
            reason="complaint",
        )
    )
    assert {"nps_9", "division_sales"} <= set(fake.labels)
    assert "ai-escalation" in fake.labels


@pytest.mark.asyncio
async def test_escalation_labels_still_land_when_the_labels_read_fails() -> None:
    """Deliberately UNLIKE add_ticket_tag's read-failure rule: these labels
    carry the escalation trigger, so skipping the write means the handoff
    never fires and the customer waits forever -- strictly worse than the
    label loss it would avoid. Falls back to today's exact behaviour."""

    class _ReadFails(_StatefulLabelsClient):
        async def _request(
            self, method: str, path: str, payload: dict[str, Any] | None = None
        ) -> dict[str, Any] | None:
            if method == "GET" and path.endswith("/labels"):
                return None
            return await super()._request(method, path, payload)

    fake = _ReadFails(seeded=[])
    adapter = _reusing_adapter(fake, "whatsapp-+60123")
    await adapter.create_ticket(
        session_id="whatsapp-+60123", title="Refund", body="help", urgency="high"
    )
    assert {"ai-escalation", "escalate"} <= set(fake.labels)
