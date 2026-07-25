# WhatsApp KB-grounded Replies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the WhatsApp agent-bot source its reply text from the backend's KB-grounded copilot (`/assist/copilot`) instead of the local ungrounded Gemini draft, while leaving routing/escalation/handoff/lifecycle untouched.

**Architecture:** The agent-service orchestrator keeps `gemini.decide()` as the router. Only when the decision is `send_reply` (and the feature flag is on) does it build a conversation `thread` and call `ProtonConfigClient.copilot_answer(...)`; a non-empty answer replaces `decision.args["text"]`. Fail-open everywhere.

**Tech Stack:** Python 3.12, FastAPI, httpx, pydantic-settings, pytest (asyncio_mode=auto), respx. Run all commands from `agent/`.

## Global Constraints

- All new code in the **`agent/`** service. Run tests with `pytest` from `agent/`.
- Feature is **off by default** (`kb_grounded_replies=False`) → behavior byte-identical to today.
- **Fail-open**: any backend error / non-2xx / empty answer → keep the local draft; never raise out of the background task.
- Env var name must match the config field verbatim: `KB_GROUNDED_REPLIES`.
- Copilot request body: `{"conversation_id": str, "thread": [{"role": "user"|"assistant", "content": <non-empty str>}], "inbox_id": int|None, "assistant_id": None}`; response `{"answer": str, ...}`.
- Backend base URL / auth come from the existing `ProtonConfigClient` (`proton_backend_url` + `x-api-key: proton_backend_key`).
- Test constants already used in the suite: `CHATWOOT` base with account id `1`, conversation `42`; `PROTON_BASE = "http://proton-backend:8080"`.

---

### Task 1: Add the `kb_grounded_replies` config flag

**Files:**
- Modify: `agent/app/config.py` (Settings class, near the other agent/AI fields e.g. `agent_mode`)
- Modify: `deploy/tenants/example.env` (agent/AI section)
- Test: `agent/tests/test_sop_config.py` (append a case) — or a new `agent/tests/test_kb_grounded_config.py`

**Interfaces:**
- Produces: `Settings.kb_grounded_replies: bool` (default `False`), env `KB_GROUNDED_REPLIES`.

- [ ] **Step 1: Write the failing test** — create `agent/tests/test_kb_grounded_config.py`:

```python
"""kb_grounded_replies config flag: defaults off, reads the env var."""
from __future__ import annotations

from app.config import get_settings


def test_kb_grounded_replies_defaults_false():
    # conftest sets required env but not KB_GROUNDED_REPLIES → default False
    assert get_settings().kb_grounded_replies is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kb_grounded_config.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'kb_grounded_replies'`

- [ ] **Step 3: Add the field.** In `agent/app/config.py`, in the `Settings` class next to `agent_mode`, add:

```python
    kb_grounded_replies: bool = False
```

- [ ] **Step 4: Document the env var.** In `deploy/tenants/example.env`, near the agent-service AI settings (e.g. after `AGENT_MODE`), add:

```bash
# When true, the WhatsApp/agent-bot sources its reply text from the backend
# KB-grounded copilot (/assist/copilot) instead of the local Gemini draft.
# Requires PROTON_BACKEND_URL/KEY set. Off = local draft (unchanged).
KB_GROUNDED_REPLIES=false
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_kb_grounded_config.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/app/config.py deploy/tenants/example.env agent/tests/test_kb_grounded_config.py
git commit -m "feat(agent): add kb_grounded_replies config flag (default off)"
```

---

### Task 2: `ProtonConfigClient.copilot_answer(...)`

**Files:**
- Modify: `agent/app/clients/proton.py` (add method to `ProtonConfigClient`)
- Test: `agent/tests/test_proton_client.py` (append tests)

**Interfaces:**
- Consumes: existing `self._client` (httpx.AsyncClient with `x-api-key` header + base_url).
- Produces: `async def copilot_answer(self, conversation_id: str, thread: list[dict], inbox_id: int | None) -> str | None`.

