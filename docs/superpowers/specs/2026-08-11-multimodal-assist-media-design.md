# Multimodal AI assist: attachments reach the model

**Date:** 2026-08-11
**Status:** design approved, implementing
**Branch:** `dev-yuda`

## The bug

An agent opens a WhatsApp conversation where the customer sent a video captioned
"this one" and clicks **Suggest a reply**. The draft comes back:

> Boleh anda jelaskan "this one" yang anda maksudkan? Adakah anda mempunyai
> soalan atau perkara lain yang ingin dibincangkan?

The model is not being unhelpful. It is answering correctly, because the only
thing it received was the string `Customer: this one`.

Two layers drop the attachment, independently:

1. **`deploy/chatwoot-fork/patches/0002-ai-assist-backend.patch:134`** builds the
   request payload as pre-rendered strings from `m.content` alone. The
   `attachments` array on each message is never read. Worse, the
   `.filter(m => m.content …)` means an attachment with *no* caption is dropped
   from the transcript entirely — a voice note or photo sent on its own is
   invisible to the AI, with no trace that it existed.

2. **`features/assist/router.py`** types `messages` as `list[str]` and passes
   `contents=user_prompt` — a plain string — to Gemini. There is no channel
   through which media could arrive even if the frontend sent it.

No patch in `deploy/chatwoot-fork/patches/` references `attachments` at all.

This affects every image, video, voice note, and document, on every assist
action, for every tenant. It is deterministic, not intermittent.

## What already exists

The capability is in the codebase, just not on this path:

- `agent/app/services/media.py` downloads Chatwoot attachments, following
  Active Storage 302 redirects (load-bearing — see commit `3a009f2`) and falling
  back to a per-`file_type` mime default when Content-Type is missing or generic.
- `agent/app/services/orchestrator.py:597-692` selects one attachment per kind
  off the current customer turn and applies a whole-turn byte budget with a
  defined drop order.
- `backend/…/features/chat/service.py:735-762` builds `types.Part.from_bytes`
  from base64 with per-kind decode guards.

All of it serves the **automatic agent-bot** (`CHAT_AGENT_ENABLED` +
`WHATSAPP_MEDIA_UNDERSTANDING_ENABLED`, both default off). The agent-facing
assist buttons were never wired to any of it.

`assist_gemini_model` is `gemini-2.5-flash`, which ingests image, video, audio,
and PDF inline.

## Design

Two halves that fail independently.

### A — Markers (always on, no flag)

The transcript gains a marker for every attachment, so the model always knows
media exists and *where in the thread* it sits, even when the bytes cannot be
sent. A caption-less message stops being filtered out.

This half is a straight bug fix and is not flag-gated: a voice note vanishing
from the transcript is wrong under every configuration.

The fork patch stops pre-rendering strings and forwards structured data instead,
so it holds no label vocabulary of its own:

```jsonc
[{ "role": "customer", "content": "this one",
   "attachments": [{ "file_type": "video" }] }]
```

`messages: list[str]` remains accepted and renders exactly as it does today, so
every existing caller and test is unaffected.

Rendering — including marker text — happens in the backend from one registry.
A second label table in JavaScript would be a second registry to drift.

### B — Bytes (flag-gated)

`ASSIST_MEDIA_UNDERSTANDING_ENABLED`, default `false`.

The backend fetches the conversation from the **Chatwoot API** and reads
`data_url` from *that* response, never from the client. The URL therefore comes
from a trusted source and needs no allowlist; a client-supplied URL would have
made the backend an SSRF gadget pointed at, among other things, the GCE metadata
endpoint. The download also runs on the VM rather than over the agent's uplink,
so the button stays responsive.

Text still comes from the browser. Sourcing it server-side would mean
`/assist/suggest` stops working whenever the Chatwoot API is unreachable, and
buys nothing.

```
agent clicks an assist action
  browser  → POST /assist/{suggest,summarize,ask}
                  { conversation_id, messages[] (structured) }
  backend  → Chatwoot GET /conversations/{id}/messages   (trusted URL source)
           → scan window, most recent instance per ingestible kind
           → download data_url (follow redirects) → resolve mime → budget
           → Gemini: [text Part, …media Parts]
  ←  { draft | summary | answer, sources }
```

## The kind registry

One table drives labels, mime fallbacks, ingestibility, and drop order.

| `file_type` | label | default mime | ingestible | drop priority |
|---|---|---|---|---|
| `image` | a photo | `image/jpeg` | yes | 2 |
| `video` | a video | `video/mp4` | yes | 0 |
| `audio` | a voice note | `audio/ogg` | yes | 3 |
| `file` | a document | from Content-Type | if mime ingestible | 1 |
| `location` | a location | — | no | — |
| `contact` | a contact card | — | no | — |
| *unknown* | a file | from Content-Type | if mime ingestible | 0 |

Two rules keep this open-ended rather than an enumeration:

- An unrecognised `file_type` still yields a marker. Nothing silently vanishes.
- Ingestibility is decided by the **resolved mime type** against Gemini's
  supported set, not by the kind name. A new Chatwoot attachment type that
  happens to be a PDF is understood without a code change.

