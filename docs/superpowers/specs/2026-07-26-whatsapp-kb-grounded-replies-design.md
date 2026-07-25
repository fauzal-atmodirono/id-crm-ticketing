# WhatsApp KB-grounded replies — design

**Date:** 2026-07-26
**Status:** Approved (brainstorming)
**Branch:** dev-yuda (do not merge to main — see memory `no-merge-to-main`)

## Problem

The WhatsApp inbox (Proton tenant, Chatwoot inbox 3, Twilio) is served by the
**agent-service** auto-reply bot (`agent/app/services/orchestrator.py` →
`agent/app/ai/gemini.py`). That local Gemini `decide()` drafts replies from the
conversation transcript alone — it is **not grounded** in the Knowledge base.
The **website** widget, by contrast, answers via the backend
(`proton-conversational-ai`, vendored at `backend/`) which grounds every answer
on the tenant KB (Vertex AI Search) through a `kb_search` tool.

Goal: WhatsApp answers should be KB-grounded like the website, **without losing**
the agent-service pipeline features already configured on that inbox (SOP
disclaimer, `auto`/`suggest` mode, conversation lifecycle, escalation, routing).

## Approach (A — chosen)

Keep the agent-service as the WhatsApp driver. Keep `gemini.decide()` as the
**router** (it still chooses `send_reply` vs `escalate_to_ticket` vs
`handoff_to_human`). Only when the decision is `send_reply`, **source the answer
text from the backend copilot** (`POST /assist/copilot`) instead of the local
draft. The copilot uses the same KnowledgePort + `kb_search` + assistant persona
as the website, so answers are genuinely KB-grounded.

Rejected alternatives:
- **B — backend's native Chatwoot bot** (`handle_turn` via the backend's own
  `/webhooks/chatwoot`): same engine as the website, but not per-inbox gated
  (would answer other inboxes too) and bypasses the agent-service SOP features.
- **C — agent delegates to `/chat/turn` (handle_turn)**: answers identical to
  website incl. product carousel, but couples to `handle_turn`'s session +
  handoff-bridge state, which can tangle with the agent-service's own handoff.

## Components

### 1. `ProtonConfigClient.copilot_answer(...)` — `agent/app/clients/proton.py`

New async method on the existing client (which already carries the
`x-api-key: proton_backend_key` header and an httpx client):

```
async def copilot_answer(
    self, conversation_id: str, thread: list[dict], inbox_id: int | None
) -> str | None
```

- `POST /assist/copilot` with JSON
  `{"conversation_id": conversation_id, "thread": thread, "inbox_id": inbox_id, "assistant_id": None}`.
- On 200: return `data["answer"]` when it is a non-empty string, else `None`.
- On any exception / non-2xx / missing-empty answer: return `None` (fail-open).
- **Not cached** (unlike the GET config calls); this is a per-turn call.
- The copilot request body: `thread` requires ≥1 item, each item
  `{"role": "user"|"assistant", "content": <non-empty str>}`.

### 2. Thread builder — `agent/app/services/orchestrator.py`

A pure helper that maps Chatwoot messages → copilot `thread`:

```
def _build_thread(message_list: list[dict]) -> list[dict]
```

- Take the last N (=20, same window as `_build_context`) messages.
- Skip `private` messages, skip activity/template types, skip empty `content`.
- `message_type == 0` (incoming) → `{"role": "user", ...}`;
  `message_type == 1` (outgoing) → `{"role": "assistant", ...}`.
- Preserve order; the customer's latest message is naturally last.
- Return `[]` if nothing qualifies (caller then skips copilot, fail-open).

To avoid a second Chatwoot API round-trip, `_build_context` and `_build_thread`
share one `get_messages` fetch. Refactor `_process_conversation` to fetch the
message list once and pass it to both builders (keeps the single-fetch,
fresh-state invariant).

### 3. Orchestrator graft — `agent/app/services/orchestrator.py`

In `_process_conversation`, after `decision = await gemini.decide(...)` and
`_log_decision`, before `_execute_decision`:

```
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

- Only `send_reply` is affected. `escalate_to_ticket` / `handoff_to_human`
  never call the copilot.
- `answer is None`/empty → `decision.args["text"]` unchanged (local draft kept).
- `_execute_decision` is unchanged: it posts `decision.args["text"]` publicly
  (auto) or as a `🤖 Suggested reply` private note (suggest).

### 4. Config — `agent/app/config.py` + `deploy/tenants/example.env`

New field `kb_grounded_replies: bool = False`. Documented in `example.env`
under the agent/AI section. Off by default → byte-identical to today. Set
`KB_GROUNDED_REPLIES=true` on the Proton tenant only.

## Data flow

```
WhatsApp → Twilio → Chatwoot inbox 3 → agent-bot /webhooks/chatwoot/bot
  → debounce → gemini.decide()  ──(escalate/handoff)──> unchanged paths
                     │
                 (send_reply)
                     │
        _build_thread(messages) → ProtonConfigClient.copilot_answer
                     │                     │
                     │            backend POST /assist/copilot
                     │            (kb_search over Vertex AI Search)
                     │                     │
                     └──── answer ◄────────┘
                     │
        decision.args["text"] = answer (if non-empty)
                     │
        _execute_decision → Chatwoot create_message
              (auto: public / suggest: private note)
```

## Error handling

Fail-open throughout (matches the agent-service background-task invariant —
never raise for expected downstream failures):
- Backend down / timeout / non-2xx / empty answer → keep the local draft.
- `kb_grounded_replies=false` or `proton` unconfigured or `inbox_id` unknown →
  identical to today.
- `copilot_answer` swallows its own exceptions and returns `None`; the
  orchestrator never lets a copilot failure abort the reply.

## Citations

Answer text only — no source list appended. Matches the backend's own WhatsApp
reply behavior (it sends `turn_result.reply` text) and keeps the chat clean.
(`sources` from the copilot response are ignored.)

## Testing (TDD, pytest + respx; agent suite)

- `copilot_answer`: respx-mock `POST /assist/copilot` →
  (a) 200 with `{"answer": "..."}` returns the string;
  (b) 200 with empty/missing answer → `None`;
  (c) 500 / connect error → `None`.
- `_build_thread`: role mapping (incoming→user, outgoing→assistant), drops
  private/empty/activity, preserves order, `[]` when nothing qualifies.
- Orchestrator (respx + injected gemini):
  - flag on + `send_reply` + copilot returns answer → posted text == copilot
    answer (auto → public);
  - flag on + copilot returns `None` → falls back to local draft text;
  - flag **off** → local draft (behavior-preserving);
  - decision `escalate_to_ticket` / `handoff_to_human` → copilot **not** called.

## Deploy

1. Rebuild `platform-agent` image from `agent/` on the VM (`/opt/platform`).
2. `docker compose -p proton ... up -d agent` to recreate.
3. Set `KB_GROUNDED_REPLIES=true` in `deploy/tenants/proton.env`.
4. Smoke: WhatsApp a product question in a fresh conversation → KB-grounded
   answer posted publicly.

Available to other tenants via `example.env` (default off).

## Out of scope

- Source citations in the WhatsApp bubble.
- Changing the router (`decide()`) or escalation/handoff/lifecycle logic.
- Product-carousel / rich `handle_turn` behavior (that's Approach C).