- [ ] **Step 1: Write the failing tests.** Append to `agent/tests/test_proton_client.py`:

```python
@respx.mock
async def test_copilot_answer_returns_answer_string():
    route = respx.post(f"{PROTON_BASE}/assist/copilot").mock(
        return_value=httpx.Response(200, json={"answer": "The Proton X50 is an SUV.", "sources": []})
    )
    inner = httpx.AsyncClient(base_url=PROTON_BASE, headers={"x-api-key": "testkey"})
    client = ProtonConfigClient(base_url=PROTON_BASE, api_key="testkey", client=inner, ttl=0.0)
    thread = [{"role": "user", "content": "tanya pasal proton x50"}]
    answer = await client.copilot_answer("chatwoot-conv-42", thread, 7)
    assert answer == "The Proton X50 is an SUV."
    assert route.called
    sent = route.calls.last.request
    import json as _json
    body = _json.loads(sent.content)
    assert body == {"conversation_id": "chatwoot-conv-42", "thread": thread, "inbox_id": 7, "assistant_id": None}
    await client.aclose()


@respx.mock
async def test_copilot_answer_empty_answer_returns_none():
    respx.post(f"{PROTON_BASE}/assist/copilot").mock(
        return_value=httpx.Response(200, json={"answer": "   ", "sources": []})
    )
    inner = httpx.AsyncClient(base_url=PROTON_BASE, headers={"x-api-key": "testkey"})
    client = ProtonConfigClient(base_url=PROTON_BASE, api_key="testkey", client=inner, ttl=0.0)
    answer = await client.copilot_answer("chatwoot-conv-42", [{"role": "user", "content": "hi"}], 7)
    assert answer is None
    await client.aclose()


@respx.mock
async def test_copilot_answer_error_returns_none():
    respx.post(f"{PROTON_BASE}/assist/copilot").mock(return_value=httpx.Response(500))
    inner = httpx.AsyncClient(base_url=PROTON_BASE, headers={"x-api-key": "testkey"})
    client = ProtonConfigClient(base_url=PROTON_BASE, api_key="testkey", client=inner, ttl=0.0)
    answer = await client.copilot_answer("chatwoot-conv-42", [{"role": "user", "content": "hi"}], 7)
    assert answer is None
    await client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_proton_client.py -k copilot_answer -v`
Expected: FAIL — `AttributeError: 'ProtonConfigClient' object has no attribute 'copilot_answer'`

- [ ] **Step 3: Implement the method.** In `agent/app/clients/proton.py`, add to `ProtonConfigClient` (after `get_assistant_messages`):

```python
    async def copilot_answer(
        self, conversation_id: str, thread: list[dict], inbox_id: int | None
    ) -> str | None:
        """KB-grounded answer from the backend copilot (POST /assist/copilot).

        Not cached (per-turn). Fail-open: returns None on any error, non-2xx,
        or empty answer, so the caller can fall back to the local draft."""
        try:
            response = await self._client.post(
                "/assist/copilot",
                json={
                    "conversation_id": conversation_id,
                    "thread": thread,
                    "inbox_id": inbox_id,
                    "assistant_id": None,
                },
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            logger.debug("proton_config: copilot_answer failed", exc_info=True)
            return None
        if not isinstance(data, dict):
            return None
        answer = data.get("answer")
        if isinstance(answer, str) and answer.strip():
            return answer
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_proton_client.py -k copilot_answer -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add agent/app/clients/proton.py agent/tests/test_proton_client.py
git commit -m "feat(agent): ProtonConfigClient.copilot_answer for KB-grounded replies"
```

---

### Task 3: `_build_thread` helper + `_fetch_messages`/`_build_context` refactor

**Files:**
- Modify: `agent/app/services/orchestrator.py` (extract `_fetch_messages`, make `_build_context` take a list, add `_build_thread`)
- Test: `agent/tests/test_orchestrator.py` (append pure-function tests)