Drop priority replaces the agent service's hardcoded `_MEDIA_DROP_ORDER`.
Lower drops first: video is biggest and least often *is* the message; audio
drops last because a voice note usually is the entire message. Unknown kinds
drop first, since we know least about them.

## Scope of media selection

The most recent instance of each ingestible kind found anywhere in the
20-message window, from non-private incoming (`message_type == 0`) messages.

Window-wide rather than current-turn-only so that clicking Suggest several
turns after the video arrived still sees it. The per-kind cap and the byte
budget bound the cost.

## Where each piece lives

**Backend**

| File | Change |
|---|---|
| `features/assist/media_registry.py` | new — the table above, pure stdlib, no third-party imports |
| `features/assist/assist_media.py` | new — transcript rendering, window scan, fetch, budget, Part building |
| `features/assist/chatwoot_context.py` | add `get_messages()`; this client is already read-only and never raises, which is exactly the contract needed |
| `features/assist/router.py` | structured `messages`, media on all three endpoints, marker-aware `_retrieval_query` |
| `platform/config.py` | `assist_media_understanding_enabled`, `assist_media_max_bytes` |
| `main.py` | pass `ChatwootContextClient(settings)` into `build_assist_router` |

**Agent service**

| File | Change |
|---|---|
| `app/services/media_registry.py` | new — mirror of the backend table |
| `app/services/media.py` | resolve mime via the registry instead of a local dict |
| `app/services/orchestrator.py` | `_MEDIA_KINDS` / `_MEDIA_DROP_ORDER` derived from the registry |

Two copies, not an import: `agent/` and `backend/` are separate services with no
shared package, and CLAUDE.md is explicit that they communicate only over HTTP.
A parity test (below) is what keeps them honest.

**Fork patch** — `0002-ai-assist-backend.patch` forwards structured messages.

**Docs** — `deploy/tenants/example.env` documents both new settings.

The flag gets **no** line in `docker-compose.tenant.yml`'s unified block. That
passthrough exists only for flags whose feature also has a `hasFeature(…)` gate
in the SPA (see the comment at `docker-compose.tenant.yml:51-71`); this one has
no frontend gate, because markers ship unconditionally.

## Prompt changes

A shared media instruction is appended to the system prompt **only when media is
actually attached**, so the no-media prompt stays byte-identical:

> If the customer attached a photo, video, voice note, or document, it is
> attached to this request — use what it actually shows. Do not ask the customer
> to describe or re-send something they have already sent.

Without this line the model still hedges even with the video in hand. This is
the sentence that closes the reported bug.

`_retrieval_query` reads `content` off structured messages rather than
regex-stripping markers out of rendered strings — we should not parse our own
output. Legacy `list[str]` keeps today's `Customer:`-prefix partition logic. A
caption-less message contributes an empty body and is skipped, consistent with
commit `f00c10b`.

## Error handling

Media is strictly additive. The invariant: **no media condition may turn a
working draft into no draft.**

| Failure | Behavior |
|---|---|
| Chatwoot API down / `get_messages` fails | Log, no media. Markers survive — they came from the browser. |
| `data_url` 404 / timeout / redirect loop | That kind skipped; other kinds proceed. Marker still reports it existed. |
| Resolved mime not ingestible | Marker only, no bytes. Not an error, not logged as one. |
| Over budget | Drop by priority, one warning log per dropped kind. |
| Decode failure | Per-kind `try/except`, mirroring `service.py:735-762`. |
| Gemini rejects the request | Existing path; the frontend already catches and logs. |
| Flag off | Zero Chatwoot calls. Markers still rendered. |

## Testing

- **Registry parity** — the backend suite loads the agent service's
  `media_registry.py` by path and asserts the two tables are identical. Both
  modules are pure stdlib specifically so this import is safe and cheap. Skips
  with an explicit reason if the sibling checkout is absent.
- **`test_media_registry.py`** — label/mime/ingestible/drop-priority resolution,
  including unknown `file_type` and Content-Type-driven ingestibility.
- **`test_assist_media.py`** — window scan takes most-recent-per-kind; private
  and outgoing messages ignored; budget drop order; Active Storage 302 followed
  (`respx`); generic Content-Type falls back to the registry default; unknown
  kind yields marker and no bytes.
- **Router** — media Parts reach the stub client on all three endpoints; a
  legacy `list[str]` payload produces byte-identical output to today; flag off
  makes zero Chatwoot calls; a raising Chatwoot client still returns a draft.
- **Patch** — assert the new structured mapping in patch `0002`, following the
  existing style of `test_p7_task7_faq_composer_patch.py`.

## Known cost trade-off

Enabling media on `/summarize` and `/ask` means an agent clicking **Summarize**
can pull a 14 MB video and pay video tokens for five bullet points that rarely
depend on it. Accepted deliberately: the flag defaults off and the budget caps
the damage. If cost becomes a problem, per-action opt-out is the cheap lever and
summarize is the first to lose it.

## Out of scope

- Outbound attachments (agent-sent media) — only customer attachments are read.
- Any change to the automatic agent-bot's behavior beyond sourcing its registry
  from the shared table. Its flags and turn boundary are untouched.
- Storing or caching downloaded bytes. Every request re-fetches.
