# WhatsApp: Single-Ticket Handoff + CSAT Survey — Design

**Date:** 2026-06-26
**Status:** Approved design (pre-plan)
**Scope:** WhatsApp (Twilio) single-ticket handoff. **CSAT is channel-agnostic** — delivered on both WhatsApp and the custom web UI, sharing one recording path. The web frontend's Sunshine Conversations *handoff* path is otherwise **unchanged** (only the CSAT survey is added to it).

## Problem

Today a single WhatsApp conversation can produce **two** Zendesk tickets:

- a **capture ticket** (Support API, via `ConversationLogPort`) that logs every turn as private comments, `solved` for routine / `open` for actionable; and
- a separate **Sunshine Conversations ticket** created by `_escalate_handoff` when the AI hands off to a human.

This is confusing for agents, and the live handoff has gaps on WhatsApp:

- the **human agent's reply does not reach the customer's WhatsApp** (it was wired for the web SSE channel), and
- when the agent finishes, the customer is silently dropped back to the bot with no closure.

## Goals

1. **One Zendesk ticket per WhatsApp session** — the capture ticket is the single hub; it already holds the full transcript and its id is persisted in session state (`conversation_ticket_id`).
2. **Seamless live handoff over WhatsApp** — when a human is needed, the AI pauses, the customer is told a human is taking over, and messages relay both ways through the *same* ticket.
3. **Closure with a CSAT survey** — when the agent resolves the ticket, the customer is asked to rate the experience (1–5) before the bot resumes. The **survey is channel-agnostic**: WhatsApp delivers it as text; the custom web UI renders a rating widget. Both record the score through one shared path so every channel behaves the same.

Routine conversations keep landing in Zendesk as a `solved` ticket **and** persist in Firestore (the ADK session store), exactly as today.

## Non-Goals

- Changing the web frontend / Sunshine Conversations handoff path.
- Native Zendesk CSAT (`satisfaction_rating`) — we store the score as a ticket comment + tag + Firestore instead (mapping to native CSAT is a later option).
- WhatsApp interactive buttons / templates for the survey (sandbox-incompatible) — the survey is plain text.
- Free-text "tell us why" follow-up after the rating (easy future add).

## Session lifecycle (state machine)

State is stored in the **session state** (Firestore-persisted, survives restarts — consistent with how `conversation_ticket_id` / `conversation_logged_count` are already stored), under a key `whatsapp_handoff_state`:

```
active ──(AI emits handoff)──▶ paused ──(agent resolves ticket)──▶ awaiting_survey ──(rating captured / skipped)──▶ active
  ▲                                                                                                                  │
  └──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **active** — the AI handles every inbound WhatsApp message (default).
- **paused** — a human agent is live; the AI does not respond.
- **awaiting_survey** — the agent has resolved; the next message is interpreted as the CSAT rating.

## Data flow

### A. Routine turn (`active`)
Unchanged from today: `twilio_whatsapp_webhook` → `handle_turn` → reply (text / image cards) → `capture_conversation` appends the turn to the single ticket (`solved`).

### B. Handoff trigger (`active` → `paused`)
During a turn the AI calls `emit_handoff_tool` (sets `handoff_triggered`). After `handle_turn` returns, for a `whatsapp-*` session the webhook runs a **Support-ticket handoff** (instead of the Sunshine path):

1. Ensure the capture ticket exists; **set status `open`**; post the AI handoff summary as a comment so the agent has context.
2. **Pause the AI** for the session (`whatsapp_handoff_state = "paused"`, persisted).
3. Send the customer a fixed WhatsApp message: *"You're being connected to a human agent who will continue helping you here shortly. 🧑‍💼"* (the normal AI reply for this turn is suppressed in favour of this message).

> `flag_for_ticket_tool` (actionable but not a live handoff) still only sets the ticket `open` via the detection gate — it does **not** pause the AI. Only a real handoff pauses the bot.

### C. While paused (live agent ↔ customer)
- **Customer → agent:** inbound WhatsApp messages are posted as **private internal notes** (prefixed `Customer (WhatsApp):`) on the capture ticket — visible to the agent, but Zendesk does not email the synthetic requester. The AI does **not** run, and `capture_conversation` is skipped (the note is the record).
- **Agent → customer:** the agent's public reply fires `zendesk-support-webhook`. We **extend** that handler (today: `sim-`/SSE only) to relay `whatsapp-*` sessions out via `TwilioChannelAdapter` (sanitised with the existing WhatsApp text helper).

### D. Handback → CSAT (`paused` → `awaiting_survey` → `active`)
The agent resolves/closes the ticket → `zendesk-handback-webhook` fires for the session:

1. Set `whatsapp_handoff_state = "awaiting_survey"` and send: *"Thanks for chatting with our support team! How would you rate your experience? Reply with a number **1–5** (1 = poor, 5 = excellent)."*
2. The next inbound WhatsApp message is captured as the rating:
   - **Valid 1–5** → post a comment `⭐ Customer satisfaction: N/5`, add a `csat_N` tag, store `csat_score` in session state; reply *"Thank you for your feedback! 🙏 You're back with our automated assistant whenever you need anything."*; set state `active`.
   - **Invalid** → one nudge (*"Please reply with a number 1–5."*). If the next message is still not a rating, **do not trap the customer** — set state `active` and process the message normally as an AI turn.

### E. Web / custom UI CSAT
The web chat already uses `POST /chat/turn` for turns and `GET /chat/stream/{session_id}` (SSE) to receive live agent messages during a Sunshine handoff. CSAT reuses that channel:

1. On handback (agent resolves), for a web session the `zendesk-handback-webhook` publishes a **`survey` event** to the session's SSE subscribers (same fan-out the agent messages use) and sets `awaiting_survey`.
2. The frontend renders a **rating widget** (1–5) in the chat thread when it receives the `survey` event.
3. The customer picks a score → frontend calls **`POST /chat/csat`** `{ session_id, score }`.
4. The endpoint calls the **shared `record_csat(session_id, score, channel)`** (same as WhatsApp): private comment `⭐ Customer satisfaction: N/5`, `csat_N` tag, `csat_score` in session state; sets state `active`. Returns a thank-you the frontend shows as a confirmation.

The **only difference between channels is delivery** (WhatsApp text vs. SSE event + widget + `POST /chat/csat`); the recording and state transitions are identical.

## Components & changes

| Area | Change |
|---|---|
| `service.py` (`OrchestratorService`) | New methods: `begin_whatsapp_handoff(session_id, summary)` (open ticket + pause + persist state), `forward_whatsapp_to_agent(session_id, text)` (post private note), `record_csat(session_id, score, channel) -> CsatResult` (**shared** record + state transition: comment + tag + Firestore), `parse_csat(text) -> int \| None` (lenient 1–5 parse, WhatsApp), `whatsapp_state(session_id)` reader. State persisted via the existing `_persist_session_state` helper. |
| `ports.py` / `zendesk.py` | All conversation writes are **private internal notes** (handoff summary, relayed customer messages, CSAT score) — `append_conversation_comment` already posts private. Add `add_ticket_tag(ticket_id, tag)` for the `csat_N` tag. The agent's reply (the public side) comes from Zendesk, not us. `NoOpConversationLog` mirrors any new signatures. |
| `router.py` | `twilio_whatsapp_webhook` routes by `whatsapp_handoff_state` (survey → CSAT, paused → forward-to-agent, else AI + post-turn handoff check). `zendesk_support_webhook` extended to relay `whatsapp-*` agent replies to Twilio. `zendesk_handback_webhook` extended: WhatsApp → text survey; web → publish `survey` SSE event. New `POST /chat/csat` `{session_id, score}` → `record_csat(..., channel="web")`. |
| `detection.py` / `agents.py` | Unchanged gate semantics; the two-level distinction (flag = open, handoff = pause) is enforced in the webhook, not the gate. |
| **Frontend** (`features/chat/`) | New `CsatSurvey.vue` rating widget (1–5) shown when a `survey` event arrives on the SSE stream; on submit it calls `POST /chat/csat` via the chat `api/` layer and renders a thank-you. Wired into the chat store/stream handling. |
| `handoff_bridge.py` / SSE | Add a typed `survey` event to the existing per-session fan-out so the frontend can distinguish it from agent messages. |

The capture ticket id and logged count remain the source of truth in session state (from the prior dedup fix).

## Error handling

- All Zendesk/Twilio calls stay **best-effort** (logged, swallowed) — a failed comment or relay never 500s the webhook, matching existing adapters.
- If `begin_whatsapp_handoff` fails to pause, the session stays `active` (AI keeps answering) rather than silently dropping the customer.
- CSAT parsing is lenient: accepts `1`–`5`, optionally surrounded by text; anything else is "invalid".
- A session with no capture ticket yet (handoff on the very first message) creates the ticket first via the existing `ensure_conversation_ticket`.

## Testing

Unit / handler tests (co-located `test_*.py`), all using the in-memory/live-session doubles and `AsyncMock` adapters:

- **Gate vs handoff:** flag → ticket open, AI not paused; handoff → ticket open + paused + customer notified.
- **Paused routing:** inbound message while paused → posted as public comment, AI not invoked, no Twilio reply.
- **Agent relay:** `zendesk-support-webhook` for a `whatsapp-*` session → relayed via Twilio; `sim-`/web session → unchanged (no Twilio).
- **Handback → survey:** resolve → state `awaiting_survey` + survey message sent.
- **CSAT capture (WhatsApp):** valid `4` → comment + tag + `csat_score` stored + state `active`; invalid → nudge, then non-rating message resumes AI normally.
- **CSAT shared/web:** `record_csat(session_id, 5, channel="web")` → comment + tag + Firestore + state `active`; `POST /chat/csat` validates score range and returns the thank-you; handback for a web session publishes a `survey` SSE event.
- **Frontend:** `CsatSurvey.vue` renders 1–5, emits the chosen score, posts via the api layer, and shows the thank-you (vue-tsc + a component test).
- **State persistence:** state survives a simulated restart (new orchestrator, same session store).

## Rollout / config

No new env vars. Requires (already configured): `CRM_PROVIDER=zendesk`, `ZENDESK_*`, `TWILIO_*`, and the **Zendesk Support webhook + handback webhook** pointing at `/webhooks/zendesk-support` and `/webhooks/zendesk-handback` with `ZENDESK_SUPPORT_WEBHOOK_SECRET`. (These power the agent-reply relay and handback/CSAT triggers.)

## Open questions / future

- Map CSAT to native Zendesk `satisfaction_rating` for built-in reporting.
- Optional free-text comment after the rating.
- A timeout that auto-resumes the AI if the customer never answers the survey.