**Interfaces:**
- Produces:
  - `async def _fetch_messages(conversation_id: int) -> list[dict]`
  - `def _build_context(message_list: list[dict]) -> str` (was `async def _build_context(conversation_id)`)
  - `def _build_thread(message_list: list[dict]) -> list[dict]`

- [ ] **Step 1: Write the failing test.** Append to `agent/tests/test_orchestrator.py`:

```python
def test_build_thread_maps_roles_and_drops_noise():
    from app.services.orchestrator import _build_thread
    messages = [
        {"message_type": 0, "content": "hello", "private": False},
        {"message_type": 1, "content": "Hi! How can I help?", "private": False},
        {"message_type": 1, "content": "internal note", "private": True},   # dropped: private
        {"message_type": 2, "content": "Assigned to X", "private": False},  # dropped: activity
        {"message_type": 0, "content": "  ", "private": False},              # dropped: empty
        {"message_type": 0, "content": "tanya pasal proton x50", "private": False},
    ]
    assert _build_thread(messages) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi! How can I help?"},
        {"role": "user", "content": "tanya pasal proton x50"},
    ]


def test_build_thread_empty_when_nothing_qualifies():
    from app.services.orchestrator import _build_thread
    assert _build_thread([{"message_type": 2, "content": "act", "private": False}]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_orchestrator.py -k build_thread -v`
Expected: FAIL — `ImportError: cannot import name '_build_thread'`

- [ ] **Step 3: Refactor + add helper.** In `agent/app/services/orchestrator.py`, replace the current `async def _build_context(conversation_id: int) -> str:` (which fetches its own messages) with a fetch helper + two pure builders:

```python
async def _fetch_messages(conversation_id: int) -> list[dict]:
    """Fetch the conversation's messages once (fresh, not from the trigger
    payload). Shared by _build_context and _build_thread."""
    chatwoot = get_chatwoot_client()
    raw_messages = await chatwoot.get_messages(conversation_id)
    if isinstance(raw_messages, dict):
        return raw_messages.get("payload") or []
    return raw_messages or []


def _build_context(message_list: list[dict]) -> str:
    """Last 20 non-private messages plus the contact's name/email."""
    lines: list[str] = []
    contact_name: str | None = None
    contact_email: str | None = None
    for message in message_list[-20:]:
        if message.get("private"):
            continue
        sender = message.get("sender") or {}
        sender_name = sender.get("name", "Unknown")
        text = message.get("content") or ""
        lines.append(f"{sender_name}: {text}")

        if message.get("message_type") == 0 and contact_name is None:
            contact_name = sender.get("name")
            contact_email = sender.get("email")

    header = f"Customer: {contact_name or 'unknown'} <{contact_email or 'unknown'}>"
    transcript = "\n".join(lines) or "(no messages)"
    return f"{header}\n\n{transcript}"


def _build_thread(message_list: list[dict]) -> list[dict]:
    """Map the last 20 non-private Chatwoot messages to copilot thread items:
    incoming (type 0) → user, outgoing (type 1) → assistant. Drops private,
    activity/template, and empty-content messages. Order preserved."""
    thread: list[dict] = []
    for message in message_list[-20:]:
        if message.get("private"):
            continue
        mtype = message.get("message_type")
        if mtype == 0:
            role = "user"
        elif mtype == 1:
            role = "assistant"
        else:
            continue
        content = (message.get("content") or "").strip()
        if not content:
            continue
        thread.append({"role": role, "content": content})
    return thread
```

- [ ] **Step 4: Update the one caller.** In `_process_conversation`, replace the existing block:

```python
    try:
        context = await _build_context(conversation_id)
    except httpx.HTTPError:
        logger.exception(
            "orchestrator: failed to fetch messages for conversation %s, skipping",
            conversation_id,
        )
        return
```

with (fetch once, build context from the list; keep `message_list` for Task 4):

