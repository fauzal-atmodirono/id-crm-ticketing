# Idle-warning {{minutes}} param + native-disclaimer switch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the idle-warning message show the configured close-grace (`{{minutes}}`), and switch the WhatsApp disclaimer to Chatwoot's native channel greeting without re-introducing the first-turn masking bug.

**Architecture:** Two small, independent changes in the `agent/` service. (A) a pure token-render helper used at the scanner's `warn` action; (B) the brain-swap orchestrator skips the native greeting (by content match against the inbox's `greeting_message`) when computing the customer's turn, mirroring the existing `proton_lifecycle` marker skip. Plus one per-tenant env flip on the proton VM.

**Tech Stack:** Python 3.12, pytest (asyncio_mode=auto), respx; agent service under `agent/`.

**Spec:** `docs/superpowers/specs/2026-07-27-idle-warning-param-and-native-disclaimer-design.md`

## Global Constraints

- Agent-only code change. No CSAT change, no routing change. No change to the code default of `lifecycle_disclaimer_enabled` (proton flips it via env, deploy step).
- Token is the literal `{{minutes}}`. `minutes` = the effective close **grace** (in-hours or out-of-hours), i.e. how long the customer has after the warning.
- Fail-open + default-preserving: a message without `{{minutes}}` is sent unchanged; an empty `greeting_text` (or a failed inbox fetch) preserves today's behavior exactly.
- Native-greeting skip is an **exact stripped content match** against `inbox.greeting_message` — documented limitation: won't match if the greeting uses Chatwoot variables.
- Run agent tests with the venv binary: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest <path> -v` (bare `pytest` is not on PATH). asyncio_mode=auto — async tests need no decorator.
- Commit only the files each task changes, by explicit path (a concurrent session has committed on dev-yuda before; never `git add -A`).

---

### Task 1: Idle-warning `{{minutes}}` parameter

**Files:**
- Modify: `agent/app/services/lifecycle.py` (`IDLE_WARNING_DEFAULT` + new `render_idle_warning`)
- Modify: `agent/app/services/lifecycle_scanner.py` (`warn` action renders the grace)
- Test: `agent/tests/test_lifecycle_idle_warning_param.py` (new)

**Interfaces:**
- Consumes: `lifecycle._resolve_message`, `lifecycle.IDLE_WARNING_DEFAULT`, the `grace` local already computed in `lifecycle_scanner._process_one`.
- Produces: `lifecycle.render_idle_warning(text: str, minutes: int) -> str`.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_lifecycle_idle_warning_param.py`:

```python
"""The idle-warning message interpolates the effective close grace via {{minutes}}."""

from app.services import lifecycle


def test_render_replaces_minutes_token():
    out = lifecycle.render_idle_warning(
        "Your chat will close in {{minutes}} minutes if we do not hear from you.", 10
    )
    assert out == "Your chat will close in 10 minutes if we do not hear from you."


def test_render_without_token_is_unchanged():
    assert lifecycle.render_idle_warning("No token here.", 7) == "No token here."


def test_default_warning_contains_the_token():
    # The shipped default must carry the token so the value is dynamic.
    assert "{{minutes}}" in lifecycle.IDLE_WARNING_DEFAULT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest tests/test_lifecycle_idle_warning_param.py -v`
Expected: FAIL — `render_idle_warning` doesn't exist / default lacks the token.

- [ ] **Step 3: Implement in `lifecycle.py`**

Change the default constant:
```python
IDLE_WARNING_DEFAULT = "Your chat will close in {{minutes}} minutes if we do not hear from you."
```
Add the helper (place it near `_resolve_message`):
```python
def render_idle_warning(text: str, minutes: int) -> str:
    """Replace the {{minutes}} token with the effective close-grace value.
    A message without the token is returned unchanged (backward compatible)."""
    return text.replace("{{minutes}}", str(minutes))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest tests/test_lifecycle_idle_warning_param.py -v`
Expected: PASS.

- [ ] **Step 5: Wire it into the scanner's `warn` action**

In `agent/app/services/lifecycle_scanner.py::_process_one`, the `warn` branch currently reads:
```python
    if action == "warn":
        await lifecycle._post(
            conversation_id,
            lifecycle._resolve_message(msgs, "idle_warning", lifecycle.IDLE_WARNING_DEFAULT),
        )
```
Replace the `_post(...)` call so the message is rendered with the effective `grace` (already computed above as the local `grace`):
```python
    if action == "warn":
        warning = lifecycle._resolve_message(
            msgs, "idle_warning", lifecycle.IDLE_WARNING_DEFAULT
        )
        await lifecycle._post(
            conversation_id, lifecycle.render_idle_warning(warning, grace)
        )
```
(Leave the subsequent `transition(... IDLE_WARNED ...)` and `_mirror_state` lines unchanged.)

