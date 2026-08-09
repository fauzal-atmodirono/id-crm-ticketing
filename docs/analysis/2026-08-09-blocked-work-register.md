# Blocked work register — what I cannot finish, and what would unblock it

**As of 2026-08-09**, branch `dev-yuda`. Companion to the test guide at
`docs/testing/2026-08-09-p1-p2-test-guide.md`.

This lists work that is **blocked on something other than engineering effort**:
a client answer, a credential, a running system, or a decision that is not
mine. It deliberately does *not* list "not built yet" — packages P5–P14 are
simply pending, and pending is not blocked.

Each item says who can unblock it and what changes when they do. If an item
here is quietly dropped rather than resolved, something ships that looks
finished and is not.

---

## 1. Blocked on a client answer

These produce a **truthful gap** today. None of them is a bug, and none should
be "fixed" by guessing — a plausible wrong number on a client slide is worse
than a visible zero, because nobody audits a number that looks reasonable.

| # | Blocked | Question | What ships until it is answered |
|---|---|---|---|
| Q5 | `escalated_to = 'hq'` | What counts as an HQ escalation? Nothing in the case model distinguishes one. | `escalated_to` offers **`dealer` and `none` only**. The validator rejects `hq` by name and says why; the provisioning script offers no `hq` option. **C1-07's HQ column will report zero** — it must be captioned "not yet classified", never read as "no HQ escalations happened". |
| Q6 | A dedicated bounce mailbox | Do you want DSNs delivered somewhere other than the tenant's own Email inbox? | **Downgraded from blocker to optimisation.** Bounce handling is built and live: Gmail returns the DSN to the envelope sender, which *is* the tenant's inbox, so no separate mailbox is required. A dedicated one would only stop DSNs touching the agent queue at all, which matters at volume. |
| Q8 | The C2 297-vs-264 discrepancy | Which of the two figures is authoritative? | P3 does **not** fix this. It avoids creating a second instance: `v_concern_pivot` buckets a null `case_detail` as `Unspecified` rather than filtering it out, so the pivot always reconciles with the headline count. |
| Q3 | Case-field ownership | Which fields are agent-entered vs system-derived? | All ten P3 fields are agent-entered. `REPORT_COVERAGE_DISCLOSURE` (default ON) captions any block grouped by one with its actual coverage. |
| Q10 | Licensing / §7 | Commercial. | R19 not attempted. |
| Q4 | Real DMS/TSP endpoints | | R11 not attempted; the integration shell exists. |
| Q7 | PII masking scope | | R16 not attempted. |

**The most urgent item in the whole programme is not engineering:** the
already-drafted vendor response marks **≥17 unbuilt requirements as "Fully
Out-of-the-Box"**. That needs reconciling before any clarification meeting.
It is row 6 of P14's risk register and it is not getting less true with time.

---

## 2. Blocked on something only the account owner can do

### 2.1 The bounce sender is outside this VM — **still open**

Delivery-failure notices for `proton.demo@demo.com` and `pic@emas.proton.com`
were still arriving on 2026-08-09 (five in 30 minutes; 60 total on the inbox).

**Ruled out**, across all three tenants: no user, no contact, no active
automation rule, no campaign, zero outgoing Chatwoot messages. No
`proton-backend` Cloud Run service. No local Docker. The originals' ActionMailer
Message-ID hosts (`c43dbee7b0fb`, `e417feed08dd`) match **no container on this
VM**, running or stopped — and `Config.Hostname` is fixed at create time, so
that is not a restart artifact.

The DSNs are addressed to `devotech29@gmail.com`, so the originals were **sent
from that Gmail account** by a Chatwoot instance elsewhere.

**Mitigated, not solved.** Deployed: a transport-level blocklist
(`EMAIL_BLOCKED_RECIPIENTS`) so this platform can never mail either address —
verified against the live backend — plus bounce handling so returning DSNs stop
becoming live cases. The automation rule targeting `pic@emas.proton.com` was
deleted outright rather than left disabled.

**Only the account owner can close it:** Google Account → Security → **App
passwords**, revoke anything that is not this VM. Gmail → Sent, search
`to:proton.demo@demo.com`, names the sender. Worth doing promptly — sustained
failures to a dead domain get a Gmail account rate-limited, and that would take
every real escalation down with it.

### 2.2 Live E2E test cases TC-01…TC-07, TC-10

Every one ends in "your test mailbox receives X". I can drive the entire server
side; I cannot read the mailbox. You chose *your own mailboxes only* for the
run. **The execution log in `docs/testing/2026-08-06-escalation-email-e2e-scenario.md`
is still empty** — TC-08 and TC-09 were proven live in an earlier session; the
rest have never been formally run.

### 2.3 `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED`

Set to `true` on proton and **never tested**. Needs a real WhatsApp voice note
or image sent to the tenant.

### 2.4 `MEDIA_DIAGNOSIS_PROMPT_ENABLED` (P7 task 8)

Code is done and unit-tested against the composed instruction string
(`build_agent_instruction` in `prompts.py`, `test_media_prompt.py`), but the
prompt has never been in front of the real model with a real photo — this
sandbox has no WhatsApp number and no real Gemini credentials, only
`GOOGLE_API_KEY=test-key` against a stubbed client. A prompt change that has
never run end to end is not a delivered feature.

**What must be sent:** a real photo of a visible vehicle fault (e.g. a dented
door, a dashboard warning light, a cracked bumper) through a real WhatsApp
inbox on a tenant with `MEDIA_DIAGNOSIS_PROMPT_ENABLED=true` **and**
`WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true` (§2.3 must be closed out on the
same message, since both flags gate the same photo). One follow-up message
of plain text should follow, to confirm nothing about the diagnostic
instruction leaks into a turn without media (per
`test_the_diagnosis_instruction_is_absent_when_no_media_is_attached`).

