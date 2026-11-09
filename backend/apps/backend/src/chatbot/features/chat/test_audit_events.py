"""FIX-17 (RFP 3.2.6 / 4.38 / 2.2.5): audit events beyond case-state changes.

Each event carries who (real user), what kind, which object, redacted
before/after, SLA status and remarks -- and stays append-only.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.authz.db import build_engine, build_session_maker, init_authz_db
from chatbot.features.authz.identity import TokenValidator
from chatbot.features.authz.repository import AuthzRepository
from chatbot.features.authz.router import build_authz_router
from chatbot.features.authz.seed import seed_defaults
from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.audit_events import record_event, redact
from chatbot.features.chat.audit_router import build_audit_router
from chatbot.features.chat.escalation_ladder import LADDER_STEP_ATTR, sweep_ladder
from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.chat.escalation_router import build_escalation_router
from chatbot.features.chat.pic_admin_router import build_pic_admin_router
from chatbot.features.chat.pic_registry import PicEntry
from chatbot.features.chat.ports import AuditEntry
from chatbot.features.chat.pic_store import DealerRecord, ProtonNetRecord
from chatbot.platform.config import Settings, get_settings

_NOW = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)


# --- redaction ----------------------------------------------------------------


def test_redact_masks_secrets_emails_and_phone_numbers():
    out = redact(
        {
            "api_key": "sk-live-123",
            "pic_email": "aisyah.rahman@proton.com",
            "pic_whatsapp": "+60 12-345 6789",
            "cc_emails": ["bob@dealer.my"],
            "response_hours": 4,
            "name": "Hours 24",
        }
    )
    assert out["api_key"] == "[redacted]"
    assert out["pic_email"] == "a***@proton.com"
    assert out["pic_whatsapp"].endswith("789") and "345" not in out["pic_whatsapp"]
    assert out["cc_emails"] == ["b***@dealer.my"]
    assert out["response_hours"] == 4
    assert out["name"] == "Hours 24"


async def test_record_event_never_raises_when_store_is_down():
    class _Broken:
        async def append(self, entry):
            raise RuntimeError("firestore down")

    assert await record_event(_Broken(), event_type="role_change", target="role:x") is False


# --- escalation notify --------------------------------------------------------


class _Sender:
    def send(self, to, cc, subject, body, attachments, *, reply_to=None, in_reply_to=None):
        return None


class _Registry:
    async def lookup(self, dept):
        return PicEntry(pic_name="Aduy", pic_email="pic@test", pic_whatsapp="")


class _DealerStore:
    async def get(self, dealer):
        return DealerRecord(dealer=dealer, emails=["dealer@test"])


class _NSettings:
    escalation_email_enabled = True
    email_escalation_ack_enabled = True
    email_escalation_ack_template = "ack"
    escalation_ack_chat_template = ""
    escalation_cc_pic = False
    escalation_cc_dealer = False
    escalation_reply_to_template = ""
    dealer_email_map_json = ""
    escalation_attachment_budget_bytes = 0
    escalation_failure_note_enabled = False


async def _noop_cw(conv_id, attrs):
    return None


async def test_notifier_records_supplied_actor_and_sla_status():
    audit = InMemoryAuditLog()
    notifier = EscalationNotifier(
        _NSettings(), _Registry(), _Sender(), None, _noop_cw,
        dealer_store=_DealerStore(), audit=audit,
    )
    await notifier.notify_escalation(
        conv_id="42", title="t", body="b", department="sales", dealer="kl",
        customer_email="customer@test", ack_transport="email",
        actor_name="Siti Agent", actor_user_id=7, sla_status="RESPONSE_BREACH",
    )
    rows = await audit.list_for_ticket("42")
    assert len(rows) == 3
    for r in rows:
        assert r.event_type == "escalation_notify"
        assert r.actor_user_id == 7
        assert r.actor_name == "Siti Agent"
        assert r.actor == "Siti Agent"
        assert r.sla_status == "RESPONSE_BREACH"
        assert r.delivery_status == "delivered"
    assert {r.to_state for r in rows} == {
        "ESCALATION_CUSTOMER_ACK", "ESCALATION_PIC", "ESCALATION_DEALER"
    }


async def test_escalation_notify_without_user_keeps_the_service_actor():
    audit = InMemoryAuditLog()
    notifier = EscalationNotifier(
        _NSettings(), _Registry(), _Sender(), None, _noop_cw,
        dealer_store=_DealerStore(), audit=audit,
    )
    await notifier.notify_escalation(
        conv_id="42", title="t", body="b", department="sales", dealer=None,
        customer_email=None, ack_transport="none",
    )
    rows = await audit.list_for_ticket("42")
    assert rows and all(r.actor == "escalation-notifier" for r in rows)
    assert all(r.event_type == "escalation_notify" for r in rows)


def test_notify_endpoint_passes_actor_and_sla_status_through():
    notifier = AsyncMock()

    async def _cw(method, path, payload=None):
        return {"meta": {}}

    app = FastAPI()
    app.include_router(
        build_escalation_router(notifier, _cw, Settings(_env_file=None, proton_backend_key="k"))
    )
    res = TestClient(app).post(
        "/escalation/notify",
        headers={"x-api-key": "k"},
        json={
            "conversation_id": "9", "title": "t", "body": "b",
            "actor_name": "Siti", "actor_user_id": 7, "sla_status": "OK",
        },
    )
    assert res.status_code == 200
    kwargs = notifier.notify_escalation.await_args.kwargs
    assert kwargs["actor_name"] == "Siti"
    assert kwargs["actor_user_id"] == 7
    assert kwargs["sla_status"] == "OK"


# --- ladder -------------------------------------------------------------------


class _LNotifier:
    def send_ladder_step(self, *, conv_id, step, to, cc, title, body, elapsed_working_hours):
        return True, ""

    async def raise_phone_task(self, *, conv_id, step, contacts, deadline):
        return True


class _Store:
    def __init__(self, rec):
        self._rec = rec

    async def get(self, _k):
        return self._rec


async def test_ladder_step_writes_audit():
    audit = InMemoryAuditLog()
    dealer = DealerRecord(
        dealer="kl",
        contacts={"cre": "cre@kl.my", "sales_aftersales_mgr": "sam@kl.my",
                  "principal": "dp@kl.my", "owner": "owner@kl.my"},
        region="central",
    )
    conv = {
        "id": 42, "status": "open", "inbox_id": 4,
        "labels": ["escalate", "dealer_kl", "dept_aftersales"],
        "custom_attributes": {
            "escalation_notified_at": (_NOW - timedelta(hours=5)).isoformat(),
            LADDER_STEP_ATTR: 2,
        },
    }

    async def _set(conv_id, attrs):
        return None

    await sweep_ladder(
        [conv],
        settings=Settings(
            _env_file=None, escalation_policy_enabled=True,
            escalation_policy_dry_run=False, sla_working_hours_enabled=False,
        ),
        notifier=_LNotifier(), dealer_store=_Store(dealer),
        pronet_store=_Store(ProtonNetRecord(region="central", area_regional_mgr="a@p.my", hod="h@p.my")),
        set_attributes=_set, now=_NOW, audit=audit,
    )
    rows = await audit.list_for_ticket("42")
    assert len(rows) == 1
    r = rows[0]
    assert r.event_type == "ladder_step"
    assert r.actor == "escalation-ladder"
    assert r.actor_user_id is None
    assert r.to_state == "LADDER_STEP_3"
    assert r.delivery_status == "delivered"
    assert r.recipients == ["dp@kl.my"]
    assert r.target == "dealer:kl"
    assert "5.0" in r.remark or "working hours" in r.remark


# --- reply link ---------------------------------------------------------------


def test_reply_link_writes_audit():
    audit = InMemoryAuditLog()

    async def _cw(method, path, payload=None):
        return {}

    app = FastAPI()
    app.include_router(
        build_escalation_router(
            AsyncMock(), _cw, Settings(_env_file=None, proton_backend_key="k"), audit=audit
        )
    )
    res = TestClient(app).post(
        "/escalation/acknowledge",
        headers={"x-api-key": "k"},
        json={"conversation_id": "9", "actor": "dealer@kl.my", "remark": "Replied from 0123456789"},
    )
    assert res.json() == {"status": "ok"}
    rows = asyncio.run(audit.list_for_ticket("9"))
    assert len(rows) == 1
    assert rows[0].event_type == "reply_link"
    assert rows[0].actor == "dealer@kl.my"
    assert "0123456789" not in rows[0].remark


# --- admin writers ------------------------------------------------------------


@pytest.fixture
async def authz(tmp_path, respx_mock):
    settings = get_settings().model_copy(update={"rbac_enabled": True})
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/ev.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    await seed_defaults(repo)
    await repo.assign_role(chatwoot_user_id=1, role_id="administrator")
    await repo.create_role("ops", "Ops", "")
    respx_mock.get(f"{settings.chatwoot_api_url}/api/v1/profile").mock(
        return_value=httpx.Response(200, json={"id": 5, "name": "Yuda Admin", "type": "SuperAdmin"})
    )
    return settings, repo, TokenValidator(settings)


_HDR = {
    "x-chatwoot-access-token": "tok",
    "x-chatwoot-client": "c",
    "x-chatwoot-uid": "u",
}


async def test_admin_role_change_writes_before_after(authz):
    settings, repo, validator = authz
    audit = InMemoryAuditLog()
    app = FastAPI()
    app.include_router(build_authz_router(repo, validator, settings, audit=audit))
    client = TestClient(app)

    res = client.post(
        "/authz/roles/ops/permissions", json={"permission_key": "sla.manage"}, headers=_HDR
    )
    assert res.status_code == 200
    res = client.delete("/authz/roles/ops/permissions/sla.manage", headers=_HDR)
    assert res.status_code == 200

    rows = sorted(await audit.list_filtered(event_type="role_change"), key=lambda r: r.at)
    assert len(rows) == 2
    grant, revoke = rows
    assert grant.actor_user_id == 5
    assert grant.actor_name == "Yuda Admin"
    assert grant.target == "role:ops"
    assert grant.before == {"permissions": []}
    assert grant.after == {"permissions": ["sla.manage"]}
    assert revoke.before == {"permissions": ["sla.manage"]}
    assert revoke.after == {"permissions": []}


async def test_role_assignment_is_audited_with_real_user(authz):
    settings, repo, validator = authz
    audit = InMemoryAuditLog()
    app = FastAPI()
    app.include_router(build_authz_router(repo, validator, settings, audit=audit))
    res = TestClient(app).post(
        "/authz/roles/ops/assign", json={"chatwoot_user_id": 9}, headers=_HDR
    )
    assert res.status_code == 200
    (row,) = await audit.list_filtered(event_type="role_change")
    assert row.actor_user_id == 5
    assert row.target == "role:ops"
    assert row.before == {"members": []}
    assert row.after == {"members": [9]}


class _PicStore:
    def __init__(self):
        self.rows: dict[str, Any] = {}

    async def list_all(self):
        return []

    async def get(self, key):
        return self.rows.get(key)

    async def set(self, key, **kw):
        self.rows[key] = {"department": key, **kw}

    async def delete(self, key):
        self.rows.pop(key, None)


class _DStore(_PicStore):
    pass


async def test_pic_edit_audited_with_masked_contacts(authz):
    settings, repo, validator = authz
    audit = InMemoryAuditLog()
    app = FastAPI()
    app.include_router(
        build_pic_admin_router(
            _PicStore(), _DStore(), repo, validator, settings, audit=audit
        )
    )
    res = TestClient(app).put(
        "/admin/escalation/pics/sales",
        json={"pic_name": "Aisyah", "pic_email": "aisyah@proton.com", "pic_whatsapp": "+60123456789"},
        headers=_HDR,
    )
    assert res.status_code == 200
    (row,) = await audit.list_filtered(event_type="escalation_routing_change")
    assert row.actor_user_id == 5
    assert row.target == "pic:sales"
    assert row.after["pic_email"] == "a***@proton.com"
    assert "123456" not in row.after["pic_whatsapp"]
    assert row.before is None or row.before == {}


# --- audit list filters -------------------------------------------------------


async def test_audit_list_filters_by_event_type_and_user(authz):
    settings, repo, validator = authz
    audit = InMemoryAuditLog()
    await record_event(audit, event_type="role_change", target="role:a", actor_user_id=5)
    await record_event(audit, event_type="sla_policy_change", target="sla:default", actor_user_id=6)
    app = FastAPI()
    app.include_router(build_audit_router(audit, repo, validator, settings))
    client = TestClient(app)

    res = client.get("/admin/audit", params={"event_type": "role_change"}, headers=_HDR)
    assert [r["target"] for r in res.json()["audit"]] == ["role:a"]
    res = client.get("/admin/audit", params={"actor_user_id": 6}, headers=_HDR)
    assert [r["target"] for r in res.json()["audit"]] == ["sla:default"]
    assert res.json()["audit"][0]["event_type"] == "sla_policy_change"


def test_audit_trail_has_no_update_or_delete_route():
    app = FastAPI()
    app.include_router(
        build_audit_router(InMemoryAuditLog(), None, None, get_settings())  # type: ignore[arg-type]
    )
    methods = {m for r in app.routes for m in getattr(r, "methods", set())} - {"HEAD", "OPTIONS"}
    assert methods == {"GET"}


async def test_shared_secret_admin_write_is_attributed_to_the_service(tmp_path):
    """RBAC off: the shared key identifies a service, and the row says so."""
    settings = get_settings().model_copy(
        update={"rbac_enabled": False, "faq_admin_api_key": "shared-key"}
    )
    audit = InMemoryAuditLog()
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/off.db")
    await init_authz_db(engine)
    repo = AuthzRepository(build_session_maker(engine))
    app = FastAPI()
    app.include_router(
        build_pic_admin_router(
            _PicStore(), _DStore(), repo, TokenValidator(settings), settings, audit=audit
        )
    )
    res = TestClient(app).put(
        "/admin/escalation/pics/sales",
        json={"pic_name": "A", "pic_email": "a@b.co"},
        headers={"x-api-key": "shared-key"},
    )
    assert res.status_code == 200
    (row,) = await audit.list_filtered(event_type="escalation_routing_change")
    assert row.actor_user_id is None
    assert row.actor == "admin-api-key"


# --- backend-derived actor and SLA status (the agent sends neither) -----------


def _derive_app(audit, messages, *, fail_messages=False):
    async def _cw(method, path, payload=None):
        if path.endswith("/messages"):
            if fail_messages:
                raise RuntimeError("chatwoot down")
            return {"payload": messages}
        return {"meta": {"sender": {"email": "customer@test"}}}

    notifier = EscalationNotifier(
        _NSettings(), _Registry(), _Sender(), None, _noop_cw,
        dealer_store=_DealerStore(), audit=audit,
    )
    app = FastAPI()
    app.include_router(
        build_escalation_router(
            notifier, _cw, Settings(_env_file=None, proton_backend_key="k"), audit=audit
        )
    )
    return TestClient(app)


_NOTIFY = {"conversation_id": "42", "title": "t", "body": "b", "department": "sales", "dealer": "kl"}


async def test_escalation_notify_writes_audit_with_user_and_sla_status():
    """The request carries NO actor and NO SLA status (like the live agent);
    both come from the backend: the SLA engine's latest row for the case and
    Chatwoot's "<name> added escalate" activity."""
    audit = InMemoryAuditLog()
    await audit.append(
        AuditEntry(
            ticket_id="42", session_id="s", actor="sla-engine", from_state="OPEN",
            to_state="SLA_BREACH_NO_RESPONSE", at="2026-11-09T09:00:00+00:00", remark="",
            event_type="sla_alert", sla_status="SLA_BREACH_NO_RESPONSE",
        )
    )
    messages = [
        {"message_type": 2, "content": "Old Timer added dept_sales", "created_at": 1},
        {"message_type": 2, "content": "Siti Agent added escalate, dealer_kl", "created_at": 5},
        {"message_type": 0, "content": "Siti Agent added escalate", "created_at": 9},
    ]
    res = _derive_app(audit, messages).post(
        "/escalation/notify", headers={"x-api-key": "k"}, json=_NOTIFY
    )
    assert res.status_code == 200
    rows = [r for r in await audit.list_for_ticket("42") if r.event_type == "escalation_notify"]
    assert len(rows) == 3
    for r in rows:
        assert r.actor_name == "Siti Agent"
        assert r.actor == "Siti Agent"
        assert r.actor_source == "chatwoot-activity"
        assert r.sla_status == "SLA_BREACH_NO_RESPONSE"


