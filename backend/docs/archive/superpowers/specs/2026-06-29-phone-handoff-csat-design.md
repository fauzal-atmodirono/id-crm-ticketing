# Phone Channel — Handoff + CSAT (Follow-up) — Design

**Date:** 2026-06-29
**Status:** Approved (brainstorm complete)
**Scope:** The follow-up to the phone/VOIP core bridge. Adds **human handoff
(callback + open Zendesk ticket)** and **in-call CSAT (spoken 1–5, AI-resolved
calls only)** to the existing real-time phone channel. Builds on
`docs/superpowers/specs/2026-06-28-phone-voip-realtime-bridge-design.md`.

## Context

The phone channel (merged 2026-06-29) is a real-time Twilio Media Streams ⇄ Gemini Live
bridge: a caller talks to the KB-grounded AI and the transcript is logged to a Zendesk
ticket at call end. The core spec deferred handoff and CSAT — this is that follow-up.

**Key architectural fact:** the phone channel runs through **Gemini Live**, not the text
`handle_turn`/ADK path, so the `handoff_triggered` session-state mechanism the web/WhatsApp/
email channels use does **not** apply here. Handoff and CSAT are therefore driven by **Gemini
Live function tools** the model calls — exactly like the existing `kb_search` tool.

### Decisions locked during brainstorming

- **CSAT scope:** the in-call spoken survey runs on **AI-resolved calls only** (no handoff).
  Best practice: don't survey at the bot→human handoff (issue unresolved); attribute CSAT to
  the final resolution. Handed-off calls just create the open callback ticket; their CSAT
  belongs to the later human-resolution touchpoint (out of scope here).
- **Mechanism:** two new Live function tools — `request_human_handoff` and `submit_csat` —
  consistent with how the phone channel already does `kb_search`.
- **CSAT capture:** a `submit_csat(score)` tool (structured) rather than parsing the spoken
  number out of the free-speech transcript (fragile).
- **CSAT recording:** extract the ticket-write part of the existing `record_csat` into a
  shared helper so phone CSAT lands as the same `csat_N` tag + comment, `channel="phone"`.
- **Token auth gating** is explicitly NOT in this spec (separate pre-deploy task).

## Data Flow

```
During the call (PhoneBridge.pump, alongside kb_search):
  ToolCall request_human_handoff(reason, summary)
       → bridge stores self.handoff = {reason, summary}; send_tool_response success
       → the model tells the caller "a specialist will follow up"
  ToolCall submit_csat(score)            (resolved calls only — model asks for 1–5 first)
       → bridge stores self.csat_score = clamp(score, 1..5); send_tool_response success
       → the model thanks the caller

At call end (PhoneBridge.finalize):
  if self.handoff is not None:                       # handed-off call
      ensure_conversation_ticket → post "[Handoff to human agent]\n{reason}\n{summary}"
      + transcript, status "open"; set external_id;  NO CSAT
  else:                                              # AI-resolved call
      ensure_conversation_ticket → post transcript, status "solved"; set external_id
      if self.csat_score is not None:
          record_csat_on_ticket(ticket_id, csat_score, channel="phone")
```

## Components & Changes

| File | Change |
|---|---|
| `features/chat/phone/handoff_csat_tools.py` (new) | `REQUEST_HANDOFF_TOOL` + `SUBMIT_CSAT_TOOL` (`types.Tool` declarations); pure `parse_csat_score(args) -> int \| None` (clamp/validate 1–5) |
| `features/chat/phone/bridge.py` | `PhoneBridge` gains `self.handoff: dict[str,str] \| None` and `self.csat_score: int \| None`; `pump` handles the two new tool names; `finalize` branches open/solved + CSAT |
| `features/chat/service.py` | extract `record_csat_on_ticket(self, ticket_id, score, channel)` (the comment + `add_ticket_tag(csat_N)` write) from `record_csat`; `record_csat` calls it; the phone path calls it too |
| router `phone_stream` (in `router.py`) | opens the Live session with all three tools (`[KB_SEARCH_TOOL, REQUEST_HANDOFF_TOOL, SUBMIT_CSAT_TOOL]`) and the updated system instruction (handoff + CSAT guidance) — this is where the single-tool list + instruction currently live |

