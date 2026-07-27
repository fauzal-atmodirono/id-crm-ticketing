# Per-inbox idle-warning message + inactivity enable toggle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-inbox idle-warning message (overriding the persona message) and a per-inbox enable toggle for the whole idle warn→close flow, both edited in the native inbox Business-Hours tab and saved by the existing "Update business hours settings" button.

**Architecture:** Extend the existing per-inbox `InboxTimingStore` (4 int keys) with two typed fields — `idle_warning_message` (str) and `inactivity_enabled` (bool). They ride the same `/kb/inboxes/{id}/timing` endpoint, the same `/kb/inboxes` list rows, and the same agent read path. The scanner gates the idle flow on `inactivity_enabled` and picks the warning message per-inbox → persona → default. The fork patch 0023 (which already injects the timing fields into `WeeklyAvailability.vue`) gains a toggle + a message textarea.

**Tech Stack:** Python 3.12, FastAPI, pydantic, Firestore, pytest (asyncio_mode=auto) + FastAPI TestClient; Vue 3 SPA fork patch (Chatwoot v4.15.1).

**Spec:** `docs/superpowers/specs/2026-07-27-per-inbox-idle-message-and-enable-toggle-design.md`

## Global Constraints

- New field keys, verbatim: `idle_warning_message` (str), `inactivity_enabled` (bool). Existing four int keys unchanged (`idle_warn_minutes`, `idle_close_grace_minutes`, `idle_close_out_of_hours_grace_minutes`, `confirm_grace_minutes`).
- Toggle default: **unset/None = enabled** (only an explicit `false` disables). Message: empty/None → fall back to persona then default.
- Scanner gate applies to the **bot-phase idle flow** only (warn/close/resolution/survey); do NOT gate the assigned-handoff auto-resolve.
- Message precedence at the warn action: per-inbox `idle_warning_message` (non-empty str) → persona `_resolve_message(msgs, "idle_warning", …)` → `IDLE_WARNING_DEFAULT`; then `render_idle_warning(warning, grace)`.
- `PUT /kb/inboxes/{id}/timing` stays full-replace (store non-null fields; all-null → delete). Message `max_length=2000`.
- Fail-open + backward compatible: no stored `inactivity_enabled` → runs; empty message → persona/default; any fetch error → today's behavior.
- Backend tests: `cd backend/apps/backend && .venv/bin/pytest <path> -v`. Agent tests: `cd agent && .venv/bin/pytest <path> -v` (bare `pytest` not on PATH). asyncio_mode=auto.
- Commit only each task's files, by explicit path (never `git add -A` — a concurrent session has committed on dev-yuda before).

---

### Task 1: Backend store — typed message + enabled fields

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/adapters/inbox_timing_store.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/adapters/test_inbox_timing_store.py` (extend)

**Interfaces:**
- Produces: module constants `MESSAGE_KEY = "idle_warning_message"`, `ENABLED_KEY = "inactivity_enabled"`, and a shared `_clean_timing(data: dict) -> dict[str, Any]`. `set`/`get`/`get_all` now round-trip a mixed dict (ints + optional str + optional bool).

- [ ] **Step 1: Write the failing test**

Append to `backend/apps/backend/src/chatbot/features/chat/adapters/test_inbox_timing_store.py`:

```python
from chatbot.features.chat.adapters.inbox_timing_store import (
    MESSAGE_KEY,
    ENABLED_KEY,
)


async def test_roundtrip_message_and_enabled():
    store = InMemoryInboxTimingStore()
    await store.set(7, {
        "idle_warn_minutes": 3,
        MESSAGE_KEY: "Closing in {{minutes}} min.",
        ENABLED_KEY: False,
    })
    assert await store.get(7) == {
        "idle_warn_minutes": 3,
        MESSAGE_KEY: "Closing in {{minutes}} min.",
        ENABLED_KEY: False,
    }


async def test_partial_only_enabled():
    store = InMemoryInboxTimingStore()
    await store.set(7, {ENABLED_KEY: True})
    assert await store.get(7) == {ENABLED_KEY: True}