async def test_escalation_notify_sla_status_falls_back_to_within_sla_and_acknowledged():
    audit = InMemoryAuditLog()
    res = _derive_app(audit, []).post("/escalation/notify", headers={"x-api-key": "k"}, json=_NOTIFY)
    assert res.status_code == 200
    rows = [r for r in await audit.list_for_ticket("42") if r.event_type == "escalation_notify"]
    assert {r.sla_status for r in rows} == {"WITHIN_SLA"}

    audit2 = InMemoryAuditLog()
    await audit2.append(
        AuditEntry(
            ticket_id="42", session_id="s", actor="x", from_state="OPEN",
            to_state="ACKNOWLEDGED", at="2026-11-09T09:00:00+00:00", remark="",
        )
    )
    _derive_app(audit2, []).post("/escalation/notify", headers={"x-api-key": "k"}, json=_NOTIFY)
    rows = [r for r in await audit2.list_for_ticket("42") if r.event_type == "escalation_notify"]
    assert {r.sla_status for r in rows} == {"ACKNOWLEDGED"}


async def test_underivable_actor_keeps_service_name_and_says_so():
    audit = InMemoryAuditLog()
    res = _derive_app(audit, [], fail_messages=True).post(
        "/escalation/notify", headers={"x-api-key": "k"}, json=_NOTIFY
    )
    assert res.status_code == 200
    rows = [r for r in await audit.list_for_ticket("42") if r.event_type == "escalation_notify"]
    assert rows and all(r.actor == "escalation-notifier" for r in rows)
    assert all(r.actor_source == "agent-service" for r in rows)