- [ ] **Step 6: Add a scanner test proving the grace value is rendered**

Append to `agent/tests/test_lifecycle_scanner.py` (it already has the `wired` fixture: one WhatsApp conv idle 12 min, warn default 10, so it warns):

```python
async def test_warn_message_uses_per_inbox_grace(wired, monkeypatch):
    # Per-inbox close grace = 7 -> the warning must say "7 minutes".
    from app.services import lifecycle

    async def _timing(inbox_id):
        return {"idle_warn_minutes": None, "idle_close_grace_minutes": 7,
                "idle_close_out_of_hours_grace_minutes": None, "confirm_grace_minutes": None}

    monkeypatch.setattr(lifecycle, "_fetch_lifecycle_timing", _timing)
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")
    await lifecycle_scanner.scan_once()
    # The warning is the first create_message call in the warn action.
    posted = [c.args[1] for c in wired.create_message.await_args_list]
    assert any("7 minutes" in str(m) for m in posted), posted
```

- [ ] **Step 7: Run the scanner + lifecycle suites**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest tests/test_lifecycle_scanner.py tests/test_lifecycle_idle_warning_param.py -v`
Expected: PASS (new tests + existing scanner tests still green). If a pre-existing scanner test asserted the exact old "5 minutes" string, update its expectation to the rendered value.

- [ ] **Step 8: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add agent/app/services/lifecycle.py agent/app/services/lifecycle_scanner.py agent/tests/test_lifecycle_idle_warning_param.py agent/tests/test_lifecycle_scanner.py
git commit -m "feat(agent): render {{minutes}} in idle-warning with the effective close grace"
```

---

### Task 2: Native-disclaimer greeting skip (no first-turn masking)

**Files:**
- Modify: `agent/app/services/orchestrator.py` (`_latest_incoming_text` gains `greeting_text`; `_process_via_chat_agent` fetches + passes it)
- Test: `agent/tests/test_orchestrator_lifecycle_masking.py` (extend)

**Interfaces:**
- Consumes: `_latest_incoming_text(message_list)` (currently 1 arg; becomes 2 with a defaulted `greeting_text=""`); the `chatwoot` client + `inbox_id` already params of `_process_via_chat_agent`; `chatwoot.get_inbox(inbox_id)` returns a dict with `greeting_enabled` (bool) and `greeting_message` (str).
- Produces: `_latest_incoming_text(message_list: list[dict], greeting_text: str = "") -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_orchestrator_lifecycle_masking.py` (it already defines `_in` and `_out` helpers):

```python
def test_native_greeting_is_skipped_by_content_match():
    # halo -> native greeting (outgoing, no marker). Passing the greeting text
    # lets the orchestrator skip it so "halo" still reaches the backend.
    greeting = "DISCLAIMER: native greeting text"
    messages = [_in("halo"), _out(greeting)]
    assert orchestrator._latest_incoming_text(messages, greeting) == "halo"


def test_non_greeting_outgoing_still_bounds_turn():
    # A real bot reply (not the greeting) still bounds the turn even when a
    # greeting_text is supplied.
    messages = [_in("halo"), _out("Hi! How can I help?"), _in("spec?")]
    assert orchestrator._latest_incoming_text(messages, "some greeting") == "spec?"


def test_empty_greeting_text_preserves_behavior():
    # No greeting supplied -> unchanged: the outgoing message bounds the turn.
    messages = [_in("halo"), _out("Hi! How can I help?")]
    assert orchestrator._latest_incoming_text(messages, "") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest tests/test_orchestrator_lifecycle_masking.py -v`
Expected: `test_native_greeting_is_skipped_by_content_match` FAILS (currently `_latest_incoming_text` takes 1 arg / breaks on the greeting); the other two may error on the arity too.

- [ ] **Step 3: Extend `_latest_incoming_text`**