async def test_wrong_types_dropped():
    store = InMemoryInboxTimingStore()
    # message must be str, enabled must be bool — wrong types are ignored.
    await store.set(7, {MESSAGE_KEY: 123, ENABLED_KEY: "yes", "idle_warn_minutes": 5})
    assert await store.get(7) == {"idle_warn_minutes": 5}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/adapters/test_inbox_timing_store.py -v`
Expected: FAIL (`MESSAGE_KEY`/`ENABLED_KEY` not importable; message/enabled dropped by the int-only `set`).

- [ ] **Step 3: Implement the typed fields**

In `inbox_timing_store.py`, add the constants right after `TIMING_KEYS` (near line 36):
```python
MESSAGE_KEY = "idle_warning_message"
ENABLED_KEY = "inactivity_enabled"
```
Add a shared module-level cleaner (place above the `InMemoryInboxTimingStore` class, after the constants):
```python
def _clean_timing(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only recognised keys with the right type: the four ints (0..1440
    not enforced here — the router validates), a str message, a bool enabled."""
    out: dict[str, Any] = {}
    for k in TIMING_KEYS:
        v = data.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
    msg = data.get(MESSAGE_KEY)
    if isinstance(msg, str):
        out[MESSAGE_KEY] = msg
    en = data.get(ENABLED_KEY)
    if isinstance(en, bool):
        out[ENABLED_KEY] = en
    return out
```
(Ensure `from typing import Any` is imported — it already is, used by the Firestore impl.)

Change `InMemoryInboxTimingStore`:
- `self._data: dict[int, dict[str, Any]] = {}`
- `get_all`/`get` return type annotations → `dict[int, dict[str, Any]]` / `dict[str, Any] | None` (bodies unchanged; they already copy).
- `set`:
```python
    async def set(self, inbox_id: int, timing: dict[str, Any]) -> None:
        self._data[inbox_id] = _clean_timing(timing)
```

Change `FirestoreInboxTimingStore._clean` to delegate:
```python
    @staticmethod
    def _clean(data: dict[str, Any]) -> dict[str, Any]:
        return _clean_timing(data)
```
Update the Firestore `get_all`/`get`/`set` type annotations from `dict[str, int]` to `dict[str, Any]` (the `set` method's `cleaned = {k: int(v) ...}` line must become `cleaned = _clean_timing(timing)`). Update the `InboxTimingStorePort` Protocol signatures' `dict[str, int]` → `dict[str, Any]` too.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/adapters/test_inbox_timing_store.py -v`
Expected: PASS (new + existing store tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add backend/apps/backend/src/chatbot/features/chat/adapters/inbox_timing_store.py backend/apps/backend/src/chatbot/features/chat/adapters/test_inbox_timing_store.py
git commit -m "feat(backend): store per-inbox idle_warning_message + inactivity_enabled"
```

---

### Task 2: Backend router — body fields + normalization

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/kb_inboxes_router.py`
- Test: `backend/apps/backend/src/chatbot/features/chat/test_kb_inboxes_timing.py` (extend)

**Interfaces:**
- Consumes: `MESSAGE_KEY`, `ENABLED_KEY` (Task 1).
- Produces: `InboxTimingBody` gains `idle_warning_message: str | None` and `inactivity_enabled: bool | None`; `_normalize_timing` returns the six fields.

- [ ] **Step 1: Write the failing test**

Append to `backend/apps/backend/src/chatbot/features/chat/test_kb_inboxes_timing.py`:

```python
def test_put_get_message_and_enabled_roundtrip():
    client, _ = _client()
    body = {
        "idle_warn_minutes": None, "idle_close_grace_minutes": None,
        "idle_close_out_of_hours_grace_minutes": None, "confirm_grace_minutes": None,
        "idle_warning_message": "Closing in {{minutes}} min.",
        "inactivity_enabled": False,
    }
    r = client.put("/kb/inboxes/5/timing", json=body, headers=_H)
    assert r.status_code == 200
    got = client.get("/kb/inboxes/5/timing", headers=_H).json()
    assert got["idle_warning_message"] == "Closing in {{minutes}} min."
    assert got["inactivity_enabled"] is False
    # unset numbers still null
    assert got["idle_warn_minutes"] is None


def test_unset_message_and_enabled_are_null():
    client, _ = _client()
    got = client.get("/kb/inboxes/99/timing", headers=_H).json()
    assert got["idle_warning_message"] is None
    assert got["inactivity_enabled"] is None


def test_message_max_length_422():
    client, _ = _client()
    r = client.put("/kb/inboxes/5/timing", json={"idle_warning_message": "x" * 2001}, headers=_H)
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_kb_inboxes_timing.py -v`
Expected: FAIL (fields absent from body/response; no max_length).

- [ ] **Step 3: Implement**

In `kb_inboxes_router.py`:

Add the import next to `TIMING_KEYS` (line 27):
```python
from chatbot.features.chat.adapters.inbox_timing_store import TIMING_KEYS, MESSAGE_KEY, ENABLED_KEY
```
Extend `InboxTimingBody` (after `confirm_grace_minutes`, line 66):
```python
    idle_warning_message: str | None = Field(default=None, max_length=2000)
    inactivity_enabled: bool | None = None
```
Extend `_normalize_timing` (line 69-72):
```python
def _normalize_timing(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Return the four int keys plus the message (str|None) and enabled (bool|None)."""
    stored = stored or {}
    out: dict[str, Any] = {k: stored.get(k) for k in TIMING_KEYS}
    out[MESSAGE_KEY] = stored.get(MESSAGE_KEY)
    out[ENABLED_KEY] = stored.get(ENABLED_KEY)
    return out
```
(Ensure `Any` is imported in this file — it is, used elsewhere. The `put_inbox_timing` `to_store` line and return type may be left as-is: `body.model_dump()` now includes the two new fields, and the `if v is not None` filter keeps `inactivity_enabled=False` because `False is not None`. Update the `put`/`get` return type hints `dict[str, int | None]` → `dict[str, Any]` for accuracy.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/backend/apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_kb_inboxes_timing.py src/chatbot/features/chat/test_kb_inboxes_router.py -v`
Expected: PASS (new + existing timing/router tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add backend/apps/backend/src/chatbot/features/chat/kb_inboxes_router.py backend/apps/backend/src/chatbot/features/chat/test_kb_inboxes_timing.py
git commit -m "feat(backend): serve idle_warning_message + inactivity_enabled via /timing"
```

---

### Task 3: Agent — client reads fields; scanner gate + message precedence

**Files:**
- Modify: `agent/app/clients/proton.py` (`get_assistant_lifecycle_timing`)
- Modify: `agent/app/services/lifecycle_scanner.py` (`_process_one`)
- Test: `agent/tests/test_proton_client.py` (extend), `agent/tests/test_lifecycle_scanner.py` (extend)

**Interfaces:**
- Consumes: the `/kb/inboxes` list rows now carry `idle_warning_message` + `inactivity_enabled` (Task 2).
- Produces: `get_assistant_lifecycle_timing` returns those two keys too; the scanner skips disabled inboxes and prefers the per-inbox message.

- [ ] **Step 1: Write the failing tests**

Append to `agent/tests/test_proton_client.py` (reuse the `_make_client` + respx pattern; add rows with the new fields to a fixture):

```python
_TIMING_MSG_INBOXES = {
    "inboxes": [
        {"inbox_id": 40, "idle_warn_minutes": 3,
         "idle_warning_message": "Bye in {{minutes}}", "inactivity_enabled": False},
        {"inbox_id": 41, "inactivity_enabled": True},
    ]
}


@respx.mock
async def test_lifecycle_timing_includes_message_and_enabled():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=_TIMING_MSG_INBOXES)
    )
    client = _make_client()
    t = await client.get_assistant_lifecycle_timing(40)
    assert t["idle_warning_message"] == "Bye in {{minutes}}"
    assert t["inactivity_enabled"] is False


