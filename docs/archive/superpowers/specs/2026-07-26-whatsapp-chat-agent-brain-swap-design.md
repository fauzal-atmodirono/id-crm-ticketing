# WhatsApp brain-swap: route the agent-bot through the backend `/chat/turn` agent

**Date:** 2026-07-26
**Status:** Approved (brainstorm) → implementation
**Branch:** dev-yuda

## Problem

The live WhatsApp bot (proton, Chatwoot inbox 3, `AGENT_MODE=auto`) answers with a
crude 3-way router (`app/ai/gemini.py::decide` + `app/ai/tools.py`:
`send_reply` / `escalate_to_ticket` / `handoff_to_human`). Its tool prompts create a
catch-22: `send_reply` says answer "using only what's in the conversation so far", and
`handoff_to_human` says use it "whenever you … don't have enough grounded information".
So a normal KB-answerable product/spec question (e.g. *"ada detail spesification?"*)
is routed to a **human handoff** instead of being answered — it never reaches the KB
copilot, which only runs *after* `send_reply` is chosen.

The Proton **frontend** does not have this problem: it drives the backend
`POST /chat/turn` ADK "Support Agent", which **answers by default** (KB-grounded, with
product cards) and only hands off on four real triggers: explicit human request
(`help_request`), KB `NO_MATCHES` (`unknown_retry_limit`), negative sentiment
(`negative_sentiment`), and purchase/test-drive intent (`sales_lead`, after gathering
lead details).

## Goal

Make the live WhatsApp bot behave like the frontend: **answer info/spec questions from
the KB; hand off only on genuine intent** — while preserving every CRM feature the
`agent/` service already owns (per-inbox mode off/suggest/auto, lifecycle SOP,
auto-categorization, dedupe, debounce, Chatwoot as system of record).

## Approach (chosen: "swap the brain in `agent/`")

Keep the `agent/` service owning the Chatwoot bot webhook and all its plumbing. Replace
**only** the decide-and-execute core of `orchestrator._process_conversation`: instead of
`gemini.decide()` (+ the `kb_grounded_replies` `/assist/copilot` graft), call the backend
`POST /chat/turn` and map its structured result to Chatwoot actions. Chatwoot stays the
system of record; the backend is the conversational brain. Two other architectures
(backend owns the Chatwoot webhook; native Twilio→backend) were rejected because they
bypass the `agent/` service's lifecycle/mode/categorization investment.

```
Twilio → Chatwoot inbox 3 → agent/ bot webhook
   (verify, dedupe, lifecycle pre-check, debounce, per-inbox mode)
        │  _process_conversation: replaces gemini.decide()
        ▼
   backend POST /chat/turn  {session_id: "crm-<conv_id>", text}
        → full ADK agent: KB answer + handoff-on-intent
        │  reply, no handoff → post to Chatwoot (auto: public via bot token;
        │                       suggest: private note + reopen)
        │  handoff present    → Chatwoot ack + reopen (existing
        │                       _handoff_to_human_via_chatwoot)
        │  forwarded_to_agent → no-op (already handed off)
        │  backend error/None → fail-open Chatwoot handoff (ack + reopen)
        ▼
   Chatwoot = system of record (unchanged)
```

## Component 1 — backend change (vendored `backend/`)

`features/chat/service.py::handle_turn` currently, on `handoff_triggered`, EITHER runs
its own escalation (`_escalate_handoff` → Sunshine live bridge / backend ticket) and
returns a `handoff` payload (web sessions), OR does neither (`whatsapp-`/`email-`
sessions — payload stays `None`).

Add a **`crm-` session prefix** that is *channel-owned*: on `handoff_triggered` it
returns a lightweight `HandoffPayload` (`reason`, `language` from session state; no
summarizer/bridge/ticket) and suppresses `reply_text`, but does **not** call
`_escalate_handoff`. Rationale: the CRM (Chatwoot, via `agent/`) owns the actual
handoff; the backend only needs to signal *that* a handoff fired and *why*.

Change at `service.py:427-435`:

```python
handoff_triggered = session_state.get("handoff_triggered") is True
is_channel_owned = session_id.startswith(("whatsapp-", "email-", "crm-"))
if handoff_triggered and not is_channel_owned:
    reason = session_state.get("handoff_reason", "help_request")
    handoff_payload = await self._escalate_handoff(session_id, reason)   # web: full escalation
    reply_text = None
elif handoff_triggered and session_id.startswith("crm-"):
    # CRM-owned handoff: the caller owns the handoff in its own system
    # (Chatwoot). Signal reason only — no live bridge, no backend ticket.
    handoff_payload = HandoffPayload(
        reason=session_state.get("handoff_reason", "help_request"),
        language=session_state.get("language", "unknown"),
    )
    reply_text = None
elif reply_text:
    ... # append assistant message (unchanged)
```

The `whatsapp-`/`email-` native paths are untouched (they still return `None`, as today).

## Component 2 — `agent/` client

`ProtonConfigClient.chat_turn(session_id: str, text: str) -> dict | None` — `POST
{base_url}/chat/turn` with `{session_id, text}`, 10s timeout, returns the parsed JSON
(`reply`, `handoff`, `products`, `forwarded_to_agent`) or `None` on any error/non-2xx
(fail-open, mirrors the existing methods). No auth header needed (`/chat/turn` is
unauthenticated), but sending the existing `x-api-key` is harmless.

## Component 3 — `agent/` orchestrator

New config flag `chat_agent_enabled: bool = False` (env `CHAT_AGENT_ENABLED`). When
**off**, `_process_conversation` is byte-identical to today. When **on**, it:

1. Resolves inbox/mode exactly as today; `off` → skip (no backend call).
2. Fetches messages, builds the new customer `text` for this debounce window
   (the latest incoming customer message content; the backend owns multi-turn history
   keyed by `crm-<conv_id>`).
3. Calls `proton.chat_turn(f"crm-{conversation_id}", text)`.
4. Logs an `ai_actions` row (decision = `chat_turn` / `chat_turn:handoff`).
5. Maps the result:
   - `forwarded_to_agent` truthy → no-op (already handed off).
   - `handoff` present → `_handoff_to_human_via_chatwoot` (ack + reopen). The reason
     may tailor the message later; start with the tenant `handoff_default_message`.
   - `reply` present, no handoff → post per mode: `auto` → public via
     `chatwoot_bot_token`, stays pending; `suggest` → private "🤖 Suggested reply" note
     + reopen (same as today's `send_reply`).
   - backend returned `None` / empty reply and no handoff → **fail-open**
     `_handoff_to_human_via_chatwoot` so the conversation is never left silent.

The debounce, lifecycle pre-check, dedupe, and `escalate_to_ticket`/Zammad-off behavior
are unchanged. The legacy `gemini.decide()` path and `kb_grounded_replies` graft remain
in place for the flag-off default and other tenants.

## Out of scope (follow-ups)

- **Product cards.** `/chat/turn` returns `products`; WhatsApp via Chatwoot can't render
  a carousel. The KB text `reply` already answers spec questions (the reported bug).
  Formatting products into WhatsApp text (the backend already has `_whatsapp_reply_text`)
  is a later enhancement.
- **History fidelity.** The backend owns history from the first `/chat/turn` call; a human
  reply typed directly in Chatwoot during the bot phase isn't fed back to the backend
  session. Acceptable for the bot phase; revisit if needed.
- Reason-specific handoff messages (sales vs help vs sentiment).

## Testing

`agent/` (TDD, `respx`-stub `/chat/turn`):
- flag on + spec question (reply, no handoff) → posts reply; auto stays pending.
- flag on + handoff payload → ack + reopen, no Zammad, reply suppressed.
- flag on + backend `None`/error → fail-open handoff (ack + reopen).
- flag on + `forwarded_to_agent` → no-op.
- flag off → legacy `gemini.decide()` path unchanged (existing tests stay green).

`backend/` (pytest): `crm-` session with `handoff_triggered` → `handle_turn` returns a
`HandoffPayload` (reason from state) and does NOT call `_escalate_handoff`; `reply_text`
suppressed. A `crm-` session without handoff → returns the reply normally.

## Rollout

Flag-gated (`CHAT_AGENT_ENABLED=false` default = byte-identical). Deploy backend
(crm- branch) + agent (new brain) to the proton VM; set `CHAT_AGENT_ENABLED=true` on
proton; live-smoke the spec question and a "talk to a human" handoff.
