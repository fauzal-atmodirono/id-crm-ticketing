# Idle-warning `{{minutes}}` parameter + native-disclaimer switch

**Date:** 2026-07-27
**Status:** Approved (design)
**Scope:** `agent/` service only (+ one per-tenant env change on the proton VM). CSAT and auto-assignment are explicitly out of scope.

## Problem

Two operator-facing gaps in the WhatsApp conversation-lifecycle flow:

1. **Idle-warning text is hard-coded to "5 minutes".** `IDLE_WARNING_DEFAULT`
   says *"Your chat will close in 5 minutes…"* but the close grace is now
   per-inbox configurable (`idle_close_grace_minutes` /
   `idle_close_out_of_hours_grace_minutes`). When an operator sets the grace to
   10, the warning still says 5 — wrong.

2. **The disclaimer is double-sourced.** Chatwoot's **native channel greeting**
   is enabled on the WhatsApp inbox *and* our custom
   `LIFECYCLE_DISCLAIMER_ENABLED=true` is on, so a new conversation can get the
   disclaimer twice. Worse, the **native** greeting is an outgoing, unmarked
   message, so it re-triggers the first-turn masking bug just fixed for our own
   disclaimer (customer's first message hidden → `/chat/turn` never called →
   no AI reply).

## Decisions (from brainstorming)

- Disclaimer: **native channel greeting owns it.** Disable our custom disclaimer
  on proton; teach the orchestrator to skip the native greeting so it doesn't
  mask the first turn.
- CSAT: **keep the custom in-conversation survey** (no change; avoids Meta
  template approval).
- Auto-assignment / Phase 5 routing: **deferred** to a separate spec.

## Non-goals

- No CSAT changes. No auto-assignment / routing changes.
- No change to the default value of `lifecycle_disclaimer_enabled` in code
  (other tenants keep today's behavior); proton flips it via env only.
- Not handling Chatwoot greeting **variables** (`{{contact.name}}` etc.) — the
  disclaimer greeting has none, so exact content match suffices. Documented
  limitation.

## Design

### Component A — Idle-warning `{{minutes}}` parameter

Files: `agent/app/services/lifecycle.py`, `agent/app/services/lifecycle_scanner.py`.

- Change the default:
  `IDLE_WARNING_DEFAULT = "Your chat will close in {{minutes}} minutes if we do not hear from you."`
- Add a pure helper in `lifecycle.py`:
  ```python
  def render_idle_warning(text: str, minutes: int) -> str:
      """Replace the {{minutes}} token with the effective close-grace value.
      A message without the token is returned unchanged (backward compatible)."""
      return text.replace("{{minutes}}", str(minutes))
  ```
- In `lifecycle_scanner._process_one`, the `warn` action already has `grace`
  (the effective in-hours / out-of-hours close grace, per-inbox). Render before
  posting:
  ```python
  if action == "warn":
      warning = lifecycle._resolve_message(
          msgs, "idle_warning", lifecycle.IDLE_WARNING_DEFAULT
      )
      await lifecycle._post(conversation_id, lifecycle.render_idle_warning(warning, grace))
      ...
  ```
- The operator's persona `idle_warning_message` (already editable via Knowledge
  Settings patch 0022) may include `{{minutes}}`; it renders the same way.
- Semantics: `minutes` = the grace between the warning and auto-close (`grace`),
  i.e. how long the customer has left. Matches the current "5 minutes" meaning.

### Component B — Native disclaimer + no first-turn masking

Files: `agent/app/services/orchestrator.py` (+ proton env change, deploy step).

1. **Env (proton only, deploy step):** set `LIFECYCLE_DISCLAIMER_ENABLED=false`.
   Native "Enable channel greeting" stays enabled in the Chatwoot inbox UI (it
   already carries the disclaimer text). Our lifecycle still seeds the row on
   `conversation_created` (seeding happens before the disclaimer check —
   verified), so idle warn/close/survey are unaffected.

2. **Orchestrator — skip the native greeting when computing the customer turn.**
   `_latest_incoming_text` currently skips `private` messages and messages
   carrying `content_attributes.proton_lifecycle`, and breaks on any other
   outgoing message. Extend it to also skip an outgoing message whose content
   equals the inbox's configured greeting:
   ```python
   def _latest_incoming_text(message_list: list[dict], greeting_text: str = "") -> str:
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
           if mtype == 0:
               if content:
                   texts.append(content)
           elif mtype == 1:
               break
       return "\n".join(reversed(texts))
   ```
   (The `content` is read once and reused for both the greeting check and the
   incoming-collect.)

3. **Caller — pass the greeting.** In `_process_via_chat_agent`, fetch the inbox
   once (via the existing `get_chatwoot_client().get_inbox(inbox_id)`; one call
   per turn, fail-open) and pass `greeting_message` when greeting is enabled:
   ```python
   greeting_text = ""
   try:
       inbox = await get_chatwoot_client().get_inbox(inbox_id) if inbox_id else None
       if isinstance(inbox, dict) and inbox.get("greeting_enabled"):
           greeting_text = inbox.get("greeting_message") or ""
   except Exception:
       greeting_text = ""  # fail-open: no skip
   text = _latest_incoming_text(message_list, greeting_text)
   ```
   Fail-open: if the inbox fetch fails, `greeting_text` stays empty → behaves
   exactly like today (no greeting skip). The `proton_lifecycle` marker skip is
   unaffected.

## Testing (TDD)

`agent/` suite (`.venv/bin/pytest`, asyncio_mode=auto).

**Component A** (`tests/test_lifecycle_*`):
- `render_idle_warning("… {{minutes}} …", 10) == "… 10 …"`.
- No-token message returned unchanged.
- Scanner `warn` action posts a message containing the effective grace (e.g. a
  per-inbox grace of 7 → "…close in 7 minutes…"); assert via the existing
  scanner test harness (mock `_fetch_lifecycle_timing` / settings).

**Component B** (extend `tests/test_orchestrator_lifecycle_masking.py`):
- Outgoing message whose content == greeting_text is skipped → the first
  customer message is still collected.
- A non-matching outgoing message (a real bot reply) still bounds the turn.
- Empty `greeting_text` (default) preserves current behavior.
- Combined with the `proton_lifecycle` marker skip.

## Rollout / deploy

- Code: agent-only change → sync `agent/app/{services/lifecycle.py,services/lifecycle_scanner.py,services/orchestrator.py}` to `/opt/platform/agent`, rebuild + recreate `proton-agent`.
- Env: set `LIFECYCLE_DISCLAIMER_ENABLED=false` in `deploy/tenants/proton.env`
  (root-owned, sudo). Confirm native channel greeting is enabled on inbox 3.
- Smoke: fresh WhatsApp "halo" → native greeting (once) + AI reply; leave idle →
  warning shows the configured grace value (e.g. "…close in 2 minutes…").
- Fail-open + default-preserving: with `greeting_text` empty and the disclaimer
  env unchanged, other tenants behave exactly as before.
