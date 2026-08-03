# Channel Follow-ups: IVR-4, EM-7, Category Hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship three independent, flag-gated features closing the last open
items from the 2026-07-28 client demo gap list: a per-turn language reminder
for the Gemini Live voice pipeline (IVR-4), two-thread email escalation
(EM-7), and a cascading category→subcategory picker in the conversation
sidebar.

**Architecture:** Track A (EM-7) adds a new backend HTTP endpoint reached
from the `agent/` service's existing label-webhook path — no change to the
AI's own escalation flow. Track B (IVR-4) adds a text-hint injection into an
already-open Gemini Live session, gated by a new flag, tested against a
mocked SDK. Track C is a single Chatwoot fork patch, pure frontend, no
backend change.

**Tech Stack:** Python/FastAPI (`backend/`), Python/FastAPI (`agent/`),
pytest + respx + `unittest.mock`, Vue 3 (Chatwoot fork patch), `google-genai`
Live SDK.

## Global Constraints

- Every new capability defaults OFF and is byte-identical to today's
  behavior when its flag/config is unset — this is a hard project-wide rule
  (see `CLAUDE.md`).
- Background-task/webhook code (`agent/`) never raises for "nothing to do"
  cases — log and return, per `CLAUDE.md`'s background-task invariant.
- New env vars must be added to both the consuming `config.py` and the
  relevant `example.env` (`deploy/tenants/example.env` for agent-visible
  vars, `backend/apps/backend/.env.example` for backend-only vars).
- Run the full relevant test suite before each commit: `cd agent && pytest`
  for Track A/agent-side changes, `cd backend/apps/backend && uv run pytest
  src/` for Track A/backend-side and Track B changes.
- Commit after each task (not each step) — one commit per task, following
  `git commit -m "<type>(<scope>): <description>"`.

---

## Track A: EM-7 — two-thread email escalation

### Task 1: Backend — EscalationNotifier gains dealer-forward + customer-ack

**Files:**
- Modify: `backend/apps/backend/src/chatbot/platform/config.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/escalation_notifier.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_escalation_notifier.py`

**Interfaces:**
- Produces: `EscalationNotifier.notify_email_channel_escalation(*, conv_id: str, title: str, body: str, department: str | None, dealer: str | None, customer_email: str | None) -> None` — new method, independent of the existing `notify()` (unchanged, still used by the AI-driven path).
- Produces: `build_dealer_email_map(settings: Settings) -> dict[str, str]` — new module-level function in `escalation_notifier.py`.
- Consumes: existing `Settings`, `PicRegistry`, `SmtpEmailSender` — no changes to their shape.

- [ ] **Step 1: Add the new settings fields**

In `backend/apps/backend/src/chatbot/platform/config.py`, find the existing
block with `escalation_email_enabled`, `escalation_cc_pic` (around line
404-409) and add immediately after it:

```python
    # EM-7: two-thread email escalation for natively-escalated Email-channel
    # conversations (agent applies the `escalate` label). Independent of the
    # AI-driven escalation_email_enabled/escalation_cc_pic pair above, which
    # covers a different trigger (the AI's own autonomous handoff decision).
    email_escalation_ack_enabled: bool = False
    email_escalation_ack_template: str = (
        "Your case has been escalated to a specialist team who will follow up shortly."
    )
    # dealer slug -> email, e.g. {"kl_pj": "kl-pj-service@dealer.example"}.
    # Empty (default) means no dealer email is ever sent.
    dealer_email_map_json: str = ""
```

- [ ] **Step 2: Write the failing test for `build_dealer_email_map`**

Add to the top of `test_escalation_notifier.py` (after the existing
imports):

```python
from chatbot.features.chat.escalation_notifier import build_dealer_email_map


def test_build_dealer_email_map_parses_json() -> None:
    settings = _settings(
        dealer_email_map_json='{"kl_pj": "kl-pj@dealer.example", "JB": "jb@dealer.example"}'
    )
    result = build_dealer_email_map(settings)
    assert result == {"kl_pj": "kl-pj@dealer.example", "jb": "jb@dealer.example"}


def test_build_dealer_email_map_empty_on_blank_or_bad_json() -> None:
    assert build_dealer_email_map(_settings(dealer_email_map_json="")) == {}
    assert build_dealer_email_map(_settings(dealer_email_map_json="not json")) == {}
    assert build_dealer_email_map(_settings(dealer_email_map_json='["a", "b"]')) == {}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_escalation_notifier.py -k dealer_email_map -v`
Expected: FAIL with `ImportError: cannot import name 'build_dealer_email_map'`

- [ ] **Step 4: Implement `build_dealer_email_map`**

In `escalation_notifier.py`, add near the top (after the `_CWRequest` type
alias, before the `EscalationNotifier` class):

```python
def build_dealer_email_map(settings: Settings) -> dict[str, str]:
    """Parse dealer_email_map_json into a lower-cased slug -> email dict.

    Returns {} on absent/blank/malformed JSON or a non-dict/non-string-keyed
    shape -- mirrors build_pic_registry's fail-safe parsing so a misconfigured
    map never crashes the app, it just means no dealer email is ever sent.
    """
    raw = (settings.dealer_email_map_json or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, str] = {}
    for key, val in data.items():
        if isinstance(key, str) and isinstance(val, str) and val:
            result[key.lower()] = val
    return result
```

Add `import json` to the top of `escalation_notifier.py` if not already
present (it is not — check the current imports; only `textwrap` is
imported).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_escalation_notifier.py -k dealer_email_map -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Write the failing tests for `notify_email_channel_escalation`**

Add to `test_escalation_notifier.py`:

```python
def _notifier(
    *,
    pic: PicEntry | None = _APPS_PIC,
    dealer_map: dict[str, str] | None = None,
    email_sender=None,
    settings_kw: dict[str, Any] | None = None,
) -> tuple[EscalationNotifier, list[dict[str, Any]]]:
    sent_emails: list[dict[str, Any]] = []

    class _FakeEmailSender:
        def send(self, to, cc, subject, body, attachments) -> None:
            sent_emails.append({"to": to, "cc": cc, "subject": subject, "body": body})

    async def _fake_cw(method: str, path: str, payload: Any = None) -> dict:
        return {}

    notifier = EscalationNotifier(
        settings=_settings(**(settings_kw or {})),
        pic_registry=_registry(pic),
        email_sender=email_sender or _FakeEmailSender(),
        twilio_adapter=None,
        chatwoot_request=_fake_cw,
        dealer_email_map=dealer_map or {},
    )
    return notifier, sent_emails


async def test_notify_email_channel_escalation_sends_customer_ack_when_enabled() -> None:
    notifier, sent = _notifier(
        pic=None, settings_kw={"email_escalation_ack_enabled": True}
    )
    await notifier.notify_email_channel_escalation(
        conv_id="9", title="Late delivery", body="details",
        department=None, dealer=None, customer_email="alex@customer.example",
    )
    assert len(sent) == 1
    assert sent[0]["to"] == ["alex@customer.example"]
    assert "specialist team" in sent[0]["body"]


async def test_notify_email_channel_escalation_skips_ack_when_disabled() -> None:
    notifier, sent = _notifier(
        pic=None, settings_kw={"email_escalation_ack_enabled": False}
    )
    await notifier.notify_email_channel_escalation(
        conv_id="9", title="t", body="b",
        department=None, dealer=None, customer_email="alex@customer.example",
    )
    assert sent == []


async def test_notify_email_channel_escalation_sends_dealer_forward_when_mapped() -> None:
    notifier, sent = _notifier(
        pic=None, dealer_map={"kl_pj": "kl-pj@dealer.example"},
    )
    await notifier.notify_email_channel_escalation(
        conv_id="9", title="t", body="b",
        department=None, dealer="kl_pj", customer_email=None,
    )
    assert len(sent) == 1
    assert sent[0]["to"] == ["kl-pj@dealer.example"]


async def test_notify_email_channel_escalation_skips_dealer_when_unmapped() -> None:
    notifier, sent = _notifier(pic=None, dealer_map={})
    await notifier.notify_email_channel_escalation(
        conv_id="9", title="t", body="b",
        department=None, dealer="unknown_slug", customer_email=None,
    )
    assert sent == []


async def test_notify_email_channel_escalation_sends_pic_and_dealer_together() -> None:
    notifier, sent = _notifier(
        pic=_APPS_PIC,
        dealer_map={"kl_pj": "kl-pj@dealer.example"},
        settings_kw={"escalation_email_enabled": True},
    )
    await notifier.notify_email_channel_escalation(
        conv_id="9", title="t", body="b",
        department="dept_apps", dealer="kl_pj", customer_email=None,
    )
    recipients = {tuple(e["to"]) for e in sent}
    assert ("alice@proton.my",) in recipients
    assert ("kl-pj@dealer.example",) in recipients


async def test_notify_email_channel_escalation_noop_when_everything_off() -> None:
    notifier, sent = _notifier(
        pic=None,
        dealer_map={},
        settings_kw={"escalation_email_enabled": False, "email_escalation_ack_enabled": False},
    )
    await notifier.notify_email_channel_escalation(
        conv_id="9", title="t", body="b",
        department=None, dealer=None, customer_email="alex@customer.example",
    )
    assert sent == []
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_escalation_notifier.py -k email_channel_escalation -v`
Expected: FAIL — `EscalationNotifier.__init__() got an unexpected keyword argument 'dealer_email_map'` and `AttributeError: 'EscalationNotifier' object has no attribute 'notify_email_channel_escalation'`

- [ ] **Step 8: Implement `notify_email_channel_escalation`**

In `escalation_notifier.py`, update `__init__` to accept the new parameter:

```python
    def __init__(
        self,
        settings: Settings,
        pic_registry: PicRegistry,
        email_sender: SmtpEmailSender,
        twilio_adapter: TwilioChannelAdapter | None,
        chatwoot_request: _CWRequest,
        dealer_email_map: dict[str, str] | None = None,
    ) -> None:
        self._settings = settings
        self._pic_registry = pic_registry
        self._email_sender = email_sender
        self._twilio = twilio_adapter
        self._cw = chatwoot_request
        self._dealer_email_map = dealer_email_map or {}
```

Add these new methods to the `EscalationNotifier` class (after `_send_wa`,
before `_write_case_state`):

```python
    async def notify_email_channel_escalation(
        self,
        *,
        conv_id: str,
        title: str,
        body: str,
        department: str | None,
        dealer: str | None,
        customer_email: str | None,
    ) -> None:
        """Two-thread email escalation (EM-7) for a natively-escalated
        Email-channel conversation -- reached only via the /escalation/notify
        endpoint, called from agent/'s maybe_escalate() when a human applies
        the `escalate` label. Independent of notify(), which is the AI's own
        autonomous escalation path and is never touched by this method.

        Three independent, best-effort sends: customer ack, PIC email, dealer
        forward. Each failure is logged and does not affect the others.
        """
        if self._settings.email_escalation_ack_enabled and customer_email:
            self._send_customer_ack(customer_email, title=title)

        if self._settings.escalation_email_enabled:
            pic = self._resolve_pic(department)
            if pic is not None:
                self._send_email(
                    pic, conv_id=conv_id, title=title, body=body, zammad_ticket_number=None
                )

        if dealer:
            self._send_dealer_forward(dealer, conv_id=conv_id, title=title, body=body)

    def _send_customer_ack(self, to_email: str, *, title: str) -> None:
        try:
            self._email_sender.send(
                to=[to_email],
                cc=[],
                subject=f"Update on your case: {title}",
                body=self._settings.email_escalation_ack_template,
                attachments=[],
            )
        except Exception as exc:
            _log.warning("escalation_customer_ack_failed", to_email=to_email, error=str(exc))

    def _send_dealer_forward(self, dealer_slug: str, *, conv_id: str, title: str, body: str) -> None:
        email = self._dealer_email_map.get(dealer_slug.lower())
        if not email:
            _log.info("escalation_dealer_unmapped", dealer=dealer_slug)
            return
        email_body = textwrap.dedent(f"""\
            A case has been escalated and forwarded to your dealership.

            Subject  : {title}
            Reference: Chatwoot conversation #{conv_id}

            --- Summary ---
            {body}

            Please action this case promptly.
        """)
        try:
            self._email_sender.send(
                to=[email],
                cc=[],
                subject=f"[Escalation - Dealer Forward] {title}",
                body=email_body,
                attachments=[],
            )
        except Exception as exc:
            _log.warning("escalation_dealer_forward_failed", dealer=dealer_slug, error=str(exc))
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_escalation_notifier.py -v`
Expected: PASS — all tests in the file, including the pre-existing ones (regression check).