The genuinely new code is **two tool declarations + their dispatch, the finalize branching,
and the shared CSAT helper.** Everything else is reuse of `ConversationLogPort` and the
existing tool-dispatch pattern.

## Behavior

### Handoff (callback + ticket)
- The model calls `request_human_handoff(reason, summary)` when it can't resolve the issue,
  the caller explicitly asks for a human, or it's a complaint/sensitive case.
- The bridge stores the intent and returns success; the model verbally reassures the caller.
- At `finalize`, the ticket is created/posted with the handoff note + transcript at status
  **`open`** so a human picks it up in Zendesk. No survey.

### CSAT (AI-resolved only)
- Near the end of a **resolved** call the model asks "how would you rate your experience, 1
  to 5?" and calls `submit_csat(score)` with the number it heard. The bridge clamps to 1–5
  and stores it. Invalid/out-of-range → ignored (no score recorded).
- At `finalize` (no handoff), the score is recorded via `record_csat_on_ticket(ticket_id,
  score, channel="phone")` → `⭐ Customer satisfaction: N/5 (via phone)` comment + `csat_N`
  tag (same format/dashboard source as every other channel).
- If the model handed off, it is instructed NOT to ask for a rating; even if a score somehow
  arrives, the handoff branch in `finalize` does not record CSAT.

### System instruction (Live session)
Adds: when to call `request_human_handoff`; on a resolved call, ask for the 1–5 rating and
call `submit_csat`, but skip the rating entirely if you handed off; keep it to one question.

## Error Handling

- Tool dispatch + ticket writes are best-effort (try/except + structlog), consistent with
  the existing bridge — a Zendesk hiccup never crashes the call.
- An out-of-range or non-integer `submit_csat` argument → no score stored (clamped/ignored),
  logged at debug.
- `finalize` keeps its existing best-effort guard (logs `session_id` + `error` + transcript
  body on failure).

## Testing

Unit (pytest-asyncio, fakes — co-located in `phone/test_bridge.py` + a tools test):
- `parse_csat_score`: valid 1–5 → int; 0/6/"x"/missing → None.
- `pump`: `request_human_handoff` tool → `self.handoff` set + success tool response; `submit_csat`
  → `self.csat_score` set; both still answer the tool so the Live turn isn't stalled.
- `finalize` handoff path → `ensure_conversation_ticket` + handoff note + transcript at
  status `open`, `set_ticket_external_id`, and **no** CSAT write.
- `finalize` resolved + score → status `solved` + `record_csat_on_ticket(..., channel="phone")`.
- `finalize` resolved + no score → solved, no CSAT.
- `record_csat_on_ticket` shared helper: posts the `⭐ … (via <channel>)` comment + `csat_N`
  tag; existing `record_csat` still passes its tests (delegates to the helper).

Live smoke test (append to `docs/testing/phone-channel-smoke-test.md`): a call where you
ask for a human → confirm an **open** Zendesk ticket with the handoff note + transcript; a
resolved call where you give a spoken rating → confirm a **solved** ticket with the `csat_N`
tag + comment.

Quality gates: `ruff format`, `ruff check`, `mypy --strict`, `pytest`.

## Known Limitations (POC)

- **No callback number:** the browser-softphone caller has a Twilio client identity, not a
  phone number, so the handoff ticket is a *record* for a human to action. A real PSTN call
  would capture Twilio's `From` for an actual callback — a real-PSTN follow-on.
- **CSAT is tag-based** (`csat_N` on the Zendesk ticket), same as all channels; the unified
  metrics/Bot-Metrics dashboard remains a separate future project.

## Out of Scope

- Authentication/rate-limiting on `POST /voice/phone/token` (separate pre-deploy task).
- DTMF (keypad) rating; an outbound/SMS survey for handed-off calls' eventual resolution.
- Capturing a real PSTN caller number / actual outbound callback.
- The metrics dashboard; retrofitting other channels.