# --- masking is structural, not a digit-run grab -----------------------------


def test_remarks_keep_timestamps_and_case_numbers_but_mask_phones():
    from chatbot.features.chat.audit_events import mask_text

    assert mask_text("2026-11-09T10:00:00+00:00") == "2026-11-09T10:00:00+00:00"
    assert mask_text("case 12345678 at 2026-11-09") == "case 12345678 at 2026-11-09"
    assert mask_text("+60 12-345 6789").endswith("789")
    assert "345" not in mask_text("+60 12-345 6789")
    assert mask_text("012-3456789") == "*******789"


def test_phone_valued_keys_are_masked_whatever_their_shape():
    assert redact({"pic_whatsapp": "123456789"})["pic_whatsapp"] == "******789"


async def test_ladder_phone_task_masks_phone_recipients_but_keeps_emails():
    audit = InMemoryAuditLog()
    dealer = DealerRecord(
        dealer="kl", contacts={"principal": "+60123456789", "owner": "owner@kl.my"}, region="central"
    )
    conv = {
        "id": 42, "status": "open", "inbox_id": 4,
        "labels": ["escalate", "dealer_kl", "dept_aftersales"],
        "custom_attributes": {
            "escalation_notified_at": (_NOW - timedelta(hours=60)).isoformat(),
            LADDER_STEP_ATTR: 4,
        },
    }

    async def _set(conv_id, attrs):
        return None

    await sweep_ladder(
        [conv],
        settings=Settings(
            _env_file=None, escalation_policy_enabled=True,
            escalation_policy_dry_run=False, sla_working_hours_enabled=False,
        ),
        notifier=_LNotifier(), dealer_store=_Store(dealer),
        pronet_store=_Store(ProtonNetRecord(region="central", area_regional_mgr="", hod="")),
        set_attributes=_set, now=_NOW, audit=audit,
    )
    rows = await audit.list_for_ticket("42")
    recipients = [x for r in rows for x in (r.recipients or [])]
    assert "********789" in recipients or not recipients
    assert not any("60123456789" in x for x in recipients)