- [ ] **Step 10: Wire `dealer_email_map` at construction in `main.py`**

In `backend/apps/backend/src/chatbot/main.py`, find the `EscalationNotifier(`
construction (around line 354, inside `if chatwoot_client is not None:`) and
add the import + the new argument:

Add to the imports section (near the other `escalation_notifier` import):
```python
from chatbot.features.chat.escalation_notifier import EscalationNotifier, build_dealer_email_map
```
This replaces the existing line 29, `from
chatbot.features.chat.escalation_notifier import EscalationNotifier`.

Update the construction call:
```python
    if chatwoot_client is not None:
        escalation_notifier = EscalationNotifier(
            settings=settings,
            pic_registry=pic_registry,
            email_sender=email_sender,
            twilio_adapter=twilio_adapter,
            chatwoot_request=chatwoot_client._request,  # type: ignore[arg-type]
            dealer_email_map=build_dealer_email_map(settings),
        )
        chatwoot_client._escalation_notifier = escalation_notifier  # type: ignore[assignment]
```

- [ ] **Step 11: Run the full backend suite**

Run: `cd backend/apps/backend && uv run pytest src/ -q`
Expected: PASS, no new failures (regression check for `main.py`'s import/wiring change).

- [ ] **Step 12: Commit**

```bash
git add backend/apps/backend/src/chatbot/platform/config.py \
        backend/apps/backend/src/chatbot/features/chat/escalation_notifier.py \
        backend/apps/backend/src/chatbot/features/chat/test_escalation_notifier.py \
        backend/apps/backend/src/chatbot/main.py
git commit -m "feat(escalation): add EM-7 dealer-forward + customer-ack to EscalationNotifier"
```

---

### Task 2: Backend — new `/escalation/notify` endpoint

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/escalation_router.py`
- Modify: `backend/apps/backend/src/chatbot/main.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_escalation_router.py`

**Interfaces:**
- Consumes: `EscalationNotifier.notify_email_channel_escalation(...)` from Task 1.
- Produces: `build_escalation_router(notifier: EscalationNotifier, chatwoot_request: _CWRequest, settings: Settings) -> APIRouter` — mounted in `main.py`.

- [ ] **Step 1: Write the failing router test**

Create `backend/apps/backend/src/chatbot/features/chat/test_escalation_router.py`:

```python
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.escalation_router import build_escalation_router
from chatbot.platform.config import Settings


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {"proton_backend_key": "test-key-123"}
    base.update(kw)
    return Settings(_env_file=None, **base)


def _client(notifier: AsyncMock, cw_response: dict | None, settings: Settings) -> TestClient:
    async def _fake_cw(method: str, path: str, payload: Any = None) -> dict | None:
        return cw_response

    app = FastAPI()
    app.include_router(build_escalation_router(notifier, _fake_cw, settings))
    return TestClient(app)


def test_notify_rejects_missing_api_key() -> None:
    notifier = AsyncMock()
    client = _client(notifier, {}, _settings())
    resp = client.post("/escalation/notify", json={"conversation_id": "9", "title": "t", "body": "b"})
    assert resp.status_code == 401
    notifier.notify_email_channel_escalation.assert_not_called()


def test_notify_rejects_wrong_api_key() -> None:
    notifier = AsyncMock()
    client = _client(notifier, {}, _settings())
    resp = client.post(
        "/escalation/notify",
        headers={"x-api-key": "wrong"},
        json={"conversation_id": "9", "title": "t", "body": "b"},
    )
    assert resp.status_code == 401


def test_notify_resolves_customer_email_and_calls_notifier() -> None:
    notifier = AsyncMock()
    cw_response = {"meta": {"sender": {"email": "alex@customer.example"}}}
    client = _client(notifier, cw_response, _settings())
    resp = client.post(
        "/escalation/notify",
        headers={"x-api-key": "test-key-123"},
        json={
            "conversation_id": "9",
            "title": "Late delivery",
            "body": "details",
            "department": "dept_apps",
            "dealer": "kl_pj",
        },
    )
    assert resp.status_code == 200
    notifier.notify_email_channel_escalation.assert_awaited_once_with(
        conv_id="9",
        title="Late delivery",
        body="details",
        department="dept_apps",
        dealer="kl_pj",
        customer_email="alex@customer.example",
    )


def test_notify_handles_missing_customer_email() -> None:
    notifier = AsyncMock()
    client = _client(notifier, {"meta": {}}, _settings())
    resp = client.post(
        "/escalation/notify",
        headers={"x-api-key": "test-key-123"},
        json={"conversation_id": "9", "title": "t", "body": "b"},
    )
    assert resp.status_code == 200
    notifier.notify_email_channel_escalation.assert_awaited_once_with(
        conv_id="9", title="t", body="b", department=None, dealer=None, customer_email=None,
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_escalation_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'chatbot.features.chat.escalation_router'`

- [ ] **Step 3: Implement the router**

Create `backend/apps/backend/src/chatbot/features/chat/escalation_router.py`:

```python
"""POST /escalation/notify -- two-thread email escalation (EM-7) for a
natively-escalated Email-channel conversation.

Called by the agent/ service's sync.maybe_escalate() when a human applies
the `escalate` label to a conversation on an Email inbox. Deliberately
separate from EscalationNotifier.notify(), which is the AI's own autonomous
escalation path (fired from ChatwootAdapter._fire_escalation) and never
reaches this endpoint -- the codebase already suppresses the `escalate`
label on AI-driven escalations to avoid the two paths colliding.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any, Callable, Coroutine

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

if TYPE_CHECKING:
    from chatbot.features.chat.escalation_notifier import EscalationNotifier
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_CWRequest = Callable[..., Coroutine[Any, Any, dict[str, Any] | None]]


class _NotifyIn(BaseModel):
    conversation_id: str
    title: str
    body: str
    department: str | None = None
    dealer: str | None = None


def _require_api_key(settings: Settings):
    """401s unless x-api-key matches proton_backend_key -- the same key the
    agent/ service already authenticates its other backend calls with."""

    def _check(x_api_key: str | None = Header(default=None)) -> None:
        if (
            not x_api_key
            or not settings.proton_backend_key
            or not hmac.compare_digest(x_api_key, settings.proton_backend_key)
        ):
            raise HTTPException(status_code=401, detail="Missing or invalid API key")

    return _check


async def _resolve_customer_email(chatwoot_request: _CWRequest, conv_id: str) -> str | None:
    """Best-effort lookup of the conversation's contact email via
    GET /conversations/{id} (meta.sender.email). None on any failure or
    missing field -- the caller sends the ack only when this resolves."""
    try:
        data = await chatwoot_request("GET", f"/conversations/{conv_id}", None)
    except Exception:
        _log.warning("escalation_notify_customer_email_lookup_failed", conv_id=conv_id)
        return None
    if not isinstance(data, dict):
        return None
    sender = (data.get("meta") or {}).get("sender") or {}
    email = sender.get("email")
    return str(email) if email else None


def build_escalation_router(
    notifier: EscalationNotifier,
    chatwoot_request: _CWRequest,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()
    auth = _require_api_key(settings)

    @router.post("/escalation/notify", dependencies=[Depends(auth)])
    async def notify(payload: _NotifyIn) -> dict[str, str]:
        customer_email = await _resolve_customer_email(chatwoot_request, payload.conversation_id)
        await notifier.notify_email_channel_escalation(
            conv_id=payload.conversation_id,
            title=payload.title,
            body=payload.body,
            department=payload.department,
            dealer=payload.dealer,
            customer_email=customer_email,
        )
        return {"status": "ok"}

    return router
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_escalation_router.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Mount the router in `main.py`**

In `main.py`, add the import near the other `chat` feature imports:
```python
from chatbot.features.chat.escalation_router import build_escalation_router
```

Immediately after the `EscalationNotifier(...)` construction block from Task
1 Step 10 (still inside `if chatwoot_client is not None:`), add:

```python
        app.include_router(
            build_escalation_router(escalation_notifier, chatwoot_client._request, settings)  # type: ignore[arg-type]
        )
```

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend/apps/backend && uv run pytest src/ -q`
Expected: PASS, no new failures.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/backend/src/chatbot/features/chat/escalation_router.py \
        backend/apps/backend/src/chatbot/features/chat/test_escalation_router.py \
        backend/apps/backend/src/chatbot/main.py
git commit -m "feat(escalation): add POST /escalation/notify endpoint (EM-7)"
```

---

### Task 3: Agent — `ProtonConfigClient.notify_email_escalation`

**Files:**
- Modify: `agent/app/clients/proton.py`
- Test: `agent/tests/test_proton_client.py`

**Interfaces:**
- Produces: `ProtonConfigClient.notify_email_escalation(conversation_id: int, title: str, body: str, department: str | None, dealer: str | None) -> None`

- [ ] **Step 1: Write the failing test**

Add to `agent/tests/test_proton_client.py` (mirroring the existing
`assign_agent` test — check that file for the exact fixture/client
construction pattern used there and match it):

```python
@respx.mock
async def test_notify_email_escalation_posts_payload():
    route = respx.post(f"{PROTON_BASE}/escalation/notify").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    client = ProtonConfigClient(base_url=PROTON_BASE, api_key="k")
    await client.notify_email_escalation(
        conversation_id=9, title="Late delivery", body="details",
        department="dept_apps", dealer="kl_pj",
    )
    assert route.called
    sent = route.calls[0].request
    import json as _json
    assert _json.loads(sent.content) == {
        "conversation_id": "9",
        "title": "Late delivery",
        "body": "details",
        "department": "dept_apps",
        "dealer": "kl_pj",
    }


@respx.mock
async def test_notify_email_escalation_swallows_errors():
    respx.post(f"{PROTON_BASE}/escalation/notify").mock(
        return_value=httpx.Response(500)
    )
    client = ProtonConfigClient(base_url=PROTON_BASE, api_key="k")
    # Must not raise.
    await client.notify_email_escalation(
        conversation_id=9, title="t", body="b", department=None, dealer=None,
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd agent && pytest tests/test_proton_client.py -k notify_email_escalation -v`
Expected: FAIL with `AttributeError: 'ProtonConfigClient' object has no attribute 'notify_email_escalation'`

- [ ] **Step 3: Implement the method**

In `agent/app/clients/proton.py`, add after `assign_agent` (the last method
in the class):

```python
    async def notify_email_escalation(
        self,
        conversation_id: int,
        title: str,
        body: str,
        department: str | None,
        dealer: str | None,
    ) -> None:
        """Ask the backend to send the EM-7 two-thread email escalation for a
        natively-escalated Email-channel conversation (POST
        /escalation/notify). Fire-and-forget: any error is logged and
        swallowed, matching assign_agent's pattern."""
        try:
            response = await self._client.post(
                "/escalation/notify",
                json={
                    "conversation_id": str(conversation_id),
                    "title": title,
                    "body": body,
                    "department": department,
                    "dealer": dealer,
                },
            )
            response.raise_for_status()
        except Exception:
            logger.debug("proton_config: notify_email_escalation failed", exc_info=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd agent && pytest tests/test_proton_client.py -v`
Expected: PASS — all tests in the file (regression check).

- [ ] **Step 5: Commit**

```bash
git add agent/app/clients/proton.py agent/tests/test_proton_client.py
git commit -m "feat(agent): add ProtonConfigClient.notify_email_escalation (EM-7)"
```

---

### Task 4: Agent — wire `maybe_escalate` for Email-channel conversations

**Files:**
- Modify: `agent/app/config.py`
- Modify: `agent/app/services/sync.py`
- Test: `agent/tests/test_sync_escalation.py`

**Interfaces:**
- Consumes: `ProtonConfigClient.notify_email_escalation(...)` from Task 3; `get_proton_config_client()` from `agent/app/clients/deps.py` (existing); `chatwoot.get_conversation(conversation_id)` and `chatwoot.get_inbox(inbox_id)` from `agent/app/clients/chatwoot.py` (existing).
- Produces: `sync._maybe_notify_email_escalation(conversation_id: int) -> None` (module-private helper, called from `maybe_escalate`).

- [ ] **Step 1: Add the config flag**

In `agent/app/config.py`, add near `lifecycle_enabled` (or any other
similarly-scoped flag):

```python
    # EM-7: two-thread email escalation for natively-escalated Email-channel
    # conversations. Requires PROTON_BACKEND_URL/KEY to be set (fail-open,
    # no-op otherwise). Default off, byte-identical when unset.
    email_escalation_enabled: bool = False
```

- [ ] **Step 2: Write the failing tests**

Add to `agent/tests/test_sync_escalation.py` (check the file's existing
imports/fixtures first and reuse its `CHATWOOT`/`PROTON` base-URL constants
and `monkeypatch`/`respx` conventions rather than redefining them):

```python
@respx.mock
async def test_maybe_escalate_notifies_email_channel_conversation(monkeypatch):
    monkeypatch.setattr(get_settings(), "zammad_ticketing_enabled", False)
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", True)
    monkeypatch.setattr(get_settings(), "proton_backend_url", "http://proton-backend:8080")
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    get_proton_config_client.cache_clear()

    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9").mock(
        return_value=httpx.Response(200, json={"id": 9, "inbox_id": 5})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/5").mock(
        return_value=httpx.Response(200, json={"id": 5, "channel_type": "Channel::Email"})
    )
    notify_route = respx.post("http://proton-backend:8080/escalation/notify").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    await sync.maybe_escalate(
        {"id": 9, "labels": ["escalate", "dept_apps", "dealer_kl_pj"]}
    )

    assert notify_route.called
    import json as _json
    sent = _json.loads(notify_route.calls[0].request.content)
    assert sent["conversation_id"] == "9"
    assert sent["department"] == "apps"
    assert sent["dealer"] == "kl_pj"
    get_proton_config_client.cache_clear()


@respx.mock
async def test_maybe_escalate_skips_notify_for_non_email_channel(monkeypatch):
    monkeypatch.setattr(get_settings(), "zammad_ticketing_enabled", False)
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", True)
    monkeypatch.setattr(get_settings(), "proton_backend_url", "http://proton-backend:8080")
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    get_proton_config_client.cache_clear()

    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9").mock(
        return_value=httpx.Response(200, json={"id": 9, "inbox_id": 3})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/3").mock(
        return_value=httpx.Response(200, json={"id": 3, "channel_type": "Channel::TwilioSms"})
    )
    notify_route = respx.post("http://proton-backend:8080/escalation/notify").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    await sync.maybe_escalate({"id": 9, "labels": ["escalate"]})

    assert not notify_route.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_maybe_escalate_skips_notify_when_flag_off(monkeypatch):
    monkeypatch.setattr(get_settings(), "zammad_ticketing_enabled", False)
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", False)

    conv_route = respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9")

    await sync.maybe_escalate({"id": 9, "labels": ["escalate"]})

    assert not conv_route.called
```

`sync.py` already imports `get_proton_config_client` directly (`from
app.clients.deps import get_chatwoot_client, get_proton_config_client,
get_zammad_client`), so the new helper in Step 4 calls it with no new
import needed. In the test file, import it the same way —
`from app.clients.deps import get_proton_config_client` — the
`get_proton_config_client.cache_clear()` calls in the test above use that
same imported name, so each test starts from a clean `lru_cache` and
doesn't leak a stale-`base_url` client into the next test.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd agent && pytest tests/test_sync_escalation.py -k email_channel -v`
Expected: FAIL — `notify_route.called` is `False` (the code doesn't call it yet).

- [ ] **Step 4: Implement `_maybe_notify_email_escalation` and wire it into `maybe_escalate`**

In `agent/app/services/sync.py`, add near `_DEALER_LABEL`:

```python
_DEPT_LABEL = re.compile(r"^dept_(.+)$")
```

Add a new function (near `maybe_stamp_dealer_escalation`, which it closely
resembles):

```python
async def _maybe_notify_email_escalation(conversation_id: int, labels: list[str]) -> None:
    """EM-7: for an Email-channel conversation, ask the backend to send the
    two-thread escalation email (customer ack + PIC/dealer forward).

    Fail-open throughout: any missing config, unreachable service, or
    resolution failure just means no email fires -- never raises, matching
    every other background-task helper in this module.
    """
    settings = get_settings()
    if not settings.email_escalation_enabled:
        return

    proton = get_proton_config_client()
    if proton is None:
        return

    try:
        chatwoot = get_chatwoot_client()
        conversation = await chatwoot.get_conversation(conversation_id)
        inbox_id = (conversation or {}).get("inbox_id")
        if inbox_id is None:
            return
        inbox = await chatwoot.get_inbox(inbox_id)
    except Exception:
        logger.exception(
            "maybe_escalate: failed to resolve channel for conversation %s", conversation_id
        )
        return

    if (inbox or {}).get("channel_type") != "Channel::Email":
        return

    department = next(
        (m.group(1) for lbl in labels if (m := _DEPT_LABEL.match(lbl))), None
    )
    dealer = next(
        (m.group(1) for lbl in labels if (m := _DEALER_LABEL.match(lbl))), None
    )

    await proton.notify_email_escalation(
        conversation_id=conversation_id,
        title=f"Escalated conversation #{conversation_id}",
        body=f"Conversation #{conversation_id} was escalated by an agent.",
        department=department,
        dealer=dealer,
    )
```

Check the top of `sync.py` for the existing import style and add whichever
of `get_proton_config_client` / `deps` module import matches it (see the
note in Step 2).

Update `maybe_escalate` to call the new helper BEFORE the
`zammad_ticketing_enabled` early return (email escalation must fire
regardless of the Zammad flag — it's the Chatwoot-only-tenant case where
this matters most):

```python
async def maybe_escalate(payload: dict) -> None:
    """Handle a Chatwoot `conversation_updated` event: escalate to a Zammad
    ticket if the `escalate` label is present and the conversation isn't
    already linked. Also fires the EM-7 email-channel notification (Chatwoot-
    only, independent of Zammad)."""
    conversation_id = payload.get("id")
    labels = payload.get("labels") or []
    if conversation_id is None or "escalate" not in labels:
        return

    await _maybe_notify_email_escalation(conversation_id, labels)

    if not get_settings().zammad_ticketing_enabled:
        # Chatwoot-only tenant: the `escalate` label no longer creates a Zammad
        # ticket; it's just a tag the agent handles natively in Chatwoot.
        logger.debug(
            "maybe_escalate: zammad integration disabled, skipping conversation %s",
            conversation_id,
        )
        return

    existing = await _conversation_link_by_chatwoot_id(conversation_id)
    if existing is not None:
        logger.info(
            "maybe_escalate: conversation %s already escalated, skipping",
            conversation_id,
        )
        return

    await escalate_conversation(conversation_id)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd agent && pytest tests/test_sync_escalation.py -v`
Expected: PASS — all tests in the file (regression check for the existing
Zammad-path tests, which must be unaffected).

- [ ] **Step 6: Run the full agent suite**

Run: `cd agent && pytest`
Expected: PASS, no new failures.

- [ ] **Step 7: Document the new env vars**

Add to `deploy/tenants/example.env` (agent-visible vars section) and
`backend/apps/backend/.env.example` (backend-only vars section):

```
# EM-7: two-thread email escalation for natively-escalated Email-channel
# conversations (agent applies the `escalate` label). Requires
# PROTON_BACKEND_URL/KEY to be set on the agent/ side.
EMAIL_ESCALATION_ENABLED=false
EMAIL_ESCALATION_ACK_ENABLED=false
EMAIL_ESCALATION_ACK_TEMPLATE="Your case has been escalated to a specialist team who will follow up shortly."
DEALER_EMAIL_MAP_JSON={}
```
(`EMAIL_ESCALATION_ENABLED` goes in `deploy/tenants/example.env`; the other
three are backend-only and go in `backend/apps/backend/.env.example`.)

- [ ] **Step 8: Commit**

```bash
git add agent/app/config.py agent/app/services/sync.py agent/tests/test_sync_escalation.py \
        deploy/tenants/example.env backend/apps/backend/.env.example
git commit -m "feat(agent): wire maybe_escalate to notify email-channel escalations (EM-7)"
```

---

## Track B: IVR-4 — per-turn language reminder

### Task 5: Backend — `send_text_hint` on the Live session + config flag

**Files:**
- Modify: `backend/apps/backend/src/chatbot/platform/config.py`
- Modify: `backend/apps/backend/src/chatbot/features/chat/phone/gemini_live.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/phone/test_gemini_live.py`

**Interfaces:**
- Produces: `LiveSession.send_text_hint(text: str) -> None` (new Protocol method); `_GeminiLiveSession.send_text_hint` implementation.

- [ ] **Step 1: Add the config flag**

In `config.py`, add near the other `gemini_live_*` fields (around line
284-289):

```python
    # IVR-4: after each caller utterance, send a short content-free reminder
    # telling the model to re-evaluate the reply language fresh each turn,
    # rather than anchoring to the conversation's established language.
    # Default off -- byte-identical when unset; the exact Live API turn-
    # injection semantics can't be verified without a real call, so this
    # ships gated and can be flipped per-tenant to A/B against today.
    phone_language_nudge_enabled: bool = False
```

- [ ] **Step 2: Write the failing test**

Open `test_gemini_live.py` first to see the existing mock/fixture pattern
for `_GeminiLiveSession` (it wraps a fake `live.AsyncSession`-like object).
Add a test following that same pattern:

```python
async def test_send_text_hint_forwards_to_realtime_input():
    sent: list[dict] = []

    class _FakeSDKSession:
        async def send_realtime_input(self, **kwargs):
            sent.append(kwargs)

    session = _GeminiLiveSession(_FakeSDKSession())
    await session.send_text_hint("match the caller's language")

    assert sent == [{"text": "match the caller's language"}]
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/phone/test_gemini_live.py -k send_text_hint -v`
Expected: FAIL with `AttributeError: '_GeminiLiveSession' object has no attribute 'send_text_hint'`

- [ ] **Step 4: Implement `send_text_hint`**

In `gemini_live.py`, add to the `LiveSession` Protocol:

```python
class LiveSession(Protocol):
    async def send_audio(self, pcm16k: bytes) -> None: ...
    async def send_tool_response(
        self, call_id: str, name: str, response: dict[str, object]
    ) -> None: ...
    async def send_text_hint(self, text: str) -> None: ...
    def events(self) -> AsyncIterator[LiveEvent]: ...
```

Add the implementation to `_GeminiLiveSession`, after `send_tool_response`:

```python
    async def send_text_hint(self, text: str) -> None:
        """Send a short text-only input alongside the audio stream -- used
        for IVR-4's per-turn language reminder. Not spoken aloud by the
        caller and does not itself count as a caller turn."""
        await self._session.send_realtime_input(text=text)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/phone/test_gemini_live.py -v`
Expected: PASS — all tests in the file.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/backend/src/chatbot/platform/config.py \
        backend/apps/backend/src/chatbot/features/chat/phone/gemini_live.py \
        backend/apps/backend/src/chatbot/features/chat/phone/test_gemini_live.py
git commit -m "feat(phone): add LiveSession.send_text_hint + phone_language_nudge_enabled flag (IVR-4)"
```

---

### Task 6: Backend — send the reminder after each caller utterance

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/phone/bridge.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/phone/test_bridge.py`

**Interfaces:**
- Consumes: `LiveSession.send_text_hint(text: str) -> None` from Task 5.

**Ground truth (read during planning, so no exploration needed):**
`bridge.py`'s `pump()` handles each `InputTranscript` event as one item in
a stream of DELTAS (per its own comment: "Gemini Live streams transcription
as incremental deltas; concatenate consecutive same-role fragments into one
coherent turn") — there is no existing signal for "this is the last delta
of the utterance." Sending the reminder on every delta (not just once per
utterance) is the simplest correct behavior here: `send_text_hint` is cheap,
idempotent, and fire-and-forget, so a caller utterance spanning 3 deltas
just means 3 harmless reminders instead of 1 — do not add turn-boundary
detection logic to avoid this, it doesn't exist in the current event model
and isn't worth inventing for a nudge.

`PhoneBridge.__init__` currently takes exactly 4 params: `live,
knowledge_port, conversation_log_port, send_twilio` (no `settings`) — this
task adds a 5th. The pump loop's `InputTranscript` branch is:
```python
            elif isinstance(event, InputTranscript):
                self._append_transcript("USER", event.text)
```

`test_bridge.py`'s existing fixtures: `_FakeLive` (a class with
`send_audio`/`send_tool_response`/`events`, constructed as
`_FakeLive(scripted: list[LiveEvent])`) and a `_bridge(live, sent, log=None)`
helper that builds `PhoneBridge(live, _FakeKnowledge(), log or _FakeLog(),
send_twilio)`. Both need updating for the new constructor arg.

- [ ] **Step 1: Write the failing test**

In `test_bridge.py`, add `send_text_hint` tracking to `_FakeLive` (after its
existing `send_tool_response` method):

```python
    async def send_text_hint(self, text: str) -> None:
        self.text_hints.append(text)
```

and initialize `self.text_hints: list[str] = []` in `_FakeLive.__init__`
alongside the existing `self.audio_sent`/`self.tool_responses`.

Add the `Settings` import at the top of the file:
```python
from chatbot.platform.config import Settings
```

Update the `_bridge` helper to accept and thread through settings:
```python
def _bridge(
    live: _FakeLive,
    sent: list[dict[str, object]],
    log: _FakeLog | None = None,
    settings: Settings | None = None,
) -> PhoneBridge:
    async def send_twilio(msg: dict[str, object]) -> None:
        sent.append(msg)

    return PhoneBridge(
        live, _FakeKnowledge(), log or _FakeLog(), send_twilio,
        settings or Settings(_env_file=None),
    )
```

Add the new tests (near `test_pump_accumulates_transcript`):

```python
async def test_pump_sends_language_hint_after_input_transcript_when_enabled() -> None:
    live = _FakeLive([InputTranscript("Saya nak tanya")])
    b = _bridge(live, [], settings=Settings(_env_file=None, phone_language_nudge_enabled=True))
    await b.pump()
    assert len(live.text_hints) == 1
    assert "language" in live.text_hints[0].lower()


async def test_pump_skips_language_hint_when_disabled() -> None:
    live = _FakeLive([InputTranscript("Saya nak tanya")])
    b = _bridge(live, [], settings=Settings(_env_file=None, phone_language_nudge_enabled=False))
    await b.pump()
    assert live.text_hints == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/phone/test_bridge.py -k language_hint -v`
Expected: FAIL — `TypeError: PhoneBridge.__init__() takes 5 positional arguments but 6 were given` (or similar, since `PhoneBridge` doesn't accept `settings` yet).

- [ ] **Step 3: Add `settings` to `PhoneBridge.__init__` and send the hint**

In `bridge.py`, add the `TYPE_CHECKING` import for `Settings`:
```python
if TYPE_CHECKING:
    from chatbot.features.chat.phone.gemini_live import LiveSession
    from chatbot.features.chat.ports import ConversationLogPort, KnowledgePort
    from chatbot.platform.config import Settings
```

Update `__init__`:
```python
    def __init__(
        self,
        live: LiveSession,
        knowledge_port: KnowledgePort,
        conversation_log_port: ConversationLogPort,
        send_twilio: Callable[[dict[str, object]], Awaitable[None]],
        settings: Settings,
    ) -> None:
        self._live = live
        self._knowledge = knowledge_port
        self._log_port = conversation_log_port
        self._send_twilio = send_twilio
        self._settings = settings
        self.stream_sid: str | None = None
        self.call_sid: str | None = None
        self.transcript: list[tuple[str, str]] = []
        self.handoff: dict[str, str] | None = None
        self.csat_score: int | None = None
```

Update the `pump()` method's `InputTranscript` branch:
```python
            elif isinstance(event, InputTranscript):
                self._append_transcript("USER", event.text)
                if self._settings.phone_language_nudge_enabled:
                    await self._live.send_text_hint(
                        "(Reminder: match your next reply's language to what "
                        "the caller just said, even mid-conversation.)"
                    )
```

- [ ] **Step 4: Update `router.py`'s `PhoneBridge` construction**

In `router.py`'s `phone_stream` method, update the `PhoneBridge(...)` call
to pass the new argument:

```python
                bridge = PhoneBridge(
                    live,
                    self.orchestrator._knowledge_port,
                    self.orchestrator._conversation_log_port,
                    websocket.send_json,
                    self.orchestrator._settings,
                )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/phone/test_bridge.py -v`
Expected: PASS — all tests in the file.

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend/apps/backend && uv run pytest src/ -q`
Expected: PASS, no new failures (regression check for the `router.py`
constructor-call change).

- [ ] **Step 7: Document the env var and commit**

Add to `backend/apps/backend/.env.example`:
```
# IVR-4: per-turn language reminder for the phone voice pipeline (see
# docs/superpowers/specs/2026-08-03-channel-followups-ivr-em7-category-hierarchy-design.md).
PHONE_LANGUAGE_NUDGE_ENABLED=false
```

```bash
git add backend/apps/backend/src/chatbot/features/chat/phone/bridge.py \
        backend/apps/backend/src/chatbot/features/chat/phone/test_bridge.py \
        backend/apps/backend/src/chatbot/features/chat/router.py \
        backend/apps/backend/.env.example
git commit -m "feat(phone): send per-turn language reminder when phone_language_nudge_enabled (IVR-4)"
```

**Manual verification (cannot be automated — no real Gemini Live API access
in this repo's test suite):** once deployed to proton with the flag on, pull
a fresh test-call transcript the same way this bug was diagnosed (`rails
runner` on `proton-chatwoot-rails`, see the spec's evidence section) and
confirm a code-switched turn like conversation #35's turn 3 now gets a
reply matching the caller's actual language.

---

## Track C: Category hierarchy — cascading `case_category`/`case_subcategory`

### Task 7: Chatwoot fork patch — cascading subcategory filter

**Files:**
- Modify (in the clone at `/Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot`): `app/javascript/dashboard/routes/dashboard/conversation/customAttributes/CustomAttributes.vue`
- Create: `deploy/chatwoot-fork/patches/0036-case-category-hierarchy.patch`

**Interfaces:** none (self-contained frontend change; no new component,
edits the existing generic attribute-rendering loop).

**Background for the implementer:** `CustomAttributes.vue` renders every
custom attribute generically via a shared `CustomAttribute.vue` component in
a `Draggable` loop, binding `:values="element.attribute_values"` and
`@update="onUpdate"`. The taxonomy's hierarchy is already encoded in the
data: `case_subcategory`'s option strings are provisioned as `"{category
label}: {subcategory}"` (see `chatwoot-config/provision_case_taxonomy.py`'s
`_subcategory_options`) — e.g. `"Sales: New Vehicle Inquiry"`. No backend or
data-model change is needed; this task only filters which of those
already-existing strings are shown once a category is selected.