@respx.mock
async def test_lifecycle_timing_message_absent_is_none():
    respx.get(f"{PROTON_BASE}/kb/inboxes").mock(
        return_value=httpx.Response(200, json=_TIMING_MSG_INBOXES)
    )
    client = _make_client()
    t = await client.get_assistant_lifecycle_timing(41)
    assert t["idle_warning_message"] is None
    assert t["inactivity_enabled"] is True
```

Append to `agent/tests/test_lifecycle_scanner.py` (uses the existing `wired` fixture — conv 70, idle 12, warn default 10 → warns):

```python
async def test_scan_skips_disabled_inbox(wired, monkeypatch):
    from app.services import lifecycle

    async def _timing(inbox_id):
        return {"inactivity_enabled": False}

    monkeypatch.setattr(lifecycle, "_fetch_lifecycle_timing", _timing)
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(70) == "active"  # disabled -> no warn
    wired.create_message.assert_not_awaited()


async def test_scan_uses_per_inbox_warning_message(wired, monkeypatch):
    from app.services import lifecycle

    async def _timing(inbox_id):
        return {"idle_close_grace_minutes": 4,
                "idle_warning_message": "Auto-close in {{minutes}}m."}

    monkeypatch.setattr(lifecycle, "_fetch_lifecycle_timing", _timing)
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")
    await lifecycle_scanner.scan_once()
    posted = [c.args[1] for c in wired.create_message.await_args_list]
    assert any("Auto-close in 4m." in str(m) for m in posted), posted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest tests/test_proton_client.py::test_lifecycle_timing_includes_message_and_enabled tests/test_lifecycle_scanner.py::test_scan_skips_disabled_inbox tests/test_lifecycle_scanner.py::test_scan_uses_per_inbox_warning_message -v`
Expected: FAIL (client omits the fields; scanner has no gate/precedence).

- [ ] **Step 3: Extend the client**

In `agent/app/clients/proton.py::get_assistant_lifecycle_timing`, after the `for key in _LIFECYCLE_TIMING_KEYS:` loop that fills `result`, before `return result`, add:
```python
            msg = row.get("idle_warning_message")
            result["idle_warning_message"] = msg if isinstance(msg, str) else None
            en = row.get("inactivity_enabled")
            result["inactivity_enabled"] = en if isinstance(en, bool) else None
