# Context-aware Reply Suggestion (`/assist/suggest`)

**Date:** 2026-07-27
**Status:** Approved (design)
**Scope:** `backend/apps/backend/src/chatbot/features/assist/router.py` only.

## Problem

In the Chatwoot agent UI, clicking **Reply Suggestion** pre-fills the composer
with an AI-drafted customer-facing reply (plus a `Sources:` line). In a real
Proton conversation — a customer gave their name, phone, email, chosen model
(S70) and preferred dealer (Bangsar), and the bot had *already* sent the handoff
message *"Thanks for reaching out. I am connecting you with one of our human
agents…"* — the suggested reply just parroted that handoff:

> "Baik, Bangsar telah dicatat. Kami telah menerima semua informasi yang
> diperlukan. Perwakilan PROTON kami akan segera menghubungi Anda…"

It added no value: it reacted to the last word (`"bangsar"`) instead of reading
the whole thread and connecting the dots, and it re-announced a handoff that had
already happened.

### Root cause

The `/assist/suggest` handler (`router.py:199-212`) has two design limits:

1. **Retrieval is grounded on a single message.**
   `query = req.messages[-1]` (`router.py:206`). The last turn was `"bangsar"`,
   so KB retrieval was derailed to *Test Drive* / *Register Your Interest*
   articles, and the reply followed suit. The inline comment says "the
   customer's latest message," but `messages[-1]` is simply the last turn — it
   can even be an outgoing agent/bot line.

2. **The prompt tells the model to react to the latest message in isolation.**
   `_SUGGEST_SYSTEM` (`router.py:52-63`): *"write a concise, professional reply
   to the customer's latest message."* It is never told to synthesize the whole
   conversation or to notice that a handoff / "we'll contact you" line was
   already sent.

Note: the transcript the frontend sends (`messages`, built from incoming +
outgoing messages) **already contains** the prior handoff line as an `Agent:`
turn. So handoff-awareness needs no new request field or Chatwoot fork patch —
only a prompt that tells the model to use what it already sees.

## Approach

**Backend-only: smarter grounding + smarter prompt.** All changes live in
`router.py`. No `SuggestRequest` change, no Chatwoot frontend (fork) patch, no
fork-image rebuild. Rejected alternative: passing an explicit handoff/status
boolean from the frontend — more surface area and a heavy fork-image rebuild,
for a signal already present in the transcript.

## Design

### 1. Retrieval grounding — build the KB query from the customer's intent

Replace `query = req.messages[-1]` with a small pure helper:

```python
def _retrieval_query(messages: list[str], max_turns: int = 6) -> str:
    """Build the KB query from the customer's turns, not just the last line.

    messages are "Customer: ..." / "Agent: ..." strings (see the frontend
    composer). Grounding on the whole customer intent keeps retrieval from being
    derailed by a one-word last turn like "bangsar". Falls back to the last
    message when no customer turn is present.
    """
    customer = [
        m.split(":", 1)[1].strip()
        for m in messages
        if m.split(":", 1)[0].strip().lower() == "customer" and ":" in m
    ]
    if not customer:
        return messages[-1]
    return "\n".join(customer[-max_turns:])
```

`suggest()` calls `query = _retrieval_query(req.messages)`. Everything else in
the handler (persona, `_kb_context`, `_generate`) is unchanged.

### 2. Prompt rewrite — read the whole thread, don't repeat a handoff

Replace `_SUGGEST_SYSTEM` with instructions that: (a) read the *entire*
conversation and connect the dots (what the customer wants, what they've already
provided, what has already been said/done); (b) if the request is already
complete and a handoff / "we'll contact you" line was already sent, do **not**
repeat it — instead write a short, specific confirmation echoing the concrete
details (model, dealer/location, how they'll be contacted); (c) otherwise answer
or advance using the FAQ context. Keep the existing language-match,
no-salutation, and return-only-text rules verbatim.

Draft:

```
You are a customer-support agent for Proton Holdings.
Read the ENTIRE conversation below — not just the last line — and connect the
dots: what the customer wants, which details they have already provided, and
what the agent or bot has already said or done.

Then write the single most useful next reply to the customer:
- If the customer's request is already complete (for example, every detail for a
  booking or request has been collected) AND the agent/bot has already told them
  they are being connected to a human or will be contacted, do NOT repeat that
  handoff. Instead write a brief, specific confirmation that reflects the
  concrete details they gave (such as the model, the dealer or location, and how
  they will be contacted).
- Otherwise, answer or advance the conversation using the FAQ context below.

LANGUAGE (critical): reply in the EXACT SAME language as the customer's latest
message. If they wrote in English, reply in English; if in Malay, reply in
Malay. Never switch languages and never default to Malay when the customer wrote
in another language.
Do not include a salutation or sign-off.
Return only the reply text, nothing else.

FAQ context:
{faq_context}
```

The `{faq_context}` placeholder and `.format()` call site (`router.py:208`) are
unchanged. Persona prefixing (`_apply_persona`) still wraps the result.

### 3. Tests

Co-located with the existing assist suite in `backend/apps/backend`. `_generate`
stays mocked (no live Gemini), as in the current tests.

- **`_retrieval_query`** (pure): multi-turn thread → returns customer-only turns
  in chronological order, capped to the most recent `max_turns`; a trailing
  `Agent:` line is excluded; a thread with no `Customer:` turn falls back to
  `messages[-1]`.
- **Prompt content**: assert `_SUGGEST_SYSTEM` contains the connect-the-dots and
  no-duplicate-handoff instructions and still contains the language-match and
  return-only-text rules (guards against regressions).
- **Handler wiring**: existing `/suggest` endpoint test still passes; the KB
  search is invoked with the customer-intent query rather than the raw last turn
  (assert on the mock's received query).

## Out of scope

- `/assist/copilot` (agent-facing Q&A) and its playbooks/scenarios.
- The `agent/` agent-bot orchestrator and the `/chat/turn` handoff flow.
- Any Chatwoot fork patch or `SuggestRequest` model change.
- `/assist/summarize` and `/assist/ask` prompts.