- [ ] **Step 1: Confirm the starting point matches the last-applied patch**

```bash
cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot
git log --oneline -3
git status
```
Expected: clean tree, HEAD at the commit noted in the most recent patch
work (per project memory, the clone should have patches 0001-0035 already
applied cleanly — if `git status` is not clean or HEAD doesn't match,
STOP and re-sync the clone before continuing, per
[[chatwoot-fork-patch-network-restriction]] in project memory: this sandbox
cannot reach github.com, so re-cloning upstream is not an option — reconcile
from the existing patch files instead).

- [ ] **Step 2: Edit `CustomAttributes.vue`**

Add a new computed function right after `filteredCustomAttributes` (around
line 89 in the file read during planning):

```javascript
const CASCADING_PREFIX_ATTR = 'case_category';
const CASCADING_CHILD_ATTR = 'case_subcategory';

const valuesForAttribute = attribute => {
  if (attribute.attribute_key !== CASCADING_CHILD_ATTR) {
    return attribute.attribute_values;
  }
  const selectedCategory = customAttributes.value[CASCADING_PREFIX_ATTR];
  if (!selectedCategory) {
    return attribute.attribute_values;
  }
  const prefix = `${selectedCategory}: `;
  return attribute.attribute_values.filter(v => v.startsWith(prefix));
};
```