```
(Update the method's return type hint to `dict[str, Any] | None` and ensure `Any` is imported in proton.py — it is, `_fetch_cached` returns `Any`.)

- [ ] **Step 4: Extend the scanner**

In `agent/app/services/lifecycle_scanner.py::_process_one`, right after `timing = await lifecycle._fetch_lifecycle_timing(inbox_id) or {}` (line 148), add the gate:
```python
    if timing.get("inactivity_enabled") is False:
        return  # inactivity & auto-close disabled for this inbox
```
Then, in the `warn` action (currently lines 174-180), replace the message resolution so the per-inbox message wins:
```python
    if action == "warn":
        per_inbox_msg = timing.get("idle_warning_message")
        if isinstance(per_inbox_msg, str) and per_inbox_msg.strip():
            warning = per_inbox_msg
        else:
            warning = lifecycle._resolve_message(
                msgs, "idle_warning", lifecycle.IDLE_WARNING_DEFAULT
            )
        await lifecycle._post(
            conversation_id, lifecycle.render_idle_warning(warning, grace)
        )
        await lifecycle_store.transition(
            conversation_id, lifecycle.IDLE_WARNED, warned_at=now
        )
        await lifecycle._mirror_state(conversation_id, lifecycle.IDLE_WARNED)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/yudaadipratama/Archive/id-crm-ticketing/agent && .venv/bin/pytest tests/test_proton_client.py tests/test_lifecycle_scanner.py -v`
Expected: PASS (new + existing).

- [ ] **Step 6: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add agent/app/clients/proton.py agent/app/services/lifecycle_scanner.py agent/tests/test_proton_client.py agent/tests/test_lifecycle_scanner.py
git commit -m "feat(agent): per-inbox idle-warning message override + inactivity enable gate"
```

---

### Task 4: UI — toggle + message textarea in patch 0023

**Files:**
- Modify: `deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch` (regenerate)

**Interfaces:** consumes `GET`/`PUT /kb/inboxes/{id}/timing` carrying the two new fields (Tasks 1–2). The current 0023 already added the four number inputs + `getInboxTiming`/`setInboxTiming` to `WeeklyAvailability.vue` / `protonKnowledge.js`.

> The Chatwoot SPA isn't in this checkout — regenerate the patch via the reconstruct-tree method against the local `chatwoot/chatwoot:v4.15.1` image. `protonKnowledge.js` needs no change (`setInboxTiming` already sends whatever body object we pass).

- [ ] **Step 1: Reconstruct the patched tree with the CURRENT 0023 applied**

```bash
rm -rf /tmp/cw_ui && mkdir -p /tmp/cw_ui && cd /tmp/cw_ui
GIT=/opt/homebrew/bin/git   # rtk mangles bare git diff
CID=$(docker create chatwoot/chatwoot:v4.15.1)
docker cp "$CID:/app/app" ./app >/dev/null 2>&1
docker rm "$CID" >/dev/null
$GIT init -q && $GIT add -A && $GIT commit -q -m base
for p in /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/00[0-1][0-9]-*.patch /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/002[0-2]-*.patch; do
  $GIT apply --whitespace=fix "$p" || { echo "FAIL $p"; exit 1; }
done
$GIT add -A && $GIT commit -q -m "patches 0001-0022"
# Apply the CURRENT 0023 into the working tree (NOT committed) so the new diff = full new 0023.
$GIT apply --whitespace=fix /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch
```
The file to edit: `app/javascript/dashboard/routes/dashboard/settings/inbox/components/WeeklyAvailability.vue` (now contains the current 0023 additions: `INACTIVITY_FIELDS`, `timing` data, `loadTiming`, `normalizeTiming`, `updateInbox` timing call, and the fields block in the template).

