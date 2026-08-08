# P7 — AI Conversational Quality

**Date:** 2026-08-08
**Programme:** `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p7-ai-conversational-quality.md`
**Closes:** 13 PARTIAL requirements
**Effort:** 3–4 weeks · **Wave:** 3 · **Blocked by:** nothing

---

## 1. The problem, precisely

Six independent AI shortfalls that share a prompt-and-retrieval surface. They are
grouped because they touch the same four files, not because they are one feature.

**Sentiment is defined, surfaced, and never written.** This one deserves care
because it looks built:

- `features/chat/models.py:8` — `Sentiment = Literal["positive", "neutral", "negative"]`
- `models.py:67` — `sentiment: Sentiment | None = None` on the result
- `detection.py:19-20` — ticket creation is gated on `sentiment in _NEGATIVE_SENTIMENTS`
- `router.py:95, 1149` — surfaced on the API response
- `service.py:597, 1145` — populated from `session_state.get("sentiment")`

**Nothing anywhere writes `session_state["sentiment"]`.** Verified by search on
2026-08-08. So the field is always `None`, the `detection.py` gate is inert, and
the API returns `null` on every response.

What actually works is a prompt-driven proxy: `prompts.py:54` instructs the model
that on negative sentiment it should call `emit_handoff_tool(reason=
"negative_sentiment")`, and `adapters/chatwoot.py:142` turns that into complaint
labels plus priority. That is a **binary complaint flag**, not the four-level
scale §4.24 asks for (satisfied / neutral / dissatisfied / urgent), and there is
no dynamic tone adjustment — only a static "## Tone" paragraph in the prompt.

**There is no translation feature at all.** §4.3 asks for EN / BM / Chinese /
Tamil. What exists is language *mirroring* (the model replies in the language it
was addressed in) plus a per-assistant default `language` field. **Tamil is not
named anywhere in the codebase**, and `SUMMARIZER_INSTRUCTION` constrains output
to `en|ms|zh`. There is no translation of inbound text *for the agent to read* —
which is the actual requirement: an agent who does not read Tamil cannot handle a
Tamil conversation no matter how well the bot replies.

**The authored `keywords` field is stored and never ranked on.** `live_faq.py`
persists `keywords` on every FAQ entry (lines 132, 147, 161, 223, 236), and
`search()` calls `_rank(entries, query_embedding, limit)` — cosine similarity on
the embedding alone. The CRM team is authoring keywords into a field that does
not affect results. §4.19, §4.22 and §3.2.1 all ask for keyword matching.

**The multimodal pipeline is complete and asks the model nothing.**
`media.py` → `_apply_media_budget` → `clients/proton.py` posts
`image_base64`/`video_base64` → `service.py:483` `types.Part.from_bytes`. The
image reaches Gemini. **No prompt anywhere instructs the model to diagnose the
fault or ask follow-up questions** (§4.20) — it is appended to a generic
instruction.

**Previous resolved cases are never indexed.** §4.23 asks for suggestions from
enquiries, the KB **and previous resolved cases**. `/assist/suggest` grounds on
FAQs, ingested docs and scraped web. No code writes a resolved conversation into
any knowledge store.

**The summariser must be asked.** §4.27 says "auto-summarize at end of
conversation". `POST /assist/summarize` is agent-triggered on demand.

## 2. What this package delivers

1. A sentiment classifier that writes the field the system already exposes, on
   the four-level scale, with tone adjustment.
2. Agent-facing translation, with an explicit Tamil decision.
3. Hybrid keyword + semantic FAQ ranking, and 1-click apply via the fork
   surface that can actually write the composer.
4. Media-diagnosis prompting.
5. A resolved-case index.
6. Auto-summary on resolve.
7. A calibration methodology with acceptance thresholds (§2.2.4, §8.1.8).

## 3. Design

### 3.1 Sentiment