In `agent/app/services/orchestrator.py`, replace the function body so it accepts `greeting_text` and skips a matching outgoing message. The current function is:
```python
def _latest_incoming_text(message_list: list[dict]) -> str:
    """The trailing run of incoming customer messages (since the last non-private
    outgoing message), joined into one turn for the backend agent. The backend
    owns the rest of the multi-turn history keyed by the crm- session id, so we
    only send what the customer has said since the bot last spoke."""
    texts: list[str] = []
    for message in reversed(message_list):
        if message.get("private"):
            continue
        # Lifecycle system messages (disclaimer, idle warn/close, resolution
        # prompt, surveys) are outgoing+public but are NOT the bot's reply to the
        # customer. They must not bound the turn, or the disclaimer posted right
        # after the customer's first message would mask it and /chat/turn would
        # never run. lifecycle stamps them with this marker.
        ca = message.get("content_attributes")
        if isinstance(ca, dict) and ca.get("proton_lifecycle"):
            continue
        mtype = message.get("message_type")
        if mtype == 0:  # incoming (customer)
            content = (message.get("content") or "").strip()
            if content:
                texts.append(content)
        elif mtype == 1:  # outgoing (bot/agent) → older than the last reply
            break
    return "\n".join(reversed(texts))
```
Replace it with:
```python
def _latest_incoming_text(message_list: list[dict], greeting_text: str = "") -> str:
    """The trailing run of incoming customer messages (since the last non-private
    outgoing message), joined into one turn for the backend agent. The backend
    owns the rest of the multi-turn history keyed by the crm- session id, so we
    only send what the customer has said since the bot last spoke.

    Two kinds of outgoing message are NOT the bot's reply and must not bound the
    turn: our own lifecycle notices (marked content_attributes.proton_lifecycle)
    and Chatwoot's native channel greeting (content == the inbox greeting_text)."""
    greeting = (greeting_text or "").strip()
    texts: list[str] = []
    for message in reversed(message_list):
        if message.get("private"):
            continue
        ca = message.get("content_attributes")
        if isinstance(ca, dict) and ca.get("proton_lifecycle"):
            continue
        content = (message.get("content") or "").strip()
        mtype = message.get("message_type")
        if mtype == 1 and greeting and content == greeting:
            continue  # native channel greeting — not a real bot reply
        if mtype == 0:  # incoming (customer)
            if content:
                texts.append(content)
        elif mtype == 1:  # outgoing (bot/agent) → older than the last reply
            break
    return "\n".join(reversed(texts))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest tests/test_orchestrator_lifecycle_masking.py -v`
Expected: PASS (all cases, including the pre-existing marker tests).

- [ ] **Step 5: Fetch + pass the greeting in `_process_via_chat_agent`**

In `agent/app/services/orchestrator.py::_process_via_chat_agent`, the body starts with:
```python
    text = _latest_incoming_text(message_list)
```
Replace that single line with a fail-open inbox fetch that supplies the greeting:
```python
    greeting_text = ""
    if inbox_id is not None:
        try:
            inbox = await chatwoot.get_inbox(inbox_id)
            if isinstance(inbox, dict) and inbox.get("greeting_enabled"):
                greeting_text = inbox.get("greeting_message") or ""
        except Exception:
            greeting_text = ""  # fail-open: no greeting skip, behave as before
    text = _latest_incoming_text(message_list, greeting_text)
```
(`chatwoot` and `inbox_id` are already parameters of `_process_via_chat_agent`.)

- [ ] **Step 6: Add an integration test for the fetch+skip path**

Append to `agent/tests/test_orchestrator_lifecycle_masking.py`:

```python
import pytest
from unittest.mock import AsyncMock


async def test_process_via_chat_agent_skips_native_greeting(monkeypatch):
    # halo followed by the native greeting: with the inbox greeting fetched,
    # the backend /chat/turn must be called with "halo" (not skipped).
    greeting = "DISCLAIMER: welcome"
    message_list = [_in("halo"), _out(greeting)]

    chatwoot = AsyncMock()
    chatwoot.get_inbox.return_value = {
        "greeting_enabled": True,
        "greeting_message": greeting,
        "channel_type": "Channel::Whatsapp",
    }

    captured = {}
    proton = AsyncMock()

    async def _chat_turn(session_id, text, inbox_id=None):
        captured["text"] = text
        return {"kind": "reply", "reply": "Hai!"}

    proton.chat_turn.side_effect = _chat_turn
    monkeypatch.setattr(orchestrator, "get_proton_config_client", lambda: proton)

    await orchestrator._process_via_chat_agent(
        conversation_id=70,
        message_list=message_list,
        effective_mode="auto",
        chatwoot=chatwoot,
        handoff_message="",
        inbox_id=7,
    )
    assert captured.get("text") == "halo"
```

If the real `_process_via_chat_agent` signature or `proton.chat_turn` shape differs from the above (verify against the current source before writing), adapt the mock to match — the assertion that matters is that `chat_turn` receives `"halo"`. If wiring a full end-to-end mock proves brittle, it is acceptable to drop this Step-6 integration test and rely on the Step-1 unit tests plus the live VM smoke — note that choice in the task report.