# --- guarded reads and acknowledge failure -----------------------------------


async def test_role_write_survives_a_failing_audit_snapshot(authz):
    settings, repo, validator = authz
    app = FastAPI()
    app.include_router(build_authz_router(repo, validator, settings, audit=InMemoryAuditLog()))

    async def _boom(*a, **k):
        raise RuntimeError("db hiccup")

    calls = {"n": 0}
    real = repo.role_permissions

    async def _flaky(role_id):
        calls["n"] += 1
        if calls["n"] == 1:  # the audit `before` read
            raise RuntimeError("db hiccup")
        return await real(role_id)

    repo.role_permissions = _flaky
    res = TestClient(app).post(
        "/authz/roles/ops/permissions", json={"permission_key": "sla.manage"}, headers=_HDR
    )
    assert res.status_code == 200


def test_acknowledge_returns_503_when_the_row_cannot_be_stored():
    class _Broken:
        async def append(self, entry):
            raise RuntimeError("firestore down")

        async def list_for_ticket(self, ticket_id):
            return []

    async def _cw(method, path, payload=None):
        return {}

    app = FastAPI()
    app.include_router(
        build_escalation_router(
            AsyncMock(), _cw, Settings(_env_file=None, proton_backend_key="k"), audit=_Broken()
        )
    )
    res = TestClient(app).post(
        "/escalation/acknowledge", headers={"x-api-key": "k"}, json={"conversation_id": "9"}
    )
    assert res.status_code == 503