- [ ] **Step 2: Edit the Vue — data**

In the component `data()`, next to the existing `timing: {...}` object, add:
```javascript
      inactivityEnabled: true,
      idleWarningMessage: '',
```

- [ ] **Step 3: Edit the Vue — loadTiming**

In `loadTiming(id)`, inside the `if (t) { ... }` block (where `this.timing = {...}` is set), also set:
```javascript
        this.inactivityEnabled = t.inactivity_enabled ?? true;
        this.idleWarningMessage = t.idle_warning_message ?? '';
```

- [ ] **Step 4: Edit the Vue — updateInbox (save)**

In `updateInbox()`, the existing `setInboxTiming(this.inbox.id, { ... })` call sends the four normalized numbers. Add the two new fields to that object:
```javascript
          idle_warning_message: this.idleWarningMessage.trim() || null,
          inactivity_enabled: this.inactivityEnabled,
```

- [ ] **Step 5: Edit the Vue — template**

In the "Inactivity & auto-close" template block (added by the current 0023), place a toggle and a message textarea ABOVE the `v-for` number inputs. Use Chatwoot's already-imported `SettingsToggleSection` for the toggle, and a plain textarea styled like the number inputs:
```vue
      <div class="mt-6">
        <div class="flex items-center my-4 py-1">
          <div class="flex-1 h-px bg-n-weak" />
          <span class="text-body-main text-n-slate-11 px-2">
            Inactivity &amp; auto-close
          </span>
          <div class="flex-1 h-px bg-n-weak" />
        </div>
        <SettingsToggleSection
          v-model="inactivityEnabled"
          header="Enable inactivity &amp; auto-close for this inbox"
          description="Warn the customer after N idle minutes, then auto-close after the grace period. Turn off to disable idle warnings and auto-close for this inbox."
        />
        <div class="mt-4">
          <label class="block mb-1 text-sm font-medium text-n-slate-12">
            Idle warning message
          </label>
          <textarea
            v-model="idleWarningMessage"
            rows="2"
            placeholder="Your chat will close in {{minutes}} minutes if we do not hear from you."
            class="w-full px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black2 text-n-slate-12 focus:outline-none"
          />
          <p class="mt-1 text-xs text-n-slate-11">
            Use <code>{{ '{{minutes}}' }}</code> for the close-grace value. Leave empty to use the default.
          </p>
        </div>
        <!-- existing v-for number inputs stay below, unchanged -->
```
Keep the existing `<div v-for="f in inactivityFields" ...>` number inputs immediately after this, and the closing `</div>` of the block. (Note the `{{ '{{minutes}}' }}` escape so Vue renders the literal token in the helper text; the `placeholder` attribute is a plain string and needs no escape.)

- [ ] **Step 6: Generate + verify the new patch**

