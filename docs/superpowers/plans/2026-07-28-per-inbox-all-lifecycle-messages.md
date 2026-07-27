# Per-inbox override for all auto-close flow messages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all seven auto-close flow customer messages editable per-inbox in the native inbox "Inactivity & auto-close" section (currently only the idle-warning is).

**Architecture:** Generalize the existing single per-inbox `idle_warning_message` field into seven message fields in `InboxTimingStore` (+ `/timing` API + agent client). A single agent resolver `per-inbox → persona → SOP default` is applied at every posting site (scanner warn/close, lifecycle resolution/survey). The fork patch 0023 gains six more textareas.

**Tech Stack:** Python 3.12, FastAPI, pydantic, Firestore, pytest (asyncio_mode=auto), respx; Vue 3 SPA fork patch (Chatwoot v4.15.1).

**Spec:** `docs/superpowers/specs/2026-07-28-per-inbox-all-lifecycle-messages-design.md`

## Global Constraints

- The 7 per-inbox message store keys (order matters for `MESSAGE_KEYS`): `idle_warning_message`, `idle_close_message`, `resolution_prompt_message`, `assign_agent_message`, `survey_ai_message`, `survey_agent_message`, `thanks_message`. Each a `str`.
- Persona key ↔ per-inbox key mapping: per-inbox key = `f"{persona_key}_message"` (persona keys: `idle_warning`, `idle_close`, `resolution_prompt`, `assign_agent`, `survey_ai`, `survey_agent`, `thanks`).
- Precedence for each message: per-inbox (non-empty str) → persona `_resolve_message(msgs, key, default)` → SOP default. Only `idle_warning` gets `render_idle_warning({{minutes}}, grace)`.
- Message fields `max_length=2000`. Fail-open + backward-compatible: empty per-inbox → persona → default (byte-identical to today).
- No change to timers, `inactivity_enabled` toggle, or the 4 int `TIMING_KEYS` (int-guarded) / `ENABLED_KEY` (bool-guarded).
- Backend tests: `cd backend/apps/backend && .venv/bin/pytest <path> -v`. Agent tests: `cd agent && .venv/bin/pytest <path> -v` (bare `pytest` not on PATH). asyncio_mode=auto.
- Commit only each task's files, by explicit path (never `git add -A`).

---

### Task 1: Backend store — seven message fields

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/adapters/inbox_timing_store.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/adapters/test_inbox_timing_store.py` (extend)

**Interfaces:**
- Produces: `MESSAGE_KEYS: tuple[str, ...]` (the 7 keys above). `_clean_timing` round-trips any subset of them (each str).

- [ ] **Step 1: Write the failing test**

Append to `test_inbox_timing_store.py`:
```python
from chatbot.features.chat.adapters.inbox_timing_store import MESSAGE_KEYS


def test_message_keys_are_the_seven():
    assert MESSAGE_KEYS == (
        "idle_warning_message", "idle_close_message", "resolution_prompt_message",
        "assign_agent_message", "survey_ai_message", "survey_agent_message", "thanks_message",
    )


async def test_roundtrip_all_messages():
    store = InMemoryInboxTimingStore()
    payload = {k: f"msg-{k}" for k in MESSAGE_KEYS}
    payload["idle_warn_minutes"] = 3
    await store.set(7, payload)
    got = await store.get(7)
    for k in MESSAGE_KEYS:
        assert got[k] == f"msg-{k}"
    assert got["idle_warn_minutes"] == 3