**Write the field that already exists.** The plumbing — type, result field,
detection gate, API surface — is all in place and correct. Only the producer is
missing, which is why this is 1–2 weeks rather than a rebuild.

Two changes:

- **Widen the scale to four levels.** §4.24 asks for satisfied / neutral /
  dissatisfied / **urgent**. The existing `Literal["positive","neutral","negative"]`
  becomes `["positive","neutral","negative","urgent"]`. `detection.py`'s
  `_NEGATIVE_SENTIMENTS` gains `urgent`, so the existing ticket-creation gate
  keeps working and becomes stricter rather than changing shape.
- **Classify per turn**, writing `session_state["sentiment"]`, so
  `service.py:597` and `:1145` pick it up unchanged.

**Mechanism: a tool call on the existing turn, not a second model round-trip.**
The agent already makes one Gemini call per turn with forced function calling. A
separate classification request would double latency and cost on the hot path for
a signal used to set a label. Instead the turn's tool schema gains an optional
`sentiment` argument, which the model fills alongside whatever else it decides.

The fallback is `neutral`, never `None` — and this is a deliberate reversal of
the current state. Today `None` means "never classified" and reads identically to
"we looked and it was fine". Once a classifier exists, absent means the model
declined to answer, and `neutral` is the safe interpretation: it does not trip
the escalation gate.

**Tone adjustment** replaces the static "## Tone" paragraph with one selected by
the current sentiment — measured, apologetic and unhurried for `negative`;
acknowledging urgency and skipping pleasantries for `urgent`. Operator-editable
in Knowledge Settings, like every other persona element, because tone is exactly
the thing a client will want to adjust after hearing one call.

**Sentiment is written to the conversation as a custom attribute** so it reaches
BigQuery via the existing mapping and P8 can report on it. A sentiment nobody can
report is half a feature.

### 3.2 Translation (§4.3)

Two distinct needs that the current "language mirroring" conflates:

| Need | Direction | Who reads it |
|---|---|---|
| The bot replies in the customer's language | outbound | the customer — **works today** |
| The agent reads an inbound message they do not speak | inbound | the agent — **does not exist** |

Only the second is missing, and it is the one §4.3 is about. Design: a translate
action on the conversation — a fork-side button and a `POST /assist/translate`
endpoint — that renders the inbound message in the agent's language as a private
note or an inline panel. On demand, not automatic: translating every message
would triple token cost for conversations where the agent reads the language
fine.

**Tamil needs a decision, not an implementation.** Gemini supports Tamil; the
open question is whether *quality* is acceptable for customer-facing replies in a
regulated automotive support context, and nobody has tested it. So:

- Inbound translation to English/BM for the agent: ship for Tamil. Low risk — an
  imperfect translation an agent reads is far better than a message they cannot
  read at all.
- **Outbound Tamil replies: gated off by default**, with a documented evaluation
  set (30 real Tamil enquiries, scored by a Tamil speaker) as the acceptance
  gate. Shipping unverified Tamil replies to customers is a reputational risk
  taken on the client's behalf, and it should be their decision with evidence in
  front of them.

`SUMMARIZER_INSTRUCTION`'s `en|ms|zh` constraint widens to include `ta`.

### 3.3 Hybrid FAQ ranking and 1-click apply

**Ranking:** `_rank` becomes a weighted blend — cosine similarity plus a keyword
overlap score against the authored `keywords` field, with the weight configurable
and defaulting to a small keyword contribution.

The default is deliberately small. Semantic search works today; the keyword
signal is there to fix the specific case semantic search handles badly — exact
part numbers, model codes, `e.MAS7`, and Malay abbreviations that embed poorly.
A large keyword weight would regress the common case to fix the rare one.

**1-click apply:** the gap analysis identified both the blocker and the way
round it. The dashboard-app iframe is cross-origin and sandboxed, with no API to
write the reply editor — documented as an intentional degradation in the
agent-app README, which is why it offers copy-to-clipboard. But **patch `0002`'s
AI-assist path already writes into the composer**, because it is fork code rather
than an iframe app.