```bash
cd /tmp/cw_ui
/opt/homebrew/bin/git diff > /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch
grep "^diff --git" /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch
# Verify the full stack applies clean on a fresh tree:
rm -rf /tmp/cw_ui_verify && mkdir -p /tmp/cw_ui_verify && cd /tmp/cw_ui_verify
CID=$(docker create chatwoot/chatwoot:v4.15.1); docker cp "$CID:/app/app" ./app >/dev/null 2>&1; docker rm "$CID" >/dev/null
/opt/homebrew/bin/git init -q && /opt/homebrew/bin/git add -A && /opt/homebrew/bin/git commit -q -m base
for p in /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/*.patch; do /opt/homebrew/bin/git apply --check --whitespace=fix "$p" && /opt/homebrew/bin/git apply --whitespace=fix "$p" || { echo "FAIL $p"; exit 1; }; done
echo "ALL PATCHES 0001-0023 APPLY CLEAN"
grep -c "inactivityEnabled\|idleWarningMessage" app/javascript/dashboard/routes/dashboard/settings/inbox/components/WeeklyAvailability.vue
```
Expected: patch touches `WeeklyAvailability.vue` (and possibly `protonKnowledge.js` if unchanged from current 0023 — that's fine); "ALL PATCHES … APPLY CLEAN"; grep ≥ 2. Clean up `/tmp/cw_ui*`.

- [ ] **Step 7: Commit**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git add deploy/chatwoot-fork/patches/0023-inbox-inactivity-timing.patch
git commit -m "feat(chatwoot-fork): inbox inactivity toggle + idle-warning message field"
```

---

### Task 5: Deploy to the proton VM + smoke

**Files:** none (deploy). Ends with a live smoke gate.

- [ ] **Step 1: Deploy backend + agent (sync + rebuild)**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
tar --exclude='__pycache__' -czf /tmp/idle-msg-toggle.tgz \
  backend/apps/backend/src/chatbot/features/chat/adapters/inbox_timing_store.py \
  backend/apps/backend/src/chatbot/features/chat/kb_inboxes_router.py \
  agent/app/clients/proton.py \
  agent/app/services/lifecycle_scanner.py
gcloud compute scp /tmp/idle-msg-toggle.tgz crm-ticketing:/tmp/idle-msg-toggle.tgz --zone asia-southeast2-a
gcloud compute ssh crm-ticketing --zone asia-southeast2-a --command '
set -e
TS=$(date +%s); tar -czf /tmp/src-backup-$TS.tgz -C /opt/platform backend/apps/backend/src/chatbot/features/chat/adapters/inbox_timing_store.py backend/apps/backend/src/chatbot/features/chat/kb_inboxes_router.py agent/app/clients/proton.py agent/app/services/lifecycle_scanner.py
tar -xzf /tmp/idle-msg-toggle.tgz -C /opt/platform 2>&1 | grep -v xattr || true
cd /opt/platform/deploy
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --build backend agent 2>&1 | tail -5
'
```

- [ ] **Step 2: Build + deploy the Chatwoot image (patch 0023 update)**

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
echo "agent: $(docker inspect proton-agent --format "{{.State.Health.Status}}"); backend: $(docker inspect proton-backend --format "{{.State.Health.Status}}"); rails: $(docker inspect proton-chatwoot-rails --format "{{.State.Health.Status}}")"
docker exec proton-agent sh -c "grep -c inactivity_enabled /app/app/services/lifecycle_scanner.py"
docker exec proton-chatwoot-rails sh -c "grep -rl \"Enable inactivity\" /app/public/vite/assets 2>/dev/null | grep -E \"\\.js\$\" | wc -l"
'
```
Expected: all healthy; scanner grep ≥1; the toggle label present in the live JS bundle (≥1).

- [ ] **Step 4: Live smoke (human)**

In Settings → Inboxes → Twilio inbox → Business Hours: the "Inactivity & auto-close" card shows a toggle + an "Idle warning message" textarea + the four numbers. (a) Set a custom message with `{{minutes}}`, Save, reload → persists; send a fresh WhatsApp, go idle → the warning uses your text with the grace value. (b) Toggle OFF, Save → a fresh idle conversation is NOT warned/closed. (c) Toggle ON → idle flow resumes.

---

## Self-Review

**Spec coverage:**
- Storage: message (str) + enabled (bool) typed fields, `_clean_timing` → Task 1. ✓
- API: `InboxTimingBody` fields + `max_length` + `_normalize_timing` six fields + list rows (via existing `_normalize_timing` spread) → Task 2. ✓
- Agent: client returns the two fields; scanner enable-gate (`is False`) + message precedence (per-inbox → persona → default → render) → Task 3. ✓
- UI: toggle (`SettingsToggleSection`) + message textarea, default enabled, loaded/saved via the single button → Task 4. ✓
- Deploy + smoke (backend/agent sync + Cloud Build) → Task 5. ✓
- Non-goals honored: no CSAT/disclaimer/routing change; gate is bot-phase only; `LIFECYCLE_ENABLED` untouched. ✓

**Placeholder scan:** none — every code step has concrete content; Task 4 gives exact edits + reconstruct procedure.

**Type consistency:** `MESSAGE_KEY`/`ENABLED_KEY` defined in Task 1, imported in Task 2, and used as the row keys the client reads in Task 3 (`"idle_warning_message"`/`"inactivity_enabled"` match verbatim). `_clean_timing` (Task 1) is the single cleaner used by both store impls. `_normalize_timing` returns the six-field dict consumed by the SPA (`t.inactivity_enabled`/`t.idle_warning_message` in Task 4) and embedded in `/kb/inboxes` rows read by the agent (Task 3). Toggle default `?? true` (Task 4) matches the scanner's `is False`-only gate (Task 3).
