# AI calibration baseline — methodology (P7 task 10)

> **UNMEASURED IN THIS ENVIRONMENT.** This document defines the four
> labelled evaluation sets, the runner that scores against them, and the
> re-run procedure. It does **not** contain a real number, because this
> sandbox has no real Gemini/Vertex credentials
> (`GOOGLE_API_KEY=test-key`, every model client stubbed — see the repo
> root `CLAUDE.md`). Per RFP §2.2.4 ("the AI shall be calibrated against a
> labelled evaluation set") and §8.1.8 (the AI-accuracy review), "we
> calibrated the AI" is unfalsifiable without a before-number — this
> document exists so the client's monthly review (§8.1.15) has something to
> report against once that number exists. A fabricated number would be
> worse than none: it would be cited in a client review as evidence of
> calibration that never happened. See
> `docs/analysis/2026-08-09-blocked-work-register.md` for the corresponding
> blocked-work register row.

**Runner:**
`backend/apps/backend/src/chatbot/features/chat/test_calibration.py`
**Labelled sets:**
`backend/apps/backend/src/chatbot/features/chat/fixtures/calibration_sets/`
(`intent_classification.json`, `faq_match.json`, `sentiment.json`,
`summary_quality.json`)

---

## 1. The four labelled evaluation sets

Each set has at least 30 hand-labelled cases (`test_each_of_the_four_calibration_sets_has_at_least_thirty_labelled_cases`),
reflects this CRM's actual domains (service booking, roadside assistance,
parts, warranty, complaints, dealer location, charging, apps, sales), and
the Malaysian multilingual reality — Malay (including SMS-register:
dropped vowels, code-switching), English, Chinese, and Tamil cases appear
in every set.

### 1.1 Intent classification — `intent_classification.json` (36 cases)

Labelled against the **real, shipped taxonomy**: `case_taxonomy.py`'s
`CaseTaxonomy`, loaded via `build_case_taxonomy(settings)` from
`Settings.case_taxonomy_json` (`platform/config.py`), **unmodified** — the
client's own RFP 2026_028 Appendix A taxonomy (8 divisions: Sales, Product,
Network, Charging, Apps, After Sales, Others, Marketing). This is the same
taxonomy P7 task 5's Malay SMS corpus labels against, so the two suites are
comparable. Each case's `expected_intent` is
`{"category_slug": ..., "subcategory": ...}` — the exact slug +
bare-subcategory pair `classify_ticket_tool`'s LLM-facing contract expects.
No invented product names: `e.MAS 5`, `e.MAS 7`, `e.MAS 7 PHEV` are
`config.py`'s real default `vehicle_models_json` values.

### 1.2 FAQ match — `faq_match.json` (32 cases)

Labelled against the only FAQ/KB content that exists in this repo's test
fixtures — there is no seeded production FAQ corpus in this sandbox:

- the 8 topics from P7 task 5's `malay_sms_corpus.json` stub set (service
  duration, service booking, warranty coverage, roadside/towing, spare
  parts, dealer locations, public charging, test drive), and
- 4 topics carried over **verbatim** (question and answer text unchanged)
  from `test_faq_hybrid_rank.py`'s `LiveFaqEntry` fixtures: battery-light
  reset, tyre pressure, `e.MAS7` charging port cover replacement, and
  general maintenance schedule.

`e.MAS7` is a real product code (see `test_faq_hybrid_rank.py`), not
invented for this set. `expected_faq` is the topic id the runner's real,
unmodified `InMemoryLiveFaqStore`/`_rank` cosine-ranking code
(`adapters/live_faq.py`) should retrieve at rank 1.

### 1.3 Sentiment — `sentiment.json` (32 cases)

Labelled against the **real, current** `Sentiment` type —
`chatbot.features.chat.models.Sentiment = Literal["positive", "neutral",
"negative", "urgent"]`, widened to four levels by P7 task 1 (see
`.superpowers/sdd/2026-08-08-rfp-p7-ai-conversational-quality/task-1-report.md`).
7 positive, 7 neutral, 8 negative, and **10 urgent** cases. `urgent` is
safety-critical: per `detection.py`'s `should_open_ticket`, it joins the
ticket-creation gate alongside `negative`. The urgent cases are
deliberately genuine safety scenarios (brake failure, smoke/fire, accident
with injury, unexpected airbag deployment, steering lock-up, tyre blowout
at highway speed) rather than merely "very negative" complaints, so the
set can tell the two apart — a calibration set that only ever tests
"angry customer" would never catch a model that fails to escalate a real
emergency.

### 1.4 Summary quality — `summary_quality.json` (32 cases)

Unlike the other three capabilities, a summary has no single correct
answer, so this set cannot use an `expected_output` field. See §2 below for
the rubric this set is scored against.

---