```python
    try:
        message_list = await _fetch_messages(conversation_id)
    except httpx.HTTPError:
        logger.exception(
            "orchestrator: failed to fetch messages for conversation %s, skipping",
            conversation_id,
        )
        return
    context = _build_context(message_list)
```

- [ ] **Step 5: Run the full orchestrator suite to verify no regression + new tests pass**

Run: `pytest tests/test_orchestrator.py tests/test_orchestrator_proton.py tests/test_orchestrator_lifecycle.py -v`
Expected: PASS (all, including the two new `build_thread` tests)

- [ ] **Step 6: Commit**

```bash
git add agent/app/services/orchestrator.py agent/tests/test_orchestrator.py
git commit -m "refactor(agent): single message fetch + _build_thread helper"
```

---

### Task 4: Graft copilot answer into the `send_reply` path

**Files:**
- Modify: `agent/app/services/orchestrator.py` (`_process_conversation`, after `_log_decision`)
- Test: `agent/tests/test_orchestrator_proton.py` (append tests)

**Interfaces:**
- Consumes: `settings.kb_grounded_replies` (Task 1), `ProtonConfigClient.copilot_answer` (Task 2), `_build_thread` + `message_list` (Task 3), existing `proton` + `inbox_id` locals in `_process_conversation`.
- Produces: KB-grounded `decision.args["text"]` for `send_reply` when the flag is on and the copilot returns a non-empty answer.