So: move FAQ suggestions onto the fork surface. Not a new mechanism — the same
one already working for AI-assist. This closes §4.28 and the "1-click" half of
§4.19 and §3.2.1 without fighting the iframe sandbox.

**Pop-up vs side panel** (§4.22 asks for a pop-up): the fork surface can render
a transient suggestion strip above the composer when confidence exceeds a
threshold, dismissible, and off by default. Anything more intrusive in an agent's
typing path will be switched off in week one and should not be built.

### 3.4 NLU robustness (§4.4)

Today robustness is *inherited* from Gemini and embedding similarity — never
explicitly engineered. There is no normaliser, no synonym dictionary, and **no
test case anywhere in SMS-style Malay**: the RFP's own example, `brp lama siap?
nk service`, has never been run.

Three things, in increasing cost:

1. **A test corpus first.** 50 real-shaped Malay/Manglish SMS-style enquiries
   with expected intents, run as a suite. This is the cheapest item here and the
   only one that tells anyone whether the other two are needed.
2. **A normaliser** — lowercase, strip repeated characters, expand a small
   abbreviation dictionary (`brp`→`berapa`, `nk`→`nak`, `tq`→`terima kasih`)
   applied **to the retrieval query only, never to the text shown to the model
   or the agent.** Normalising what the model sees would strip exactly the
   register cues that make it answer in the customer's voice.
3. **A synonym dictionary** feeding the keyword half of §3.3's ranking.

Corpus first, because it is entirely possible Gemini already handles this and
the correct engineering answer is a documented test result rather than a
normaliser nobody needed.

### 3.5 Media diagnosis (§4.20)

The pipeline works; the prompt does not ask for anything. Add a media-specific
instruction — describe what is visible, identify the likely fault, state
confidence, and ask the one follow-up question that would most reduce
uncertainty — appended when a media part is present.

Operator-editable in Knowledge Settings, because "what should the AI ask about a
photo of a scratched bumper" is a business question.

**`WHATSAPP_MEDIA_UNDERSTANDING_ENABLED` has never been confirmed against a real
WhatsApp number.** That is a verification task, not a build task, and this
package's definition of done includes it — a prompt improvement that has never
run end to end is not a delivered feature.

### 3.6 Resolved-case retrieval (§4.23)

On resolve, index a compact record — the case summary (§3.7 generates it
anyway), category, subcategory, `case_detail`, resolution, and the outcome — into
the same pgvector store the operator KB uses, in a separate namespace.

**Separate namespace, not mixed with the FAQ corpus**, for two reasons: an
operator must be able to purge machine-generated content without touching
authored content, and resolved-case hits should be labelled as such in the
suggestion panel so an agent knows they are looking at what a colleague did last
month, not at approved guidance.

**PII:** a resolved case contains customer names, phone numbers and vehicle
details. The index stores the *summary*, not the transcript, and the summariser
prompt is instructed to omit identifiers. This is a mitigation, not a guarantee —
full PII masking is R16, blocked on Q7 — and the design says so rather than
implying the problem is solved. Indexing is off by default for that reason.

### 3.7 Auto-summary on resolve (§4.27)

Hook the existing `POST /assist/summarize` to the resolve event. The gap analysis
estimates ~2 days and it is right: the summariser, the private-note posting path
(patch `0002`) and the resolve webhook all exist.

Idempotent per resolve — a case resolved, reopened and resolved again produces
two summaries, appended, not one overwritten. The second summary is about
different work.

### 3.8 Calibration methodology (§2.2.4, §8.1.8)

Both requirements are PARTIAL for the same reason: the tunables exist (persona,
prompts, lifecycle messages, `KB_SCORE_FLOOR`) and there is **no methodology, no
accuracy baseline and no acceptance threshold**.

The deliverable is a document plus a runnable evaluation set:

- a labelled set per capability (intent, FAQ match, sentiment, summary quality),
- a baseline measured **before** any tuning,
- stated acceptance thresholds agreed with the client,
- a re-run procedure and a cadence.

Without a baseline, "we calibrated the AI" is unfalsifiable. With one, §8.1.15's
monthly AI-accuracy review has something to report.

## 4. What could go wrong

| Risk | Mitigation |
|---|---|
| Sentiment classification adds latency to every turn | Same call, extra tool argument — no second round-trip |
| Widening the sentiment enum breaks the existing detection gate | `urgent` added to `_NEGATIVE_SENTIMENTS`; the gate becomes stricter, never looser; regression test |
| Keyword weighting regresses semantic search | Small default weight; the evaluation set from §3.8 is the acceptance gate |
| Unverified Tamil replies reach customers | Outbound Tamil off by default; inbound translation only until an evaluation is signed off |
| The suggestion pop-up disrupts agent typing | Off by default, confidence-gated, dismissible |
| Resolved-case index leaks PII | Summary not transcript; identifier-omission instruction; off by default; R16 named as the real fix |
| Normalisation strips register cues | Applied to the retrieval query only, never to model or agent text |

## 5. Testing

- **Sentiment** (`test_sentiment.py`): each level; `neutral` fallback; `urgent`
  trips the gate; the attribute reaches the conversation; latency unchanged
  (one model call per turn asserted).
- **Translation** (`test_translate.py`): endpoint returns target language;
  Tamil inbound works; outbound Tamil blocked while the flag is off.
- **Ranking** (`test_faq_hybrid_rank.py`): keyword hit lifts an entry semantic
  search ranked lower; the weight is configurable; weight 0 reproduces today's
  ordering exactly.
- **NLU corpus** (`test_malay_sms_corpus.py`): 50 cases, a reported pass rate,
  and the RFP's own `brp lama siap? nk service` as a named case.
- **Media** (`test_media_prompt.py`): the diagnosis instruction is present when
  media is attached and absent otherwise.
- **Resolved-case index** (`test_resolved_case_index.py`): written on resolve;
  namespace separated; purgeable; labelled in suggestions.
- **Auto-summary** (`test_auto_summary.py`): fires on resolve; appends on
  re-resolve; failure is swallowed.

## 6. Feature flags

| Setting | Default | Effect |
|---|---|---|
| `SENTIMENT_CLASSIFIER_ENABLED` | `false` | Off = field stays `None`, as today |
| `SENTIMENT_TONE_ADJUSTMENT_ENABLED` | `false` | Off = static tone paragraph |
| `TRANSLATION_ENABLED` | `false` | Off = no translate action |
| `TRANSLATION_OUTBOUND_TAMIL_ENABLED` | `false` | Off until the evaluation is signed off |
| `FAQ_KEYWORD_WEIGHT` | `0.0` | 0 = today's pure-semantic ranking |
| `FAQ_SUGGESTION_POPUP_ENABLED` | `false` | Off = side panel only |
| `MEDIA_DIAGNOSIS_PROMPT_ENABLED` | `false` | Off = today's generic instruction |
| `RESOLVED_CASE_INDEX_ENABLED` | `false` | Off = nothing indexed |
| `AUTO_SUMMARY_ON_RESOLVE_ENABLED` | `false` | Off = agent-triggered only |

`FAQ_KEYWORD_WEIGHT` is a float defaulting to the identity value — the same
pattern P4 uses for `REPORTING_TIMEZONE`.

## 7. Requirements closed

2.2.4, 3.2.1, 4.3, 4.4, 4.19, 4.20, 4.22, 4.23, 4.24, 4.27, 4.28, 8.1.8, B-WA-02.

**Stated limits:** outbound Tamil ships disabled pending a signed-off evaluation;
PII in the resolved-case index is mitigated, not solved (R16, blocked on Q7); and
`WHATSAPP_MEDIA_UNDERSTANDING_ENABLED` must be verified against a real WhatsApp
number before §4.20 is claimed.