def test_mask_recipient_keeps_emails_and_masks_phone_numbers():
    from chatbot.features.chat.audit_events import mask_recipient

    assert mask_recipient("dp@kl.my") == "dp@kl.my"
    assert mask_recipient("+60123456789") == "********789"
    assert mask_recipient("the dealer principal") == "the dealer principal"


def test_automation_system_activity_is_marked_as_automation():
    import asyncio

    from chatbot.features.chat.escalation_context import derive_context

    async def _cw(method, path, payload=None):
        return {"payload": [
            {"message_type": 2, "content": "Automation System added escalate", "created_at": 5}
        ]}

    actor, sla, source = asyncio.run(
        derive_context(None, _cw, "42", need_actor=True, need_sla=False)
    )
    assert (actor, sla, source) == ("Automation System", None, "chatwoot-automation")


async def test_slow_chatwoot_lookup_does_not_hold_up_the_send(monkeypatch):
    import time

    from chatbot.features.chat import escalation_context

    monkeypatch.setattr(escalation_context, "DERIVE_TIMEOUT_SECONDS", 0.2)
    audit = InMemoryAuditLog()
    sent: list[float] = []

    class _TimedSender:
        def send(self, to, cc, subject, body, attachments, *, reply_to=None, in_reply_to=None):
            sent.append(time.monotonic())

    async def _cw(method, path, payload=None):
        if path.endswith("/messages"):
            await asyncio.sleep(30)  # a hung Chatwoot
        return {"meta": {"sender": {"email": "customer@test"}}}

    notifier = EscalationNotifier(
        _NSettings(), _Registry(), _TimedSender(), None, _noop_cw,
        dealer_store=_DealerStore(), audit=audit,
    )
    app = FastAPI()
    app.include_router(
        build_escalation_router(
            notifier, _cw, Settings(_env_file=None, proton_backend_key="k"), audit=audit
        )
    )
    start = time.monotonic()
    res = await asyncio.to_thread(
        TestClient(app).post, "/escalation/notify", headers={"x-api-key": "k"}, json=_NOTIFY
    )
    elapsed = time.monotonic() - start
    assert res.status_code == 200
    assert elapsed < 3
    assert sent and sent[0] - start < 3
    rows = [r for r in await audit.list_for_ticket("42") if r.event_type == "escalation_notify"]
    assert rows
    assert all(r.actor_source == "agent-service" for r in rows)
    assert all(r.actor == "escalation-notifier" for r in rows)
    assert all(r.sla_status == "WITHIN_SLA" for r in rows)  # the SLA half finished in budget


def _status_payload(sender):
    from chatbot.features.chat.schemas import ChatwootWebhookPayload

    return ChatwootWebhookPayload.model_validate(
        {"event": "conversation_status_changed", "id": 42,
         "conversation": {"id": 42, "status": "resolved"}, "sender": sender}
    )


async def _status_row(sender):
    from chatbot.features.chat.router import ChatRouter

    audit = InMemoryAuditLog()
    stub = type("S", (), {"_audit_log": audit, "orchestrator": type("O", (), {"_ticketing_port": None})()})()
    await ChatRouter._log_status_transition(stub, "42", _status_payload(sender))
    (row,) = await audit.list_for_ticket("42")
    return row


async def test_status_change_by_an_agent_records_their_user_id():
    row = await _status_row({"id": 7, "name": "Agent Sam", "type": "user"})
    assert (row.actor, row.actor_user_id) == ("Agent Sam", 7)


async def test_status_change_by_a_contact_is_not_recorded_as_an_agent():
    row = await _status_row({"id": 7, "name": "Real Customer", "type": "contact"})
    assert row.actor == "customer"
    assert row.actor_user_id is None
    assert row.actor_name == "customer"