- [ ] **Step 1: Write the failing tests.** Append to `agent/tests/test_orchestrator_proton.py` (uses the file's existing helpers: `_make_proton_client`, `CONVERSATION_RESPONSE` with `inbox_id: 7`, `INBOXES_WITH_MODE["auto"]`, `CHATWOOT`, `PROTON`, `_payload`, `_fast_debounce`). Note `PROTON` in this file is the proton base URL constant; confirm it equals the client's base_url:

```python
@respx.mock
async def test_send_reply_uses_copilot_answer_when_flag_on(monkeypatch):
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "kb_grounded_replies", True)
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(200, json=CONVERSATION_RESPONSE)
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"payload": [
            {"message_type": 0, "content": "tanya pasal proton x50", "private": False,
             "sender": {"name": "Cust", "email": "c@x.my"}},
        ]})
    )
    respx.get(f"{PROTON}/kb/inboxes").mock(return_value=httpx.Response(200, json=INBOXES_WITH_MODE["auto"]))
    copilot = respx.post(f"{PROTON}/assist/copilot").mock(
        return_value=httpx.Response(200, json={"answer": "The Proton X50 is a compact SUV.", "sources": []})
    )
    create_message = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    monkeypatch.setattr(gemini, "decide", _stub_decide(
        gemini.Decision("send_reply", {"text": "LOCAL DRAFT"}, None, 5)
    ))
    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    task = await orchestrator.handle_bot_event(_payload())
    await task

    assert copilot.called
    import json as _json
    posted = _json.loads(create_message.calls.last.request.content)
    assert posted["content"] == "The Proton X50 is a compact SUV."
    await client.aclose()


@respx.mock
async def test_send_reply_falls_back_to_local_when_copilot_none(monkeypatch):
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "kb_grounded_replies", True)
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(200, json=CONVERSATION_RESPONSE)
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"payload": [
            {"message_type": 0, "content": "hi", "private": False, "sender": {"name": "C"}},
        ]})
    )
    respx.get(f"{PROTON}/kb/inboxes").mock(return_value=httpx.Response(200, json=INBOXES_WITH_MODE["auto"]))
    respx.post(f"{PROTON}/assist/copilot").mock(return_value=httpx.Response(500))
    create_message = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    monkeypatch.setattr(gemini, "decide", _stub_decide(
        gemini.Decision("send_reply", {"text": "LOCAL DRAFT"}, None, 5)
    ))
    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    task = await orchestrator.handle_bot_event(_payload())
    await task

    import json as _json
    posted = _json.loads(create_message.calls.last.request.content)
    assert posted["content"] == "LOCAL DRAFT"
    await client.aclose()


@respx.mock
async def test_flag_off_does_not_call_copilot(monkeypatch):
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "kb_grounded_replies", False)
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(200, json=CONVERSATION_RESPONSE)
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"payload": [
            {"message_type": 0, "content": "hi", "private": False, "sender": {"name": "C"}},
        ]})
    )
    respx.get(f"{PROTON}/kb/inboxes").mock(return_value=httpx.Response(200, json=INBOXES_WITH_MODE["auto"]))
    copilot = respx.post(f"{PROTON}/assist/copilot").mock(return_value=httpx.Response(200, json={"answer": "X"}))
    create_message = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    monkeypatch.setattr(gemini, "decide", _stub_decide(
        gemini.Decision("send_reply", {"text": "LOCAL DRAFT"}, None, 5)
    ))
    client = _make_proton_client()
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: client)

    task = await orchestrator.handle_bot_event(_payload())
    await task

    assert not copilot.called
    import json as _json
    posted = _json.loads(create_message.calls.last.request.content)
    assert posted["content"] == "LOCAL DRAFT"
    await client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_orchestrator_proton.py -k "copilot or flag_off" -v`
Expected: FAIL — copilot route not called / posted content is "LOCAL DRAFT" where the KB answer was expected (graft not present yet).

- [ ] **Step 3: Add the graft.** In `agent/app/services/orchestrator.py`, in `_process_conversation`, immediately after `await _log_decision(conversation_id, decision)` and before the `try:`/`_execute_decision` block, insert:

```python
    # KB-grounded reply: for a plain answer, source the text from the backend
    # copilot (same KB + assistant as the website) instead of the local draft.
    # Router (send_reply vs escalate/handoff) is unchanged; fail-open to draft.
    if (
        settings.kb_grounded_replies
        and decision.action == "send_reply"
        and proton is not None
        and inbox_id is not None
    ):
        thread = _build_thread(message_list)
        if thread:
            answer = await proton.copilot_answer(
                f"chatwoot-conv-{conversation_id}", thread, inbox_id
            )
            if answer:
                decision.args["text"] = answer
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_orchestrator_proton.py -k "copilot or flag_off" -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full agent suite (no regressions)**

Run: `pytest`
Expected: PASS (all tests green)

- [ ] **Step 6: Commit**

```bash
git add agent/app/services/orchestrator.py agent/tests/test_orchestrator_proton.py
git commit -m "feat(agent): KB-ground send_reply via backend copilot (flag-gated, fail-open)"
```

---

## Deploy (after all tasks green — manual/ops, needs the VM)

Not a code task; executed against the Proton tenant on VM `crm-ticketing`.

1. On the VM (`/opt/platform`): rebuild the agent image —
   `docker compose -p proton -f deploy/docker-compose.tenant.yml --env-file deploy/tenants/proton.env build agent`
2. Set `KB_GROUNDED_REPLIES=true` in `deploy/tenants/proton.env`.
3. Recreate: `docker compose -p proton -f deploy/docker-compose.tenant.yml --env-file deploy/tenants/proton.env up -d agent`
4. Smoke: from a fresh WhatsApp conversation, ask a product question → expect a KB-grounded public answer.

## Self-Review notes

- Spec coverage: config flag (T1), copilot client (T2), thread builder + single-fetch refactor (T3), orchestrator graft + fail-open + escalate/handoff-untouched (T4), tests (each task), deploy (section). ✓
- Escalate/handoff untouched: the graft guards on `decision.action == "send_reply"`, so other actions skip the copilot entirely — covered implicitly (no copilot route for those paths) and by `test_flag_off`. ✓
- `decision.args` is a mutable dict on the `Decision` NamedTuple, so `decision.args["text"] = answer` is valid without rebuilding the tuple. ✓