- [ ] **Step 7: Run the full agent suite**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 8: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add agent/app/services/orchestrator.py agent/tests/test_orchestrator_lifecycle_masking.py
git commit -m "fix(agent): skip native channel greeting so it doesn't mask the first turn"
```

---

### Task 3: Deploy to the proton VM + flip the disclaimer env

**Files:** none (deploy + env). This task has no unit tests; it ends with a live smoke gate.

**Interfaces:** consumes the code from Tasks 1–2 (already committed on dev-yuda).

- [ ] **Step 1: Sync the changed agent source to the VM**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
tar --exclude='__pycache__' -czf /tmp/agent-warn-greeting.tgz \
  agent/app/services/lifecycle.py \
  agent/app/services/lifecycle_scanner.py \
  agent/app/services/orchestrator.py
gcloud compute scp /tmp/agent-warn-greeting.tgz crm-ticketing:/tmp/agent-warn-greeting.tgz --zone asia-southeast2-a
```

- [ ] **Step 2: Back up, extract, flip the env, rebuild**

```bash
gcloud compute ssh crm-ticketing --zone asia-southeast2-a --command '
set -e
TS=$(date +%s)
tar -czf /tmp/agent-src-backup-$TS.tgz -C /opt/platform agent/app/services/lifecycle.py agent/app/services/lifecycle_scanner.py agent/app/services/orchestrator.py
tar -xzf /tmp/agent-warn-greeting.tgz -C /opt/platform
# Flip disclaimer off (native greeting owns it). Idempotent set-or-append.
sudo sed -i "/^LIFECYCLE_DISCLAIMER_ENABLED=/d" /opt/platform/deploy/tenants/proton.env
echo "LIFECYCLE_DISCLAIMER_ENABLED=false" | sudo tee -a /opt/platform/deploy/tenants/proton.env >/dev/null
cd /opt/platform/deploy
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --build agent 2>&1 | tail -5
'
```

- [ ] **Step 3: Verify the deploy**

```bash
gcloud compute ssh crm-ticketing --zone asia-southeast2-a --command '
echo "health: $(docker inspect proton-agent --format "{{.State.Health.Status}}")"
docker exec proton-agent sh -c "grep -c render_idle_warning /app/app/services/lifecycle.py; grep -c greeting_text /app/app/services/orchestrator.py"
sudo grep LIFECYCLE_DISCLAIMER_ENABLED /opt/platform/deploy/tenants/proton.env
'
```
Expected: health=healthy; `render_idle_warning` ≥1; `greeting_text` ≥1; `LIFECYCLE_DISCLAIMER_ENABLED=false`. Also confirm in the Chatwoot inbox-3 UI that "Enable channel greeting" is ON with the disclaimer text.

- [ ] **Step 4: Live smoke (human)**

Send a fresh WhatsApp "halo" to the Proton number. Expected:
1. The native greeting/disclaimer arrives **once** (not twice).
2. The AI replies to "halo" (first turn no longer masked).
3. Leave it idle past the warn threshold → the warning shows the configured grace, e.g. "…close in 2 minutes…" (matches the inbox's close-grace setting).

---

## Self-Review

**Spec coverage:**
- Component A (idle-warning `{{minutes}}` render, default carries token, scanner uses effective grace, persona field supported) → Task 1. ✓
- Component B (disable custom disclaimer via env; orchestrator skips native greeting by content match; keep `proton_lifecycle` marker skip; fail-open) → Task 2 (code) + Task 3 Step 2 (env). ✓
- Non-goals honored: no CSAT change, no routing change, no code-default change to `lifecycle_disclaimer_enabled`. ✓
- Deploy + smoke → Task 3. ✓

**Placeholder scan:** No TBD/TODO. Every code step has concrete content. Task 2 Step 6 gives concrete test code with an explicit, bounded fallback (drop the integration test, keep unit tests + smoke) rather than a vague "handle it".

**Type consistency:** `render_idle_warning(text: str, minutes: int) -> str` used identically in Task 1 Steps 3/5/6. `_latest_incoming_text(message_list, greeting_text="")` defined in Task 2 Step 3 and called with that arity in Steps 1/5/6. `grace` is the existing scanner local. `chatwoot.get_inbox` returns a dict with `greeting_enabled`/`greeting_message` (consistent with the existing `get_inbox` usage in `_format_reply_for_channel`).