Update the template's `CustomAttribute` binding (around line 291) to use it:
```diff
-              :values="element.attribute_values"
+              :values="valuesForAttribute(element)"
```

Update `onUpdate` (around line 200) to clear an invalidated subcategory:
```diff
 const onUpdate = async (key, value) => {
-  const updatedAttributes = { ...customAttributes.value, [key]: value };
+  const updatedAttributes = { ...customAttributes.value, [key]: value };
+  if (key === CASCADING_PREFIX_ATTR) {
+    const currentSub = updatedAttributes[CASCADING_CHILD_ATTR];
+    if (currentSub && !currentSub.startsWith(`${value}: `)) {
+      updatedAttributes[CASCADING_CHILD_ATTR] = '';
+    }
+  }
   try {
```

- [ ] **Step 3: Local build check (no network needed — this is a pure Vue/JS syntax check)**

```bash
cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot
docker build --target builder . 2>&1 | tail -40
```
Expected: builder stage completes without a Vite/SFC compile error. (Per
project memory, mustache-interpolation mistakes like `{{ '{{minutes}}' }}`
have broken this stage before — this change has no such interpolation, but
still verify.)

- [ ] **Step 4: Export the patch**

```bash
cd /Users/yudaadipratama/Archive/chatwoot-fork-work/chatwoot
git diff -- app/javascript/dashboard/routes/dashboard/conversation/customAttributes/CustomAttributes.vue \
  > /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0036-case-category-hierarchy.patch
```