async def test_message_wrong_type_dropped():
    store = InMemoryInboxTimingStore()
    await store.set(7, {"idle_close_message": 123, "thanks_message": "ok"})
    assert await store.get(7) == {"thanks_message": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/adapters/test_inbox_timing_store.py -v`
Expected: FAIL (`MESSAGE_KEYS` missing; only `idle_warning_message` round-trips).

- [ ] **Step 3: Implement**

In `inbox_timing_store.py`, replace the single-message constant + handling. Where `MESSAGE_KEY = "idle_warning_message"` is defined (line ~39), replace with:
```python
MESSAGE_KEYS: tuple[str, ...] = (
    "idle_warning_message",
    "idle_close_message",
    "resolution_prompt_message",
    "assign_agent_message",
    "survey_ai_message",
    "survey_agent_message",
    "thanks_message",
)
```
In `_clean_timing`, replace the single-message block (the `msg = data.get(MESSAGE_KEY)` / `if isinstance(msg, str): out[MESSAGE_KEY] = msg` lines) with:
```python
    for mk in MESSAGE_KEYS:
        mv = data.get(mk)
        if isinstance(mv, str):
            out[mk] = mv
```
(Leave the `TIMING_KEYS` int guard and `ENABLED_KEY` bool guard unchanged. If any other code imports `MESSAGE_KEY`, update it to `MESSAGE_KEYS[0]` — grep `MESSAGE_KEY` repo-wide; the router import in the next task will switch to `MESSAGE_KEYS`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/adapters/test_inbox_timing_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add backend/apps/backend/src/chatbot/features/chat/adapters/inbox_timing_store.py backend/apps/backend/src/chatbot/features/chat/adapters/test_inbox_timing_store.py
git commit -m "feat(backend): store all seven per-inbox auto-close messages"
```

---

### Task 2: Backend router — seven message body/response fields

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/kb_inboxes_router.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_kb_inboxes_timing.py` (extend)

**Interfaces:**
- Consumes: `MESSAGE_KEYS` (Task 1).
- Produces: `InboxTimingBody` with all 7 message fields; `_normalize_timing` returns them.

- [ ] **Step 1: Write the failing test**

Append to `test_kb_inboxes_timing.py`:
```python
def test_all_message_fields_roundtrip():
    client, _ = _client()
    body = {
        "idle_close_message": "Closed.", "resolution_prompt_message": "Resolved? Y/N",
        "assign_agent_message": "Assigning…", "survey_ai_message": "Rate AI",
        "survey_agent_message": "Rate agent", "thanks_message": "Ta",
    }
    assert client.put("/kb/inboxes/5/timing", json=body, headers=_H).status_code == 200
    got = client.get("/kb/inboxes/5/timing", headers=_H).json()
    for k, v in body.items():
        assert got[k] == v


def test_message_field_max_length_422():
    client, _ = _client()
    r = client.put("/kb/inboxes/5/timing", json={"thanks_message": "x" * 2001}, headers=_H)
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_kb_inboxes_timing.py -v`
Expected: FAIL (fields absent).

- [ ] **Step 3: Implement**

In `kb_inboxes_router.py`:
- Update the import to pull `MESSAGE_KEYS` (replace `MESSAGE_KEY` if imported): `from chatbot.features.chat.adapters.inbox_timing_store import TIMING_KEYS, MESSAGE_KEYS, ENABLED_KEY`.
- In `InboxTimingBody`, next to the existing `idle_warning_message`, add the six more:
```python
    idle_close_message: str | None = Field(default=None, max_length=2000)
    resolution_prompt_message: str | None = Field(default=None, max_length=2000)
    assign_agent_message: str | None = Field(default=None, max_length=2000)
    survey_ai_message: str | None = Field(default=None, max_length=2000)
    survey_agent_message: str | None = Field(default=None, max_length=2000)
    thanks_message: str | None = Field(default=None, max_length=2000)
```
- In `_normalize_timing`, replace the single `out[MESSAGE_KEY] = stored.get(MESSAGE_KEY)` line with a loop, keeping the enabled line:
```python
    for mk in MESSAGE_KEYS:
        out[mk] = stored.get(mk)
    out[ENABLED_KEY] = stored.get(ENABLED_KEY)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_kb_inboxes_timing.py -v`
Expected: PASS (existing timing tests still green — the `_ALL_NULL`/roundtrip helpers already normalize the extra keys via `_normalize_timing`; if an exact-dict assertion breaks, extend its expected dict with the six new `None` keys).

- [ ] **Step 5: Commit**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add backend/apps/backend/src/chatbot/features/chat/kb_inboxes_router.py backend/apps/backend/src/chatbot/features/chat/test_kb_inboxes_timing.py
git commit -m "feat(backend): serve all seven per-inbox auto-close messages via /timing"
```

---

### Task 3: Agent client — return the seven message keys

**Files:**
- Modify: `agent/app/clients/proton.py` (`get_assistant_lifecycle_timing`)
- Test: `agent/tests/test_proton_client.py` (extend)

**Interfaces:**
- Produces: `get_assistant_lifecycle_timing` result includes all 7 message keys (str-or-None).

- [ ] **Step 1: Write the failing test**

Append to `agent/tests/test_proton_client.py`:
```python
_MSG_ROW = {"inboxes": [{"inbox_id": 50,
    "idle_close_message": "Closed.", "thanks_message": "Ta", "survey_ai_message": ""}]}


@respx.mock
async def test_lifecycle_timing_all_message_keys():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(return_value=httpx.Response(200, json=_MSG_ROW))
    client = _make_client()
    t = await client.get_assistant_lifecycle_timing(50)
    assert t["idle_close_message"] == "Closed."
    assert t["thanks_message"] == "Ta"
    assert t["survey_ai_message"] == ""          # empty string preserved (resolver treats as unset)
    assert t["assign_agent_message"] is None     # absent -> None
    assert t["resolution_prompt_message"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest tests/test_proton_client.py -k all_message_keys -v`
Expected: FAIL (client only returns `idle_warning_message`).

- [ ] **Step 3: Implement**

In `agent/app/clients/proton.py`, add a module constant near `_LIFECYCLE_TIMING_KEYS`:
```python
_LIFECYCLE_MESSAGE_KEYS = (
    "idle_warning_message", "idle_close_message", "resolution_prompt_message",
    "assign_agent_message", "survey_ai_message", "survey_agent_message", "thanks_message",
)
```
In `get_assistant_lifecycle_timing`, replace the two lines that read `idle_warning_message` (and the `inactivity_enabled` block stays) with a loop over the message keys:
```python
            for mk in _LIFECYCLE_MESSAGE_KEYS:
                mv = row.get(mk)
                result[mk] = mv if isinstance(mv, str) else None
            en = row.get("inactivity_enabled")
            result["inactivity_enabled"] = en if isinstance(en, bool) else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest tests/test_proton_client.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add agent/app/clients/proton.py agent/tests/test_proton_client.py
git commit -m "feat(agent): read all seven per-inbox auto-close messages"
```

---

### Task 4: Agent — unified resolver + wire every posting site

**Files:**
- Modify: `agent/app/services/lifecycle.py` (`_resolve_lifecycle_message` + `handle_lifecycle_reply` + `on_human_resolved`)
- Modify: `agent/app/services/lifecycle_scanner.py` (`warn` + `close` actions)
- Test: `agent/tests/test_lifecycle_message_override.py` (new), `agent/tests/test_lifecycle_scanner.py` (extend)

**Interfaces:**
- Consumes: `get_assistant_lifecycle_timing` message keys (Task 3), existing `_fetch_lifecycle_timing`, `_fetch_assistant_messages`, `_resolve_message`, `render_idle_warning`.
- Produces: `lifecycle._resolve_lifecycle_message(timing: dict | None, msgs: dict | None, key: str, default: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/test_lifecycle_message_override.py`:
```python
from app.services import lifecycle


def test_per_inbox_wins():
    t = {"idle_close_message": "Custom close"}
    assert lifecycle._resolve_lifecycle_message(t, {"idle_close": "persona"}, "idle_close", "def") == "Custom close"


def test_falls_back_to_persona():
    assert lifecycle._resolve_lifecycle_message({}, {"idle_close": "persona"}, "idle_close", "def") == "persona"


def test_falls_back_to_default():
    assert lifecycle._resolve_lifecycle_message(None, None, "idle_close", "def") == "def"


def test_blank_per_inbox_ignored():
    t = {"idle_close_message": "   "}
    assert lifecycle._resolve_lifecycle_message(t, {"idle_close": "persona"}, "idle_close", "def") == "persona"
```

Append to `agent/tests/test_lifecycle_scanner.py` (uses the `wired` fixture; to drive the `close` action, seed IDLE_WARNED past the close threshold):
```python
async def test_close_uses_per_inbox_messages(wired, monkeypatch):
    from datetime import datetime, timezone
    from app.services import lifecycle, lifecycle_store

    async def _timing(inbox_id):
        return {"idle_close_message": "BYE", "resolution_prompt_message": "OK? Y/N"}

    monkeypatch.setattr(lifecycle, "_fetch_lifecycle_timing", _timing)
    # Seed IDLE_WARNED long enough ago that decide_idle_action returns "close".
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")
    await lifecycle_store.transition(
        70, lifecycle.IDLE_WARNED, warned_at=datetime(2026, 7, 20, 11, 40, tzinfo=timezone.utc)
    )
    await lifecycle_scanner.scan_once()
    posted = [c.args[1] for c in wired.create_message.await_args_list]
    assert any("BYE" in str(m) for m in posted), posted
    assert any("OK? Y/N" in str(m) for m in posted), posted
```
(If the seeded `warned_at`/idle timing doesn't trigger `close` under the `wired` fixture's `now`/idle values, adjust the `warned_at` timestamp so `state_age >= close_after` — the point is to exercise the `close` branch. Verify by reading the fixture's `now`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest tests/test_lifecycle_message_override.py tests/test_lifecycle_scanner.py -k "override or close_uses" -v`
Expected: FAIL (`_resolve_lifecycle_message` missing; close uses persona only).

- [ ] **Step 3: Add the resolver to `lifecycle.py`**

Add near `_resolve_message` (after it):
```python
def _resolve_lifecycle_message(
    timing: dict | None, msgs: dict | None, key: str, default: str
) -> str:
    """Per-inbox override -> persona message -> SOP default. The per-inbox store
    key is f"{key}_message"; `key` is the persona key (e.g. "idle_close")."""
    per = (timing or {}).get(f"{key}_message")
    if isinstance(per, str) and per.strip():
        return per
    return _resolve_message(msgs, key, default)
```

- [ ] **Step 4: Wire the scanner (warn + close)**

In `lifecycle_scanner.py::_process_one`:
- `warn` action — replace the current per-inbox idle_warning inline check so it uses the resolver:
```python
    if action == "warn":
        warning = lifecycle._resolve_lifecycle_message(
            timing, msgs, "idle_warning", lifecycle.IDLE_WARNING_DEFAULT
        )
        await lifecycle._post(
            conversation_id, lifecycle.render_idle_warning(warning, grace)
        )
        await lifecycle_store.transition(conversation_id, lifecycle.IDLE_WARNED, warned_at=now)
        await lifecycle._mirror_state(conversation_id, lifecycle.IDLE_WARNED)
```
- `close` action — replace the two `_resolve_message(...)` calls with the resolver:
```python
    elif action == "close":
        await lifecycle._post(
            conversation_id,
            lifecycle._resolve_lifecycle_message(timing, msgs, "idle_close", lifecycle.IDLE_CLOSE_DEFAULT),
        )
        await lifecycle._post(
            conversation_id,
            lifecycle._resolve_lifecycle_message(timing, msgs, "resolution_prompt", lifecycle.RESOLUTION_PROMPT_DEFAULT),
        )
        await lifecycle_store.transition(conversation_id, lifecycle.AWAITING_RESOLUTION)
        await lifecycle._mirror_state(conversation_id, lifecycle.AWAITING_RESOLUTION)
```
(`timing` and `msgs` are both already in scope in `_process_one`.)

- [ ] **Step 5: Wire the lifecycle reply/survey sites**

In `lifecycle.py::handle_lifecycle_reply`, after `msgs = await _fetch_assistant_messages(inbox_id)` add:
```python
    timing = await _fetch_lifecycle_timing(inbox_id)
```
and replace its three `_resolve_message(msgs, key, DEFAULT)` calls with `_resolve_lifecycle_message(timing, msgs, key, DEFAULT)` for `assign_agent`/`survey_ai`/`thanks`.
In `lifecycle.py::on_human_resolved`, after its `msgs = await _fetch_assistant_messages(inbox_id)` add `timing = await _fetch_lifecycle_timing(inbox_id)` and change its `survey_agent` post to `_resolve_lifecycle_message(timing, msgs, "survey_agent", SURVEY_AGENT_DEFAULT)`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest tests/test_lifecycle_message_override.py tests/test_lifecycle_scanner.py tests/test_lifecycle_replies.py -v`
Expected: PASS (new + existing lifecycle/scanner tests). If a pre-existing reply test asserted persona/default text, it still passes because empty per-inbox timing falls through to persona/default.

- [ ] **Step 7: Commit**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add agent/app/services/lifecycle.py agent/app/services/lifecycle_scanner.py agent/tests/test_lifecycle_message_override.py agent/tests/test_lifecycle_scanner.py
git commit -m "feat(agent): per-inbox override for all seven auto-close messages"
```

---

### Task 5: UI — six more message textareas in patch 0023

**Files:** Modify: `deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch` (regenerate)

**Interfaces:** consumes `GET`/`PUT /kb/inboxes/{id}/timing` carrying the 7 message keys (Tasks 1–2). `protonKnowledge.js` needs no change.

> Reconstruct-tree against the local `chatwoot/chatwoot:v4.15.1` image; use `/opt/homebrew/bin/git` for `git diff`. Apply patches 0001-0022 (committed) then the CURRENT 0023 into the working tree, edit `WeeklyAvailability.vue`, and `git diff` for the full new 0023. **`{{minutes}}` literals in template TEXT need `<code v-pre>` — a static `placeholder` attribute string is fine.**

- [ ] **Step 1: Reconstruct with the current 0023 applied**
```bash
rm -rf /tmp/cw_msg && mkdir -p /tmp/cw_msg && cd /tmp/cw_msg
GIT=/opt/homebrew/bin/git
CID=$(docker create chatwoot/chatwoot:v4.15.1); docker cp "$CID:/app/app" ./app >/dev/null 2>&1; docker rm "$CID" >/dev/null
$GIT init -q && $GIT add -A && $GIT commit -q -m base
for p in /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/00[0-1][0-9]-*.patch /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/002[0-2]-*.patch; do $GIT apply --whitespace=fix "$p" || { echo FAIL $p; exit 1; }; done
$GIT add -A && $GIT commit -q -m "0001-0022"
$GIT apply --whitespace=fix /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch
```
File to edit: `app/javascript/dashboard/routes/dashboard/settings/inbox/components/WeeklyAvailability.vue` (already has `idleWarningMessage` data, its textarea, `loadTiming`, and the `setInboxTiming` body from the current 0023).

- [ ] **Step 2: Edit the Vue — data**

Next to `idleWarningMessage: ''`, add:
```javascript
      idleCloseMessage: '',
      resolutionPromptMessage: '',
      assignAgentMessage: '',
      surveyAiMessage: '',
      surveyAgentMessage: '',
      thanksMessage: '',
```

- [ ] **Step 3: Edit the Vue — loadTiming**

Next to `this.idleWarningMessage = t.idle_warning_message ?? '';` add:
```javascript
        this.idleCloseMessage = t.idle_close_message ?? '';
        this.resolutionPromptMessage = t.resolution_prompt_message ?? '';
        this.assignAgentMessage = t.assign_agent_message ?? '';
        this.surveyAiMessage = t.survey_ai_message ?? '';
        this.surveyAgentMessage = t.survey_agent_message ?? '';
        this.thanksMessage = t.thanks_message ?? '';
```

- [ ] **Step 4: Edit the Vue — updateInbox (save body)**

Next to `idle_warning_message: this.idleWarningMessage.trim() || null,` in the `setInboxTiming` body, add:
```javascript
          idle_close_message: this.idleCloseMessage.trim() || null,
          resolution_prompt_message: this.resolutionPromptMessage.trim() || null,
          assign_agent_message: this.assignAgentMessage.trim() || null,
          survey_ai_message: this.surveyAiMessage.trim() || null,
          survey_agent_message: this.surveyAgentMessage.trim() || null,
          thanks_message: this.thanksMessage.trim() || null,
```

- [ ] **Step 5: Edit the Vue — template (six textareas)**

After the existing "Idle warning message" textarea block (and before the four number inputs, or right after the warning box — keep grouped), add six textareas following the same markup. Use a `v-for` over a local list to stay DRY, OR six explicit blocks. Explicit example for one (repeat for the other five with the matching label/model/placeholder):
```vue
        <div class="mt-4">
          <label class="block mb-1 text-sm font-medium text-n-slate-12">Chat closed message</label>
          <textarea v-model="idleCloseMessage" rows="2"
            placeholder="Closed due to inactivity."
            class="w-full px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black2 text-n-slate-12 focus:outline-none" />
        </div>
```
Labels/models/placeholders for the six:
- Chat closed message → `idleCloseMessage` → "Closed due to inactivity."
- Resolution prompt → `resolutionPromptMessage` → "Is your case resolved? Please reply YES or NO."
- Assign-to-agent message → `assignAgentMessage` → "Thank you. We will assign an agent to assist you further."
- AI rating survey → `surveyAiMessage` → "Thank you. Please rate our AI assistant from 1 to 5."
- Agent rating survey → `surveyAgentMessage` → "Thank you. Please rate our support agent from 1 to 5."
- Thank-you message → `thanksMessage` → "Thank you for your feedback!"

- [ ] **Step 6: Generate + verify the patch**
```bash
cd /tmp/cw_msg
/opt/homebrew/bin/git diff > /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch
# Full stack applies clean on a fresh tree:
rm -rf /tmp/cw_msg_v && mkdir -p /tmp/cw_msg_v && cd /tmp/cw_msg_v
CID=$(docker create chatwoot/chatwoot:v4.15.1); docker cp "$CID:/app/app" ./app >/dev/null 2>&1; docker rm "$CID" >/dev/null
/opt/homebrew/bin/git init -q && /opt/homebrew/bin/git add -A && /opt/homebrew/bin/git commit -q -m base
for p in /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/*.patch; do /opt/homebrew/bin/git apply --check --whitespace=fix "$p" && /opt/homebrew/bin/git apply --whitespace=fix "$p" || { echo FAIL $p; exit 1; }; done
echo "STACK APPLIES CLEAN"
# Vite compiles (catches SFC errors before Cloud Build):
cd /Users/yudaadipratama/Archive/id-crm-ticketing && docker build --target builder -t proton-chatwoot-verify:local deploy/chatwoot-fork/ 2>&1 | tail -15
```
Expected: "STACK APPLIES CLEAN" and a successful `vite build`. Clean up `/tmp/cw_msg*` + the verify image.

- [ ] **Step 7: Commit**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch
git commit -m "feat(chatwoot-fork): per-inbox fields for all seven auto-close messages"
```

---

### Task 6: Deploy to proton VM + smoke

**Files:** none (deploy). Ends with a live smoke gate.

- [ ] **Step 1: Sync + rebuild backend + agent**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
tar --exclude='__pycache__' -czf /tmp/allmsg.tgz \
  backend/apps/backend/src/chatbot/features/chat/adapters/inbox_timing_store.py \
  backend/apps/backend/src/chatbot/features/chat/kb_inboxes_router.py \
  agent/app/clients/proton.py \
  agent/app/services/lifecycle.py \
  agent/app/services/lifecycle_scanner.py
gcloud compute scp /tmp/allmsg.tgz crm-ticketing:/tmp/allmsg.tgz --zone asia-southeast2-a
gcloud compute ssh crm-ticketing --zone asia-southeast2-a --command '
set -e
TS=$(date +%s); tar -czf /tmp/src-backup-$TS.tgz -C /opt/platform backend/apps/backend/src/chatbot/features/chat/adapters/inbox_timing_store.py backend/apps/backend/src/chatbot/features/chat/kb_inboxes_router.py agent/app/clients/proton.py agent/app/services/lifecycle.py agent/app/services/lifecycle_scanner.py 2>/dev/null || true
tar -xzf /tmp/allmsg.tgz -C /opt/platform 2>&1 | grep -v xattr || true
cd /opt/platform/deploy
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --build backend agent 2>&1 | tail -5
'
```

- [ ] **Step 2: Build + deploy the Chatwoot image**
```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
gcloud builds submit deploy/chatwoot-fork/ --config deploy/chatwoot-fork/cloudbuild.yaml \
  --substitutions _REGISTRY=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images
gcloud compute ssh crm-ticketing --zone asia-southeast2-a --command '
cd /opt/platform/deploy
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env pull chatwoot-rails chatwoot-sidekiq
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --force-recreate chatwoot-rails chatwoot-sidekiq 2>&1 | tail -6
'
```

- [ ] **Step 3: Verify deploy**
```bash
gcloud compute ssh crm-ticketing --zone asia-southeast2-a --command '
echo "agent:$(docker inspect proton-agent --format {{.State.Health.Status}}) backend:$(docker inspect proton-backend --format {{.State.Health.Status}}) rails:$(docker inspect proton-chatwoot-rails --format {{.State.Health.Status}})"
docker exec proton-agent sh -c "grep -c _resolve_lifecycle_message /app/app/services/lifecycle.py"
docker exec proton-chatwoot-rails sh -c "grep -rl \"Chat closed message\" /app/public/vite/assets 2>/dev/null | grep -E \"\\.js\$\" | wc -l"
'
```
Expected: all healthy; resolver grep ≥1; "Chat closed message" label in the live bundle ≥1.

- [ ] **Step 4: Live smoke (human)**

In Settings → Inboxes → Twilio → Business Hours → Inactivity & auto-close: all seven message boxes appear. Set a custom "Chat closed message" and "Resolution prompt", Save, reload → persist. Drive a WhatsApp conversation through idle → warn → close and confirm your custom close + resolution-prompt text is what the customer receives (and reply YES/NO → your custom survey/thanks text).

---

## Self-Review

**Spec coverage:**
- Store `MESSAGE_KEYS` (7) + `_clean_timing` loop → Task 1. ✓
- Router body (6 more `max_length=2000`) + `_normalize_timing` loop → Task 2. ✓
- Agent client returns 7 keys → Task 3. ✓
- `_resolve_lifecycle_message` + wiring at scanner warn/close + lifecycle handle_lifecycle_reply/on_human_resolved → Task 4. ✓
- UI six more textareas → Task 5. ✓
- Deploy + smoke → Task 6. ✓
- Non-goals: timers/toggle/disclaimer/CSAT/routing untouched; only these 7 messages. ✓

**Placeholder scan:** none — concrete code + exact labels/defaults; Task 5 reconstruct-gated with vite-compile + apply-check.

**Type consistency:** `MESSAGE_KEYS` (Task 1) imported by the router (Task 2) and mirrored as `_LIFECYCLE_MESSAGE_KEYS` in the client (Task 3) — same seven `<name>_message` strings. `_resolve_lifecycle_message(timing, msgs, key, default)` (Task 4) maps persona `key` → per-inbox `f"{key}_message"`, matching the store keys. UI camelCase models (Task 5) map to the snake_case API keys in `loadTiming`/`updateInbox`. Only `idle_warning` is wrapped in `render_idle_warning` (Task 4 warn action).