**What would count as passing:** the bot's reply names something specific it
observed in the photo (not a generic "please describe the issue"), states an
explicit confidence level, and asks at most one follow-up question — never
a checklist of several. Record the actual exchange (screenshots or the raw
WhatsApp/Chatwoot transcript) in `docs/testing/`, dated, the same way
`docs/testing/2026-08-06-escalation-email-e2e-scenario.md` records the email
E2E cases. Until that exists, `docs/testing/2026-08-09-media-diagnosis-prompt-live-check.md`
is a template awaiting that run, not a result.

### 2.5 The Malay SMS query normaliser (P7 task 6)

`nlu_normalise.py`'s `normalise()` — collapses repeated characters and
expands a fixed table of SMS-register Malay abbreviations (`brp`→`berapa`,
`nk`→`nak`, etc., sourced from P7 task 5's 56-case corpus) — is code-complete,
unit-tested against real production call payloads (`test_nlu_normalise.py`:
the text handed to the model and the text echoed back on the agent-facing
`/kb/suggest` surface are both asserted, against actual captured payloads, to
survive un-normalised), and wired into the single retrieval call site
(`MergedKnowledgeAdapter.search_kb`). It ships **disabled**
(`NORMALISE_RETRIEVAL_QUERY_ENABLED = False`, a plain module constant, not a
`Settings`/env flag — this task was explicitly scoped to add no config
surface) because its acceptance gate is literally "ship only if it
measurably improves task 5's corpus pass rate", and **that pass rate has
never been measured for real**: task 5's corpus runner only exists against a
stub keyword classifier and a stub topic embedder in this sandbox
(`CorpusReport.mode == "stub"`), because there is no real Gemini/Vertex
credential here. `test_nlu_normalise.py`'s eighth test runs the corpus
with and without the normaliser through that same stub harness and proves
the comparison mechanism works and reports both rates — it does not, and
structurally cannot, assert an improvement.

**What must happen before this flag may be flipped on any tenant:** run P7
task 5's corpus (`test_malay_sms_corpus.py`) against real Gemini/Vertex
credentials twice — once with the query fed to FAQ retrieval left raw, once
with `normalise()` applied — and confirm the FAQ-hit pass rate is measurably
better with it on. Only that comparison, run for real, is the evidence the
brief's gate asked for. Until it exists, `NORMALISE_RETRIEVAL_QUERY_ENABLED`
stays `False` — flipping it on the strength of the stub numbers this sandbox
can produce would be exactly the "plausible wrong number" this register
exists to prevent.

**And a coverage limit that survives the flag being flipped:** the normaliser is
wired into one retrieval call site (`MergedKnowledgeAdapter.search_kb`), but
`kb_suggest_router.py` has a **second, pre-existing live-FAQ retrieval path**
(`_live_faq_suggestions`) that embeds the agent's query directly and bypasses
the merged adapter entirely. So even switched on, the normaliser would not cover
every query the agent-assist panel makes. The task was scoped to the single call
site deliberately; whoever runs the comparison above should decide at the same
time whether the second path is in scope, because a measured improvement on one
path is not an improvement on the surface as a whole.

### 2.6 The AI calibration baseline (P7 task 10)

`docs/testing/2026-08-08-ai-calibration-baseline.md` and
`backend/apps/backend/src/chatbot/features/chat/test_calibration.py` (four
labelled sets, ≥30 cases each, covering intent classification, FAQ match,
sentiment, and summary quality) are code-complete and green
(`test_calibration.py -q -s`, 4/4 named tests), but — same root cause as
§2.5 — **neither the pre-change nor the post-P7 baseline number exists**,
because this sandbox has no real Gemini/Vertex credentials
(`GOOGLE_API_KEY=test-key`, every model client stubbed). Every
`CalibrationReport` this suite can produce here carries `mode == "stub"`,
a named stub `model_identity`, and a disclaimer stating in plain language
that the number is not the P7 calibration baseline — structurally, not as
a comment someone could miss (same idiom as P7 task 5's `CorpusReport`).

**What must happen before any acceptance threshold can be agreed with the
client:** run the procedure in §5 of the baseline document — real
`GOOGLE_API_KEY`/Vertex credentials, once against a pre-P7 checkout and
once against the current one — and record both resulting scores per
capability in that document's §3/§4 tables. The proposed thresholds in its
§6 are exactly that, a proposal: they must not be presented to the client,
in a slide or otherwise, as already met or already agreed. Until a real run
exists, `docs/testing/2026-08-08-ai-calibration-baseline.md`'s baseline
tables read `TBD — unmeasured`, deliberately, the same way
`docs/testing/2026-08-09-media-diagnosis-prompt-live-check.md` is a
template awaiting its own live run.

**One more reason the stub numbers must never be quoted**, stated because they
*look* like excellent results: the stub runs score **97–100% across all four
capabilities**, and both P7 task 5's corpus and task 10's calibration sets show
the same effect for the same reason — **the same author wrote both the
ground-truth labels and the naive keyword rules being scored against them.**
That is not a hard baseline, it is a harness agreeing with itself. The
`mode == "stub"` labelling keeps it honest in the data; this note keeps it honest
in prose, because a 98% in a status update is exactly the kind of figure that
gets repeated without its qualifier.

---

## 3. Blocked on infrastructure I cannot reach

| Item | Why | Unblocked by |
|---|---|---|
| **BigQuery `ensure_views()`** | All development ran against the mocked adapter. There are now **33 views** and the schema has grown by 13 columns. | One live run with GCP credentials. Note it **re-creates every view** — see the timezone warning below. |
| **Cloud Build for fork patch 0052** | The tier-2 manager field on the Escalation Routing page. Patch is written and verified with `git apply --check`; no image contains it. | `gcloud builds submit deploy/chatwoot-fork/ …` — off-VM, amd64. **Never build this on the prod VM, never from an arm64 Mac.** |
| **`provision_case_record_fields.py`** | P3's sidebar panel needs the custom-attribute definitions to exist. No fork patch needed. | One dry-run then live run per tenant. |
| **The Power BI `.pbix` (§4.55)** | A proprietary binary only Power BI Desktop can author. `proton-crm.pbids` (the connection) and the page-by-page spec ship instead — those are the reviewable, diffable parts. | One Power BI Desktop session. **Evidence required before reporting it done:** every page rendering against a real dataset, refresh succeeding under the service account, and a screenshot of each page in `power-bi-runbook.md`. The client raised this at the 2026-07-28 demo (feedback item 5), so an unevidenced claim will be checked. |
| **Cloud Build for fork patch 0053** | P6's Workforce dashboard page. **Hand-built and never verified against a real Chatwoot checkout** — context lines were transcribed from `0045`, the hunk arithmetic is script-verified, and it applies to a *synthetic* tree, which proves internal consistency only. If some unlisted patch also touches `protonAdmin.js` / `Sidebar` / `dashboard.routes.js`, or 0045's shipped content differs from what was transcribed, 0053 needs a line-number fix-up or a regenerated diff. | `gcloud builds submit deploy/chatwoot-fork/ …` — off-VM, amd64. **Do not schedule a demo of the Workforce page before this build has gone green.** |
| **Cloud Build for fork patch 0054** | P6's agent-facing **My status** page (the availability-status picker + the administrator's status-catalogue editor). Same constraint as 0053 and the same evidence: hand-built, hunk arithmetic script-generated, applies to a *synthetic* tree reconstructing its context, **never checked against a real Chatwoot checkout**. Its three anchored hunks apply ON TOP OF 0053 and transcribe 0053's own added lines as context, so if 0053 needs a line-number fix-up, 0054 needs the same one. The backend behind it (`features/routing/status_router.py`) is complete, needs no build, and **is now mounted** — `main.py` mounts `build_status_router` with the shared status/presence stores under `presence_custom_statuses_enabled`, and `test_p6_wiring.py` drives the endpoints through the real `bootstrap_application()` app to prove they answer rather than 404. So the API is live wherever that flag is on; only the page in front of it waits on this build. | `gcloud builds submit deploy/chatwoot-fork/ …` — off-VM, amd64. **Do not promise agents the "My status" page, or demo the catalogue editor, before this build has gone green.** Until then `PRESENCE_CUSTOM_STATUSES_ENABLED` on a tenant buys an API with no UI. |
| **Cloud Build for fork patch 0055** | P7 task 3's agent-facing **Translate** button in the reply composer's top panel (backs `translate_router.py`). **Hand-built and never verified against a real Chatwoot checkout** — unlike 0053/0054, its four hunks are anchored on content `0002-ai-assist-backend.patch` itself adds (already merged in this repo), not on unverified upstream lines, so every context line was transcribed verbatim from an already-shipped patch rather than reconstructed from memory. Hunk arithmetic was verified by applying the four hunks with `git apply --check` (and a full `git apply`, inspected) against a synthetic file reconstructing exactly that known content, which proves internal consistency only — it does not depend on 0053/0054 and has no unbuilt dependency of its own, only on 0002 (already merged). If 0002's shipped content ever differs from what is transcribed here (e.g. a later patch also touches `ReplyTopPanel.vue`), 0055 needs a line-number fix-up. | `gcloud builds submit deploy/chatwoot-fork/ …` — off-VM, amd64. **Do not demo the Translate button before this build has gone green.** The backend (`translate_router.py`) is independently testable and covered by its own suite regardless of this build; only the UI trigger waits on it. |
| **Cloud Build for fork patch 0057** | P9 tasks 2/3/6's **new-inbound alerting in the main Chatwoot UI** (toast/sound/desktop moved out of the `my-tasks` iframe) plus the **Alert preferences** page (backs `features/alerts/rules_router.py`). **Hand-authored and never verified against a real Chatwoot checkout.** Better evidence than 0053/0054 and comparable to 0055/0056: four of the six files are NEW (no upstream context at all), and the two modified files' hunks were **generated by `git diff`** — not written by hand — against a synthetic pre-image reconstructed only from lines transcribed verbatim from 0003's, 0025's, 0035's, 0041's, 0043's, 0053's and 0054's own merged diffs, then re-applied to a fresh copy of it with a real `git apply`. It **stacks on 0025** (Sidebar's `useProtonPermissions` import and `protonHasPermission` setup line), **0043** (the Cases nav block, its nav hunk's leading context) and **0003** (`...inboxRoutes,`). It deliberately anchors *below* 0053/0054's insertions rather than on their content, so a **content** change to those two cannot break it — only their line counts shift, which `git apply` absorbs as an offset. Two upstream APIs it could not read are **guesses**: the Vuex conversation-list getter names and `GET /api/v1/accounts/:id/conversations`'s response shape. Both fail toward a **visible degraded indicator**, never toward silence. | `gcloud builds submit deploy/chatwoot-fork/ …` — off-VM, amd64, **never on the prod VM, never from an arm64 Mac.** **Do not claim §3.1.1/§4.2 (pop-up on new inbound contact) before this build has gone green AND the manual screenshot check in 3h below has been done.** The preferences page's backend (`rules_router.py`) is independently tested and does not wait on this build — but it is **not mounted in `main.py` yet**, so today the page would 404 against a live backend. |
| **Upstream Chatwoot source** | This sandbox cannot reach github. | Only matters for files **upstream owns**. Files our own patches *created* can be reconstructed by replaying the patch history — that is how 0052 was authored. `CustomAttributes.vue` is upstream-owned and therefore genuinely out of reach. |

---

## 3b. P5 control items — five rows that cannot be measured

The control-item slide (C1 p48) renders **nine of fourteen** rows from real
data. The other five report `no_data` with a client-facing reason. They are
**blank on purpose**, and the distinction is the point of the package:

> **A zero would be a claim about performance; a blank is a statement about
> instrumentation.**

| Row | Why it cannot be measured | Unblocked by |
|---|---|---|
| 10 Call abandon rate | No call queue is instrumented — nothing to abandon, nothing to count | R9 (4–6 weeks) |
| 11 Average speed of answer | Queue measurement, same gap | R9 |
| 12 Service level | Queue measurement, same gap | R9 |
| 13 Calls offered | Comes from telephony queue statistics, not integrated | R9 |
| 14 Escalations to HQ | "HQ escalation" is undefined in the case model | **Q5** |

**If anyone "tidies" these to 0 before a client meeting**, the slide begins
asserting a 0% abandon rate on a platform with no call queue. That is the
single most likely way this work gets undone, and it will look like an
improvement to whoever does it.

---

## 3c. P6 workforce dashboard — one column that cannot be measured

Same rule as §3b, one column instead of five rows:

| Column | Why it cannot be measured | Unblocked by |
|---|---|---|
| Cases closed today | No helper date-filters "resolved today". The only conversation pager in the codebase is a synchronous, unfiltered, full-history one built for a nightly batch job; calling it on every ~30 s dashboard poll would get slower as history grows. Reported as `null` with a `cases_closed_today_caveat`, never `0`. | A "resolved since" incremental query (new plumbing, not requested by any package) |

Two scope statements that belong here rather than only in prose, because both
read as more than they are:

- **The 1-hour unavailability alert's WIP list is scoped to `SLA_INBOX_IDS`** —
  the same inboxes the SLA engine watches, **not** account-wide. A tenant
  routing agent chats through an out-of-scope inbox has those open cases
  invisible to the alert. It is a prompt to review, not an audit.
- **Requirement 4.69's average-handling-time half is not delivered.** P6 ships
  the After-Call-Work *state*; AHT needs the same call-queue instrumentation
  (R9) that blanks four control items above. Do not let the ACW state be
  reported as closing 4.69.

## 3c-1. P8 task 1 — `ai_actions` needs a manual `ALTER TABLE` on already-deployed tenants

Same root cause as the SLA-policy migration on 2026-08-08 (`ALTER TABLE
sla_policies ADD COLUMN IF NOT EXISTS tier2_hours DOUBLE PRECISION` etc.,
against `RBAC_DATABASE_URL`, recorded in that day's deploy notes): this repo
has no Alembic, so `agent/app/db/models.py`'s new `output_tokens`/
`cached_tokens` columns on `AiAction` are created automatically by
`Base.metadata.create_all` for a **fresh** database only. An already-deployed
tenant's `ai_actions` table (every live tenant today) does not gain these
columns until someone runs, against that tenant's `AGENT_DATABASE_URL`:

```sql
ALTER TABLE ai_actions ADD COLUMN IF NOT EXISTS output_tokens INTEGER;
ALTER TABLE ai_actions ADD COLUMN IF NOT EXISTS cached_tokens INTEGER;
```

Both columns are nullable, so the migration is additive and safe to run
against a populated table — existing rows simply read `NULL` (correctly:
"not captured", not "zero"). Until it runs on a given tenant, any future
consumer of these columns (P8 task 4's cost pricing) reading directly against
that tenant's Postgres will fail on the missing columns rather than silently
return wrong numbers, which is the correct failure mode for a schema gap.

## 3c-2. P8 tasks 4/6/8/9 — eleven new BigQuery views, none of them created yet

No BigQuery project or credentials exist in this sandbox (controller decision
D2), so every view added by P8 tasks 4, 6, 8 and 9 is authored and asserted
structurally, exactly as P4 and P5 did, and **not one of them has been run
against a real dataset.** What is owed, per tenant:

1. **Run `ensure_views(settings)`.** This creates the six new
   `conversations`-based views: `v_ai_resolution`, `v_ai_vs_human`,
   `v_ai_escalation_reasons`, `v_ai_deflection`, `v_csat_by_resolution`
   (task 8) and `v_kb_coverage` (task 9) — plus `v_csat_by_agent` (task 6)
   **only if `CSAT_BY_AGENT_ENABLED=true` for that tenant**, since a
   flags-off tenant deliberately gets the pre-P8 view set unchanged. Watch for
   two things a structural test cannot check: `RANK() OVER (PARTITION BY day,
   channel, respondents >= N)` inside a `CASE` in `v_csat_by_agent`, and the
   `SUM(COUNT(*)) OVER (PARTITION BY ...)` window-over-aggregate in
   `v_ai_vs_human` / `v_ai_escalation_reasons`.

2. **Create the three `token_usage` views.** `bigquery_schema.ai_cost_view_ddls`
   (`v_ai_token_usage`, `v_ai_cost_surface_coverage`, `v_ai_cost`) has **no
   runtime caller** — `ensure_views` only runs `view_ddls`, whose base table is
   `conversations`. They must be created by hand or by a follow-up wiring
   change, and `v_ai_token_usage` requires the `token_usage` table to exist
   first (`BigQueryTokenUsageSink` creates it on init, so it appears the first
   time a tenant runs with `TOKEN_METERING_ENABLED=true` and
   `METRICS_PROVIDER=bigquery`). Creating `v_ai_cost` before then fails:
   BigQuery resolves a view's base tables at creation time.

3. **`GET /metrics/ai-cost` returns `read_status: "unavailable"` until both of
   the above are done**, which is deliberate — with no warehouse there is no
   evidence of zero spend, so the endpoint must not render a confident 0.00.

4. **`v_kb_staleness` (task 9) cannot be created at all yet**, and this is the
   larger gap. It reads a `faq_entries` table that does not exist and that
   nothing populates. Owed: create the table from
   `faq_schema.FAQ_ENTRIES_SCHEMA`, then build a snapshot job that writes one
   row per FAQ entry with `updated_at` (the operator's last edit) and
   `serve_count` (how often live-FAQ search served it) — neither of which is
   recorded anywhere today; `faq_feedback` records user feedback, not serves,
   and has no edit timestamp. Note `faq_schema.faq_view_ddls` has **no runtime
   caller either**, so `v_faq_quality` is also not created by any deploy path
   today — a pre-existing gap this task found rather than introduced.
   `v_kb_staleness` is deliberately left out of `ensure_views` because a
   `CREATE VIEW` over the missing `faq_entries` table would raise partway
   through the loop and abort creation of every view after it.

5. **The `agent` service's token counts never reach the warehouse.** They are
   written to `ai_actions` in Postgres (P8 task 1 + the D4 fix). `v_ai_cost`
   therefore reports the `agent` service as `cost_status='unmetered'` with a
   reason, not as zero spend. Owed: an export from `ai_actions` into
   `token_usage`, or an equivalent read path.

6. **No sentiment cut exists on the AI performance reports.** P7's sentiment is
   a Chatwoot custom attribute that `features/metrics/mapping.py` does not read
   into `ConversationRow`, so there is no `sentiment` column in
   `CONVERSATIONS_SCHEMA` — and this repo's own
   `test_every_schema_column_is_either_a_row_field_or_explicitly_sync_only`
   correctly forbids adding a column nothing populates. Owed, in order:
   `mapping.py` reads the attribute, `ConversationRow` gains the field, the BQ
   schema gains the column, then the reports can cut by it. When they do, an
   unclassified case must bucket as its own level and **never** as `neutral` —
   P7's sentiment is `None` whenever `SENTIMENT_CLASSIFIER_ENABLED` is off, and
   folding that into `neutral` would report the whole pre-flag history as
   neutral sentiment.

7. **`resolved_by` is not an AI-vs-human column and `v_resolution_split`'s
   column names still imply it is.** `mapping.py` derives it from Chatwoot
   `status` alone, so `resolved_by='bot'` means "resolved". Task 8's views
   deliberately do not read it; `v_resolution_split`'s live `closed_by_bot` /
   `transfer_to_agent` labels are left alone because renaming them would break
   existing dashboards. Owed: a decision on whether to rename them, and a note
   to whoever reads them next that they mean resolved / not-yet-resolved.
   **Task 10 supplied that note in the client-facing docs**, where the
   overclaim was live: the feature guide's Reports chapter described the Bot
   report's "bot-resolved %" and "bot-vs-agent resolution split" as an
   AI-vs-human measure, and now states that the two numbers are resolved and
   not-yet-resolved. The rename decision is still owed.

## 3c-3. P8 tasks 2/3/7 — the surfaces that cannot be metered, and the QA table migration

Sections 3c-1 and 3c-2 cover the `ai_actions` columns and the eleven views.
This one covers the rest of what P8 cannot measure, so a cost or performance
figure taken from this platform is never quoted without it. Added by task 10
alongside the flags-ON gate entries.

**1. A second `ALTER TABLE` is owed, this one in BigQuery** — same class of gap
as 3c-1, different table. `create_table(..., exists_ok=True)` does not
retroactively add columns, so a tenant with a pre-existing `qa_labels` table
needs this once they turn `CALL_QA_ENABLED=true`:

```sql
ALTER TABLE `<project>.<dataset>.qa_labels`
  ADD COLUMN IF NOT EXISTS channel STRING,
  ADD COLUMN IF NOT EXISTS rubric_greeting BOOL,
  ADD COLUMN IF NOT EXISTS rubric_identification BOOL,
  ADD COLUMN IF NOT EXISTS rubric_resolution BOOL,
  ADD COLUMN IF NOT EXISTS rubric_closing BOOL,
  ADD COLUMN IF NOT EXISTS rubric_compliance BOOL,
  ADD COLUMN IF NOT EXISTS call_qa_percentage FLOAT64;
```

`BigQueryQaLabels.record_label` only ever *adds* these keys when a label
actually carries them (it never sends an explicit `null`), so a pre-migration
table keeps working until a reviewer both has the flag on and submits a rubric.
The statement is also in `features/metrics/qa_schema.py`'s module docstring.

**2. `chat.turn` is unmeterable at our client boundary, and it is the largest
line item.** google-adk takes a model *string* (`Agent(model=...)`) and
constructs its own `google.genai.Client` **inside the installed package**, so no
wrapper at our boundary can observe the call. `features/chat/service.py`'s
wrapped client only *transcribes*, and its rows are therefore labelled
`chat.transcribe`, never `chat.turn` — a `chat.turn` row carrying only a
transcription's tokens would tell the cost report that the busiest surface in
the product is nearly free. `SURFACE_CHAT_TURN` is kept defined-but-unused with
the reason in a code comment. Unblocking this needs either an ADK hook that
exposes usage, or our own client passed into `Agent`, i.e. an upstream change or
a fork — not a wiring fix.

**3. `phone.live` usage is uncapturable the same way.** The Live API reports
usage in server messages rather than on a response object. `connect_live` is
routed through the metering wrapper for the structural guarantee only; no token
row is ever written for it. Unblocking: read usage off the server-message stream
in the phone bridge, which is a feature, not a migration.

**4. `embed` is visible but not priceable end to end.** Embeddings bill per
*character* (`EmbedContentResponse` carries no `usage_metadata` at all, so all
three token counts are `None` by construction). `price_table.py` has a
per-character class (`TOKEN_CLASS_EMBEDDING_CHARS`) so a rate *can* be recorded,
but `token_usage` has no character-count column, so no cost can be computed from
it. Owed: a `billable_character_count` column on `token_usage` plus capture of
`EmbedContentResponse.metadata.billable_character_count` in the wrapper.

**5. Thinking-model token classes are billed and not captured.**
`thoughts_token_count` and `tool_use_prompt_token_count` fall outside the three
classes `TokenUsage` records, so the three sum to **less than**
`total_token_count` and even the five priced surfaces are understated for a
thinking-enabled model. `total_token_count` was never persisted upstream either,
so there is nothing at this layer to reconcile against.
`completeness.excluded_token_classes` names them on the payload. Owed by whoever
next changes `TokenUsage`'s schema.

**6. There is deliberately no total, and that must stay true.** The only money
figure is `priced_subtotal_usd`; `test_the_report_emits_no_unqualified_total`
fails the build on `total`, any `total_*` and any `*_total`. Unmetered surfaces
are rows with `cost_usd: null` **and** `calls: null`, because `0` claims a
surface is free and an absent row claims the inventory is complete. This is not
an owed item — it is a constraint on future work, recorded here so a later
"tidy-up into a headline figure" is recognised as a regression rather than an
improvement.

**7. Call QA is manual by design.** The five criteria are only ever set from
explicit `POST /qa/label` request fields; there is no transcript reader, no
Gemini call and no heuristic anywhere in `qa.py`/`qa_adapter.py`/`qa_router.py`.
The reason is 2.x-shaped rather than 3.x-shaped: the phone transcript path has
never run against a real Twilio call (see
`docs/testing/phone-channel-package-c-verification.md`), so an automated scorer
built on it would produce confident noise. Unblocking automated scoring requires
that live-call verification first, not more code.

**8. The web live-chat widget's survey is unsampled.** `should_survey_nps` is
exported and ready, but the widget calls `/chat/csat` / `/chat/nps` from its own
UI, so NPS sampling reaches WhatsApp, email and phone only. Owed: a frontend
wiring task. Declared, not implied — a tenant reading "NPS sampling is on" should
not expect widget conversations in the denominator.

**9. No live validation of anything in P8.** No BigQuery, no real Gemini
`usage_metadata`, no Twilio call, no live Postgres or Firestore in this
environment (controller decisions D1/D2). Every figure P8 produces is generated
by code unit-tested against recorded and synthetic usage-metadata shapes and
in-memory fakes. The first live run is where a real
`GenerateContentResponseUsageMetadata`, a real `RANK() ... OVER` inside a `CASE`,
and a real price-table read get exercised for the first time.

## 3d. P6 follow-up date — built, and deliberately invisible

Not blocked on anyone outside this repo, recorded here so it is not mistaken
for shipped: the per-ticket follow-up date exists end to end in the backend and
the `agent` service (and is asserted never to appear as an SLA breach), but
there is **no Chatwoot UI for it**. The field needs P3's conversation-panel
patch, which is not part of P6. `FOLLOW_UP_DATE_ENABLED` keeps it invisible
until that patch exists — so the flag being off is the honest state, not an
oversight.

## 3e. P6 absence alerts — the two statuses that deliberately never alert

Recorded because both look like bugs and neither is, and "fixing" either would
produce an alert storm that gets the whole feature switched off:

- **Chatwoot's native `busy` never counts as an absence.** Busy means an agent
  is working — mid-conversation, or on a long call. A 10-minute alert on Busy
  fires at every agent handling anything substantial.
- **Chatwoot's native `offline` never counts as an absence either.** Offline is
  an agent stating they are off shift; the design derives the availability
  history from exactly these transitions. Alerting on it pages an
  administrator ten minutes after every agent logs off in the evening, and
  again an hour later, every night, per agent.

The alerts fire for the away-from-desk statuses an agent picks — Lunch, Break,
Toilet, Prayer — which is what §4.13/4.14 ask for. Both native values *do* now
resolve to catalogue entries (before the C1 fix they resolved to "no
information", which is what made the alerts unreachable in production); they
resolve to entries with `counts_as_unavailable=False`. Named tests pin all of
this: `test_a_native_status_from_the_poller_resolves_and_alerts_about_nothing`
and `test_offline_is_catalogued_and_does_not_count_as_unavailable`.

**A consequence worth stating for operators:** a tenant that enables
`PRESENCE_THRESHOLD_ALERTS_ENABLED` but never enables
`PRESENCE_CUSTOM_STATUSES_ENABLED` will correctly see **no alerts at all** —
it has no way for an agent to record an absence in the first place.

## 3f. P7 resolved-case index — written, and nothing reads it yet

Same category as §3d: not blocked on anyone outside this repo, recorded so it is
not mistaken for shipped. With `RESOLVED_CASE_INDEX_ENABLED` on (and the
pgvector KB configured), a resolved conversation's **summary** is embedded and
stored in its own `resolved_case_summaries` table, each hit labelled
`resolved_case` and purgeable without touching a single authored FAQ. **No
surface queries it.** `kb_suggest_router.py`, the copilot and `/assist/suggest`
all ground on the curated KB only, so an agent sees no resolved-case
suggestions today — enabling the flag builds the corpus and nothing more.

Whoever adds that surface inherits two constraints already built for them, and
both are the point of the containment work: every hit carries
`RESOLVED_CASE_SOURCE_LABEL` and a `RESOLVED_CASE_DISCLAIMER` string, and they
must be rendered — machine-generated content shown unlabelled beside curated
FAQs silently acquires the curated corpus's authority. **Until the surface
exists, no client-facing material may describe agents as receiving
resolved-case suggestions.** The operator handbook's AI Conversational Quality
section says so explicitly.

The other half of the same wiring **is** live: `AUTO_SUMMARY_ON_RESOLVE_ENABLED`
posts the summary as a private note through the mounted `/assist/summarize`
logic, proven end to end through the real app in
`backend/apps/backend/src/chatbot/test_p7_wiring.py`.

## 3g. P7 `FAQ_SUGGESTION_POPUP_ENABLED` — now has a consumer, still not fully wired

Originally recorded during the P7 wiring wave as a setting with no consumer at
all; task 7 (this row's update) closed that specific gap but left two others,
so this row is updated rather than removed.

`deploy/chatwoot-fork/patches/0056-faq-composer-apply.patch` adds a
dismissible FAQ-suggestion strip to the reply composer's top panel
(`ReplyTopPanel.vue`, already extended by `0002` and `0055`). It fetches
`GET /kb/suggest` (unchanged) for the conversation's latest customer message,
shows only the single top hit when its `score` clears
`FAQ_SUGGESTION_CONFIDENCE_THRESHOLD` (0.75 — Vertex hits never carry a
`score` at all, so they never qualify by construction), and its Apply button
re-uses `0002`'s existing `protonAssistResult` bridge (the same one
`ReplyBox.vue`'s three Copilot actions already write the composer through) —
so it does not fight the iframe sandbox the agent-app README describes; it
supersedes that path for this one feature only. Dismissal is keyed by
customer-message id, so a suggestion dismissed for one message cannot
reappear for that same message.

Two things remain genuinely unverified or incomplete, stated so the flag is
not read as a finished feature:

1. **The patch has never been through a Cloud Build or applied to a real
   Chatwoot checkout** — this sandbox has no network access to clone
   upstream, the same constraint recorded against `0053`/`0054`/`0055`. Its
   own tests (`backend/apps/backend/src/chatbot/test_p7_task7_faq_composer_
   patch.py`) prove the hunks apply cleanly to a synthetic reconstruction of
   `0002`'s and `0055`'s own already-merged content and that the resulting
   logic behaves as described — not that it applies to the real fork.
2. **`FAQ_SUGGESTION_POPUP_ENABLED` and the frontend's actual gate are two
   independent switches.** The strip's real client-side gate is
   `hasFeature('faq_suggestion_popup')`, read from a tenant's `PROTON_FEATURES`
   list — the same mechanism `ai_assist` already uses. Turning on the backend
   setting does not populate that list; `deploy/tenants/*.env`,
   `docker-compose.tenant.yml` and `main.py` were all off-limits for task 7, so
   wiring the two together is a deploy-config task still owed, not something
   task 7 could close by itself.

The side-panel FAQ suggestions are a separate, older feature and are
unaffected either way. The README's P7 section and the operator handbook have
been updated to describe this state rather than "no consumer."

## 3h. P9 `INBOUND_ALERTS_ENABLED` — the same two-switch gap as 3g, plus an owed screenshot

`deploy/chatwoot-fork/patches/0057-inbound-alerts.patch` (P9 tasks 2/3/6) puts
new-inbound alerting into the main Chatwoot UI: `helper/protonAlerts.js` (the
`my-tasks` app's own `beep()`/`Notification`/`toast()` code, transcribed — that
app is **not** modified and keeps its SLA alerts),
`composables/useProtonInboundAlerts.js` (installed from `Sidebar.vue`, which is
mounted on every dashboard page), `api/protonAlerts.js`, and
`views/ProtonAlertPreferencesPage.vue` with a route and a nav entry.

Four things are unverified or incomplete. None of them is a bug, and none
should be read as "the feature is done":

1. **`INBOUND_ALERTS_ENABLED` and the fork's real gate are two independent
   switches** — the identical gap 3g records for
   `FAQ_SUGGESTION_POPUP_ENABLED`. The module's client-side gate is
   `hasFeature('inbound_alerts')`, read from a tenant's `PROTON_FEATURES` list
   via `window.__PROTON_CONFIG__`. There is **no backend endpoint that reports
   `inbound_alerts_enabled` to the frontend**, and `deploy/tenants/*.env`,
   `docker-compose.tenant.yml` and `main.py` were off-limits for these tasks,
   so an operator must add `inbound_alerts` to `PROTON_FEATURES` **in addition
   to** turning the backend setting on. Closing this is a deploy-config task.
   *The preferences page is different and has a single real switch:* it is
   gated by RBAC (`alerts.set_own_preferences` / `alerts.manage`) and reports
   `ALERT_RULES_ENABLED` honestly, because `rules_router.py` answers
   `{"disabled": true, "reason": …}` when it is off and the page renders that
   reason instead of an empty table.
2. **`rules_router.py` is not mounted in `main.py`.** `build_rules_router` is a
   factory waiting for a wiring step. Until that lands, the preferences page's
   five endpoints 404 against a live backend and every agent gets the built-in
   defaults — which is the designed fallback, not a failure, but it means the
   page cannot yet save anything.
3. **The manual verification is owed and cannot be done here.** The definition
   of done requires: an inbound WhatsApp message raising a **toast within 2
   seconds in the main Chatwoot UI, with a screenshot**; sound firing when an
   agent enables it; a desktop notification appearing once permission is
   granted; **nothing firing on the agent's own reply**; and the degraded
   indicator appearing with the event stream blocked at the network level while
   no alert fires twice on reconnect. None of that has happened. The patch's
   own tests (`backend/apps/backend/src/chatbot/test_p9_task236_inbound_alerts_
   patch.py`) execute the shipped decision functions in node and apply the
   patch to a synthetic tree; they prove nothing about pixels, browsers or
   sockets. Record the screenshots in `docs/testing/` when the build is green.
4. **Two upstream APIs are guesses**, because this sandbox cannot read
   upstream: the Vuex conversation-list getter names
   (`CONVERSATION_GETTER_CANDIDATES`) and the shape of
   `GET /api/v1/accounts/:id/conversations`. Both are guarded — a wrong getter
   name yields coverage `'none'`, which forces poll mode **and shows the
   degraded indicator**, and a failed poll leaves the mode unchanged rather
   than claiming health. So a wrong guess is visible and fixable by editing one
   array; it is not silent. Confirm both on the first real build.

## 4. Deliberately not attempted

Recorded so they are not mistaken for oversights.

- **R9 call queue** — 4–6 weeks. Blocks 6 of the 14 monthly control items.
- **R17 multi-zone HA** — **99.9% uptime and P1<2h are not supportable on one
  GCE VM.** That is a commercial conversation, not an engineering task.
- **§4.63 telephony half** — depends on R9.
- **§2.1.1 procurement, §2.1.2** — not achievable as written.
- **B-EM-01 mailbox provisioning, B-SM-05 Meta verification** — third-party.
- **Appendix B Malay wording** — Appendix B is **English-only**. The plan
  assumed bilingual; no Malay was invented, because inventing customer-facing
  copy in a language the client did not approve is not a gap I get to close.
  PROTON must supply it.

---

## 5. Carries a warning rather than a block

Not blocked — but they will surprise someone if they land unannounced.

**`REPORTING_TIMEZONE`** — switching it **re-buckets every historical figure on
every dashboard** the next time `ensure_views()` runs. Totals do not change;
cases slide between adjacent days, weeks and months. That is why it reads as
"close but not quite" rather than obviously broken. The default (UTC) is the
*identity transform* — byte-identical DDL — so nothing moves until someone
decides. **Run `scripts/compare-reporting-timezone.py` first and keep the
output**: it is the evidence that Monday's movement was expected.

**`v_dealer_escalation` keys on `dealer_escalated_at`, not `created_at`.** A
case created in May and escalated in June is a **June** row, so this view's
monthly total deliberately does not sum to that month's case count. Someone
will file that as a bug. It is asserted as a named test so the answer is
findable.

**The summariser's PII-omission instruction is a request, and an operator can
argue with it.** `/assist/summarize`'s prompt now asks the model to leave out the
customer's name, phone number, email, home address and plate number — the
mitigation the P7 design claims, and it did not exist as a prompt line until
commit `f4d6258`. What it is **not**: nothing in `assist/router.py`, and nothing
in `resolved_case_index.py` (which stores the output into pgvector) inspects,
strips or validates the returned text, so a summary can still carry an identifier
if the model includes one. And because `_apply_persona` **prepends** an operator
persona prefix (product name, guardrails, preferred language) ahead of the task
prompt, a tenant whose guardrails say the opposite — "always include the
customer's full name" — puts that instruction *earlier in the same request*, and
the model may prefer it. **Anyone with persona-edit access can therefore weaken
the mitigation without touching code.** The real fix is R16 (full PII masking),
blocked on **Q7**; this is a prompt, not a control, and should not be presented as
one in any privacy discussion.

> **Correction, recorded so it is not re-propagated:** this run's SDD ledger
> (`.superpowers/sdd/2026-08-08-rfp-p7-ai-conversational-quality/progress.md`)
> states as a "critical follow-up" that `_apply_persona` **replaces**
> `_SUMMARIZE_SYSTEM` wholesale when a tenant has persona `instructions` set, so
> that the omission sentence "disappears entirely" for those tenants. **That is
> not what the code does.** `_apply_persona`
> (`features/assist/router.py:245`) has one branch — `return prefix + "\n\n" +
> task_system` — and `_resolve_persona_prefix` reads `product_name`,
> `guardrails` and `language` only; it never reads the persona's `instructions`
> field at all. The same is true of `compose_chat_agent_instruction`
> (`features/chat/chat_persona.py`), which appends an `## Operator persona`
> section rather than replacing its base. P7 task 3's own report reached this
> conclusion first and correctly; the ledger entry overstated it. The residual
> risk is the model-instruction-following one described above, which is real but
> weaker, and no code change is owed for the stronger claim because the stronger
> claim is false. Verified 2026-08-09 during the P7 wiring wave.

**The flags-on test run.** `deploy/scripts/check-suites-both-flag-states.sh`
runs both suites with every feature flag forced ON. That run has already caught
two defects the flags-off run could not — the on-path is code nobody exercises
until a tenant opts in. **Every new default-off flag must be added to
`FLAGS_ON`**, or its on-path is untested.

P7 added eight of its nine settings, with `TRANSLATION_OUTBOUND_TAMIL_ENABLED`
deliberately excluded (see §2.4's neighbours and the Tamil note in the README) —
and `backend/.../test_p7_flags.py` now *asserts* both halves of that: every P7
setting present in `FLAGS_ON`, and outbound Tamil absent from it. The list is no
longer maintained by memory.

P6 added its seven flags **and P5's two, which had been omitted**. Adding them
immediately caught a third class of defect the flags-off run cannot see: three
tests asserting "this flag defaults to false" by constructing `Settings()`
without clearing the environment first. `_env_file=None` does not stop
pydantic-settings reading `os.environ`, so on the flags-ON run those tests were
asserting the exact opposite of their own names — passing while proving nothing.
**A new default-asserting test must clear its variable from the environment**,
or adding its flag to `FLAGS_ON` turns the test into a lie rather than a check.
`ROUTING_ENABLED` is deliberately *not* in `FLAGS_ON`: it is a Phase-5 switch,
and the one P6 component gated on it (the assignment sweeper) has its on-path
covered by `features/routing/test_p6_wiring.py` instead.