## 2. The summary-quality rubric (and why it is reproducible)

Each case carries a `transcript` (a short `Customer:`/`Agent:` exchange)
and a `required_elements` checklist: a list of `{"name": ..., "keywords":
[...]}` entries, where `keywords` are synonym phrases that would count as
that element being present (e.g. for a roadside case, an element named
`"action"` might accept `["tow truck"]`).

**Scoring rule:** for a candidate summary, an element counts as present if
*any one* of its keyword phrases appears as a case-insensitive substring of
the summary text. The case score is `elements_matched / total_elements`;
the capability score is the mean case score across the set.

**Why this is reproducible month over month, by someone who is not the
author of this document:** the rubric is a mechanical keyword-presence
checklist, not a subjective judgement call or an LLM-as-judge prompt whose
wording could drift. Given the same `summary_quality.json` and the same
candidate summaries, two different people (or the same person a year
later) compute the identical score, because "does this substring appear in
this string" has exactly one answer. The only thing that can legitimately
change the score between runs is the *summary text itself* — which is
exactly what this rubric exists to measure. This is the same reason §8.1.15's
monthly review needs a fixed, low-judgement procedure rather than a
one-off human read: a rubric only the original implementer can apply is
not a deliverable.

The runner's stub summarizer (`_stub_naive_summarizer` in
`test_calibration.py`) is a naive extractor — first customer line + last
agent line, string concatenation, no model call — used only because this
sandbox cannot call Gemini. It is not tuned to maximise this fixture's
score, matching the same "approximate what an engineer with no ML would
write" convention P7 task 5 used for its keyword intent classifier.

---

## 3. Pre-change baseline (before any P7 tuning)

**UNMEASURED.** This package (P7 — AI Conversational Quality) is itself the
first time any of these four capabilities has had a labelled evaluation
set run against it, so there is no prior calibration run to report a
"before" number from, and no real Gemini/Vertex credentials in this
environment to run one now. This slot stays as `TBD — unmeasured` rather
than a placeholder percentage:

| Capability | Pre-change score | Mode | Model identity | Date |
|---|---|---|---|---|
| Intent classification | **TBD — unmeasured** | n/a | n/a | n/a |
| FAQ match | **TBD — unmeasured** | n/a | n/a | n/a |
| Sentiment | **TBD — unmeasured** | n/a | n/a | n/a |
| Summary quality | **TBD — unmeasured** | n/a | n/a | n/a |

To fill this table, run §5's procedure against a **pre-P7** checkout (a
commit before P7's changes landed) with real credentials, once, and record
the resulting `CalibrationReport` for each capability.

## 4. Post-P7 measurement

**UNMEASURED**, for the identical reason as §3: no real Gemini/Vertex
credentials exist in this sandbox. `test_calibration.py`'s own stub run
(against a deterministic, NOT-tuned keyword/embedding/extraction
stand-in — see `CalibrationReport.mode == "stub"`,
`.model_identity`, and `.disclaimer` on every report) is **not** this
number and must never be cited as one:

| Capability | Post-P7 score | Mode | Model identity | Date |
|---|---|---|---|---|
| Intent classification | **TBD — unmeasured** | n/a | n/a | n/a |
| FAQ match | **TBD — unmeasured** | n/a | n/a | n/a |
| Sentiment | **TBD — unmeasured** | n/a | n/a | n/a |
| Summary quality | **TBD — unmeasured** | n/a | n/a | n/a |

To fill this table, run §5's procedure against the current (post-P7)
checkout with real credentials, once, and record the resulting
`CalibrationReport` for each capability. Once both this table and §3's are
filled in from real runs, the *difference* between them is what "we
calibrated the AI" can finally point to.

---

## 5. Re-run procedure

Precise enough to run without prior familiarity with this repository.

### Preconditions

- A checkout of this repository at the commit you want to measure (§3:
  a pre-P7 commit; §4: the current `dev-yuda` HEAD or later).
- **Real Gemini credentials.** The environment variable is
  **`GOOGLE_API_KEY`** (a real Google AI Studio / Gemini API key — the same
  variable this repo's test suite otherwise sets to the placeholder
  `test-key`). If you use Vertex AI instead of the AI Studio API, set
  `GOOGLE_GENAI_USE_VERTEXAI=true` and `VERTEX_PROJECT_ID`/`VERTEX_LOCATION`
  instead (`backend/apps/backend/.env.example` documents both paths); the
  swap in step 3 below is the same either way.