- [ ] **Step 5: Verify the patch applies cleanly on top of the existing stack**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork
for p in patches/*.patch; do echo "=== $p ==="; done
# Confirm 0036 is present and correctly ordered (Dockerfile globs patches/*.patch,
# so ordering is by filename sort — 0036 sorts after 0035, correct).
```
Then do a full local build to confirm the WHOLE stack (0001-0036) still
applies and compiles — reuse the same command already established in this
session:
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork
UPSTREAM=$(cat UPSTREAM_VERSION) && docker build --build-arg UPSTREAM_VERSION=$UPSTREAM -t proton-chatwoot:$UPSTREAM-custom .
```
Expected: build succeeds (0 errors), same as the 0001-0035 build done
earlier this session.

- [ ] **Step 6: Commit the patch file**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add deploy/chatwoot-fork/patches/0036-case-category-hierarchy.patch
git commit -m "feat(chatwoot-fork): cascading case_category -> case_subcategory picker"
```

**Manual verification (documented as STILL TODO for the human tester, same
as every other UI patch in this program):** after redeploying the rebuilt
image, open a conversation, set `case_category` to a division, confirm
`case_subcategory`'s dropdown only shows that division's options; change
the category again and confirm an now-invalid subcategory value is cleared.

---

## Final Step: Update the deploy checklist

- [ ] Add a line to the running project memory / next-steps note (not part
  of this repo's tracked files) that patches through `0036` need a Cloud
  Build + redeploy pass, same as the `0031-0035` batch from earlier this
  session, and that `EMAIL_ESCALATION_ENABLED` / `EMAIL_ESCALATION_ACK_ENABLED`
  / `DEALER_EMAIL_MAP_JSON` / `PHONE_LANGUAGE_NUDGE_ENABLED` all need to be
  set per-tenant to activate (all default off).