- `uv` installed (the backend's Python package/venv manager).

### Steps

1. Install dependencies:

   ```bash
   cd backend/apps/backend
   uv sync
   ```

2. Confirm the stub run first, to prove the harness itself is intact
   before touching credentials:

   ```bash
   GOOGLE_API_KEY=test-key uv run pytest src/chatbot/features/chat/test_calibration.py -q -s
   ```

   Every printed report must show `mode: stub` and a disclaimer containing
   "UNMEASURED IN THIS ENVIRONMENT". If it does not, stop — something is
   wrong with the harness, not the credentials.

3. Swap each capability's stub stand-in for a real one, in
   `test_calibration.py` (same call shape, no test-signature changes
   needed — this mirrors how P7 task 5's corpus is designed to be
   upgraded, see its `task-5-report.md`):
   - **Intent** (`_run_intent_capability`): replace
     `_stub_intent_classifier` with a real one-forced-function-call
     Gemini decision (reuse `build_ai_agent` + a real `google.adk.Runner`,
     the same round trip `service.py`/`orchestrator.py` make in
     production — not the direct-tool-call pattern this file uses for
     the stub).
   - **FAQ** (`_run_faq_capability`): replace `_StubTopicEmbedder` with
     `adapters/live_faq.py`'s `VertexEmbedder`, and seed
     `InMemoryLiveFaqStore` with the tenant's real production FAQ/KB
     entries instead of the 12-topic stub set.
   - **Sentiment** (`_run_sentiment_capability`): replace
     `_stub_sentiment_classifier` with the real per-turn Gemini decision
     (same forced-function-call turn as intent — sentiment rides the same
     `classify_ticket_tool` call in production, see P7 task 1).
   - **Summary quality** (`_run_summary_capability`): replace
     `_stub_naive_summarizer` with the real summariser (the
     `/assist/summarize` logic in `features/assist/router.py`, or
     `agents.py`'s summarizer agent — whichever is the tenant's live
     summarisation path).
   - After each swap, change that capability's `mode` from `"stub"` to
     `"real"` and `model_identity` to name the actual model (e.g.
     `"gemini-2.5-flash (real, <date>)"`) — this field is what
     distinguishes a real measurement from a stub one, so treat any
     report still reading `"stub"` as not-yet-measured no matter what
     percentage it prints.

4. Re-run with real credentials:

   ```bash
   GOOGLE_API_KEY=<real key> uv run pytest src/chatbot/features/chat/test_calibration.py -q -s
   ```

5. Record each capability's `CalibrationReport.score`, `.model_identity`,
   and the date in §3 (if this is a pre-P7 checkout) or §4 (if this is the
   current checkout).

6. Revert the stub swaps from step 3 before committing anything back to
   `dev-yuda` — this repository's checked-in `test_calibration.py` must
   keep running in stub mode by default, since CI/local runs never have
   real credentials (`GOOGLE_API_KEY=test-key`). Only the *recorded
   numbers* in this document persist; the code stays stub-mode.

### Cadence — the §8.1.15 monthly review

Run steps 4–5 (no need to repeat 1–3, once the real-credential swap exists
as a saved patch/branch) **once a month**, against the then-current
production build, and record the score in a new dated row appended to §4's
table (keep prior months' rows — this becomes the accuracy trend line the
monthly review reports against). Compare each capability's score to its
proposed threshold in §6; a capability that regresses below its threshold
for two consecutive months should be raised at the review as a blocking
finding, not a footnote.

---

## 6. Proposed acceptance thresholds — **pending client sign-off**

**These are a proposal, not a standard the delivery has satisfied or the
client has agreed to.** No baseline has been measured yet (§3, §4), so nothing below has been
validated against a real number — the thresholds exist so the client has
something concrete to react to, adjust, or approve at the first AI-accuracy
review, not because engineering has decided them unilaterally.

| Capability | Proposed acceptance threshold | Rationale |
|---|---|---|
| Intent classification | ≥ 80% top-1 match against `case_taxonomy.json` | Matches the granularity the client's own case-categorisation reporting already commits to (P3); below this, human reclassification overhead likely exceeds the AI's time savings. |
| FAQ match | ≥ 75% top-1 retrieval against the labelled set | Slightly more lenient than intent, since FAQ phrasing has more legitimate near-duplicates. |
| Sentiment | ≥ 90% on `urgent` specifically (recall, not just accuracy); ≥ 80% overall | The **urgent** class is safety-critical and trips ticket creation (`detection.py`) — a missed urgent case is a materially worse failure than a missed neutral/positive one, so it gets its own, stricter bar. |
| Summary quality | ≥ 70% mean rubric coverage (§2) | Summaries are advisory (an agent reads and can correct them before it's client-facing), so full element coverage is not required for the feature to be useful. |

**Nothing in this table may be treated as met, agreed, or contractually
binding until:**

1. §3 and §4 both contain real, credentialed measurements (not stub
   numbers), and
2. the client has explicitly signed off on these specific numbers (or
   revised ones) at an AI-accuracy review meeting (§8.1.15).

Until both conditions hold, this table is a **starting point for
discussion**, and any dashboard, slide, or report citing it must say so.
