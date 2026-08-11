# System integration test script

**Programme:** PROTON e.MAS CRM enhancement (RFP 2026_028)
**Closes:** §2.2.6 (SIT/QA report against a pre-agreed script) — the script half
**Version:** 1.0, drafted 2026-08-08
**Companion:** `2026-08-08-sit-report.md` (the execution) · `../governance/qa-plan.md`

---

## Agreement status

> **STATUS: DRAFT — NOT YET SENT TO PRO-NET. NOT AGREED.**
>
> | Step | State | Date |
> |---|---|---|
> | Script drafted, all nine integration areas covered | Done | 2026-08-08 |
> | The two untestable integration points listed with reasons | Done | 2026-08-08 |
> | Sent to PRO-NET for review | **Not done** | — |
> | Agreement received and recorded | **Not done** | — |
> | Executed | **Not done** — see `2026-08-08-sit-report.md` | — |

**This block is the requirement, not the table of test cases.** §2.2.6 asks for a
report against a **pre-agreed** script. A script agreed after execution is not a
test script, it is a description of what we chose to check — and a client
reviewer will read it that way, correctly.

So the sequencing is non-negotiable: **this document is sent, reviewed and agreed
by PROTON before a single case is executed**, and the agreement date is recorded
above and quoted in the report. If execution happens first, the deliverable is
not met no matter how thorough the testing was.

**Do not fill in the agreement date without an actual agreement.** That is the one
way this document can become worse than nothing.

---

## Scope and prerequisites

### Environment

Executed against a **non-production environment** with real credentials for every
third party. This is worth stating because it is the reason the SIT exists: the
automated suites run with no live credentials at all — no real Gemini, BigQuery,
Twilio, Postgres, Firestore or Chatwoot — so **every case below tests something
no existing test can reach.** See `../governance/qa-plan.md` §5.

### Prerequisites, all required before execution starts

| # | Prerequisite | Why |
|---|---|---|
| P-1 | A scratch tenant provisioned via `deploy/scripts/add-tenant.sh`, isolated from `proton` and `default` | Several cases send real customer-facing messages |
| P-2 | Chatwoot image built from the current fork, **including patches 0052–0060** | Nine patches have never been in an image; every UI case depends on this |
| P-3 | Real Gemini/Vertex credentials | Every AI case |
| P-4 | A BigQuery project and dataset, with `ensure_views()` run once | The 33 views have never been created |
| P-5 | A Twilio account, a voice number and a WhatsApp sender | Every phone and WhatsApp case |
| P-6 | A real Firestore database for the scratch tenant | Every store case |
| P-7 | An email mailbox the tester can **read**, not just send from | Every email case ends in "the mailbox receives X" |
| P-8 | Both webhook secrets configured **differently** in Chatwoot | The two receivers use different secrets; identical secrets 401 every delivery on one of them |
| P-9 | RBAC enabled with at least three test users: administrator, supervisor, agent | Every permission case |

**P-2 is the prerequisite most likely to be skipped and least able to be.**
Patches 0052–0060 have never been applied to a real Chatwoot checkout, and
several stack on each other, so the build may fail and need line-number fix-ups
before the SIT can start at all. **Budget time for that before scheduling the
SIT, not during it.**

**P-7 is a real constraint, not a formality.** In earlier live testing the tester
could drive the entire server side and could not read the destination mailbox, so
eight test cases were left unexecutable. A mailbox the tester can open is the
difference between an executed case and a blocked one.

### How to record a result

Each case is `PASS`, `FAIL`, or `BLOCKED` with the blocking reason. **`BLOCKED` is
a legitimate outcome and must not be recorded as `PASS`.** A case whose
prerequisite was missing was not tested.

Every `FAIL` gets a defect id and a severity per `../governance/qa-plan.md` §6.
**A SIT report with no failures across nine integration areas is not a credible
document**, and the client's own reviewers will read it that way — so a clean run
is itself a finding worth investigating.

---

## 1. Chatwoot ↔ agent service

The webhook contract: verify → dedupe → return 200 → dispatch to a background
task.

| ID | Case | Preconditions | Steps | Expected | Result |
|---|---|---|---|---|---|
| CW-01 | Valid signature accepted | P-8 | Send a `conversation_updated` webhook with a correct `sha256=` HMAC over `f"{timestamp}."+body` | 200, event processed | |
| CW-02 | Bad signature rejected | P-8 | Same body, corrupted signature | **401**, nothing processed | |
| CW-03 | Stale timestamp rejected | P-8 | Correct signature, timestamp 400 s old | **401** (300 s skew window) | |
| CW-04 | The two receivers use different secrets | P-8 | Send a valid `/webhooks/chatwoot` payload signed with the **bot** secret | **401.** This case exists because configuring both with one secret is a *working* misconfiguration whose symptom is a feature that silently never fires | |
| CW-05 | Duplicate delivery deduped | — | Send the same payload twice with the same `X-Chatwoot-Delivery` | Processed **once**; second returns 200 without side effects | |
| CW-06 | Concurrent duplicate deliveries deduped | — | Send the same delivery id twice simultaneously | Exactly one processed — the atomic primary-key insert, not check-then-insert, is what makes this safe | |
| CW-07 | Missing delivery id still processes | — | Omit `X-Chatwoot-Delivery` | Processed (cannot dedupe → process) | |
| CW-08 | 200 returns before slow work | — | Time the response with a slow downstream | Response **well under** the downstream latency | |
| CW-09 | Downstream failure does not raise | — | Make the Chatwoot API fail, then send a webhook | 200; failure logged and skipped; **no unretrieved-exception log** | |
| CW-10 | `contact_updated` accepted | — | Send the event | 200, no error (handler is a deliberate no-op stub) | |
| CW-11 | `conversation_status_changed` accepted | — | Send the event | 200, no error | |
| CW-12 | Escalation label fires the email path | P-7 | Add the `escalate` label to a conversation on an **Email-channel** inbox | Customer acknowledgement **and** PIC/dealer forward both arrive (EM-7 two-thread) | |
| CW-13 | Escalation label on a non-email inbox | — | Add `escalate` on a WhatsApp inbox | No escalation email; acknowledgement posted in-thread | |
| CW-14 | Dealer escalation timestamp stamped once | — | Add a `dealer_<slug>` label, then remove and re-add it | `dealer_escalated_at` set on first application and **never overwritten** | |
| CW-15 | Bot receiver acts only on a pending conversation | — | Send an incoming customer message on an `open` conversation | No AI action taken | |
| CW-16 | Debounce coalesces a burst | — | Send five customer messages within the debounce window | **One** Gemini call, against the full re-fetched history | |
| CW-17 | A task past its sleep is not cancelled | — | Send a message, wait past the debounce, send another mid-processing | The first completes; **no partial side effects** | |
| CW-18 | `AGENT_MODE=suggest` posts a private note | — | Trigger the bot with the default mode | Reply posted as a **private note**; conversation reopened for a human | |
| CW-19 | `AGENT_MODE=auto` sends directly | — | Set `auto`, trigger the bot | Reply sent to the customer | |
| CW-20 | Every decision is logged | — | Trigger the bot; inspect `ai_actions` | One row per decision, written **before** execution | |

---

## 2. agent service ↔ backend

Deliberately fail-open: no shared process, no shared database, HTTP only.

| ID | Case | Preconditions | Steps | Expected | Result |
|---|---|---|---|---|---|
| AB-01 | Persona fetch reaches the bot prompt | P-3 | Set a distinctive persona on an inbox; trigger the agent-bot | The persona's product name and guardrails are present in the decision prompt | |
| AB-02 | Empty persona preserves behaviour | P-3 | Clear the persona; trigger the bot | Behaviour **byte-identical** to the pre-persona default | |
| AB-03 | Backend outage fails open | — | Stop the backend; trigger the bot | Bot still answers using its local path; failure logged, no 500 | |
| AB-04 | Unset backend URL fails open | — | Clear `PROTON_BACKEND_URL`; trigger the bot | Works; no exception. The client accessor returns `None` so callers need no flag branch | |
| AB-05 | Wrong backend key | — | Set a wrong `PROTON_BACKEND_KEY`; trigger the bot | 401 from the backend, handled fail-open, logged | |
| AB-06 | Escalation notify reaches the backend | P-7 | Trigger an email escalation | `notify_email_escalation` called; both threads sent | |
| AB-07 | Lifecycle messages come from the operator config | — | Set a custom idle-warning message; let a conversation idle | The **operator's** wording is sent, not the built-in default | |
| AB-08 | `/assist/suggest` from the CRM panel | P-3 | Use the agent-assist panel on a live conversation | A grounded suggestion returns within an acceptable latency | |
| AB-09 | `/assist/summarize` from the CRM | P-3 | Trigger Summarise | A summary is returned and posted as a private note | |
| AB-10 | Summary omits identifiers | P-3 | Summarise a conversation containing a name, phone, email and plate number | None appear in the summary. **Note: this is a prompt, not a control — nothing validates the output (risk R15). A pass here is one observation, not a guarantee** | |
| AB-11 | Copilot multi-turn | P-3 | Ask Copilot two dependent questions | The second answer uses the first's context | |
| AB-12 | Translate action | P-3, P-9 | As an agent with `translation.use`, translate a customer message | Translation returned | |
| AB-13 | Translate denied without the permission | P-9 | As an agent without `translation.use` | **403** | |
| AB-14 | Translate disabled reports a reason | — | Set `TRANSLATION_ENABLED=false`; translate | `{"disabled": true, "reason": …}` with **no `translation` field**, and the fork button shows a legible refusal — not a 404 | |

---

## 3. Twilio WhatsApp

| ID | Case | Preconditions | Steps | Expected | Result |
|---|---|---|---|---|---|
| WA-01 | Inbound text creates a conversation | P-5 | Send a WhatsApp message to the tenant number | Conversation appears in Chatwoot with correct contact | |
| WA-02 | Outbound reply delivered | P-5 | Reply from Chatwoot | Message arrives on the handset | |
| WA-03 | Inbound image | P-5 | Send a photo | Attachment visible in Chatwoot | |
| WA-04 | Inbound voice note | P-5 | Send a voice note | Attachment visible; transcribed if `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED` | |
| WA-05 | Media understanding reaches the model | P-3, P-5 | With both media flags on, send a photo of a visible vehicle fault | The reply **names something specific it observed**, states a confidence level, and asks **at most one** follow-up question. Never a generic "please describe the issue" | |
| WA-06 | No media leakage on a text turn | P-3, P-5 | Immediately follow WA-05 with a plain text message | Nothing about the diagnostic instruction appears in the reply | |
| WA-07 | Video over budget refused early | P-5 | Send a video above `WHATSAPP_VIDEO_MAX_BYTES` | Dropped with a log; the turn proceeds on the remaining content | |
| WA-08 | Delivery status recorded | P-5 | Send outbound; inspect status callbacks | Status transitions recorded | |
| WA-09 | Failed delivery to an invalid number | P-5 | Send to an invalid number | Failure recorded, not silently dropped | |
| WA-10 | New-inbound alert raises a toast | P-2 | With `INBOUND_ALERTS_ENABLED` **and** `inbound_alerts` reaching `PROTON_FEATURES`, send an inbound message | **Toast within 2 seconds in the main Chatwoot UI. Screenshot required.** | |
| WA-11 | No alert on the agent's own reply | P-2 | Reply as the agent | **No alert fires.** This is the case that makes the feature tolerable rather than hated | |
| WA-12 | Sound only when enabled | P-2 | Enable sound in alert preferences; send inbound | Sound fires. Default is toast-only, deliberately: with sound on, a tenant doing most of its volume on WhatsApp beeps constantly and agents disable **all** alerting, including SLA breach | |
| WA-13 | Desktop notification after permission | P-2 | Grant notification permission; send inbound | Notification appears | |
| WA-14 | Degraded indicator on a blocked stream | P-2 | Block the event stream at the network level | **Degraded indicator appears**; falls back to the 60-second poll; **no alert fires twice on reconnect** | |
| WA-15 | One switch, not two | P-2 | Set only `INBOUND_ALERTS_ENABLED=true` on a tenant that never listed `inbound_alerts` in `PROTON_FEATURES` | Alerts work. **This is the case that proves patch 0058; before it, the documented switch did nothing** | |

---

## 4. Twilio Voice

**Nothing in this section has ever run against a real call** (risk R10). Treat a
high failure rate here as expected rather than alarming.

| ID | Case | Preconditions | Steps | Expected | Result |
|---|---|---|---|---|---|
| VO-01 | Inbound call answered | P-5 | Call the tenant number | Call answered; greeting played | |
| VO-02 | Recording announcement played | P-5 | Call with `PHONE_RECORDING_ENABLED` | The announcement is heard **before** recording begins | |
| VO-03 | DTMF menu routes | P-5 | Press each menu option | Each routes as configured | |
| VO-04 | Invalid DTMF re-prompts | P-5 | Press an unmapped key | Re-prompt, no dead air | |
| VO-05 | Handoff to a human dials out | P-5 | Request an agent | Call dials `PHONE_HANDOFF_TARGET_NUMBER` with the configured caller id | |
| VO-06 | Handoff timeout falls back | P-5 | Let the handoff ring past `PHONE_HANDOFF_TIMEOUT_SECONDS` | Falls back gracefully — voicemail or a message, never silence | |
| VO-07 | Recording stored and retrievable | P-5, P-9 | Complete a recorded call, then `GET /calls/{id}/recording` as a user with `call_recording.listen` | A signed URL returns and an audit row is written. **Expected to FAIL: the handler reads an in-process registry nothing in production writes (R16). Recorded as a case so the gap is visible** | |
| VO-08 | Recording retrieval denied | P-9 | Same request without the permission | **403** | |
| VO-09 | Voicemail ingested | P-5 | Leave a voicemail | Appears as a conversation with audio | |
| VO-10 | After-hours handling | P-5 | Call outside business hours with `PHONE_AFTER_HOURS_ENABLED` | After-hours message played | |
| VO-11 | RSA bypasses after-hours | P-5 | Choose the roadside-assistance option after hours with `PHONE_RSA_AFTER_HOURS_BYPASS` | Call proceeds — roadside assistance is not office hours | |
| VO-12 | Live transcript appears | P-3, P-5 | Speak during a call with the transcript flags on | Transcript updates in the CRM | |
| VO-13 | Transcript classification | P-3, P-5 | Complete a call about a known topic | Classified onto the correct case category | |
| VO-14 | ACW after call end | P-5 | End a call assigned to an agent | The agent enters after-call-work state | |
| VO-15 | ACW with no assignee | P-5 | End a call with no assignee | Logged and skipped. **Never guesses an agent** | |
| VO-16 | Language nudge | P-5 | Speak Malay with `PHONE_LANGUAGE_NUDGE_ENABLED` | The nudge behaves as designed | |
| VO-17 | Token rate limit | P-5 | Exceed `PHONE_TOKEN_RATE_LIMIT` | Rate-limited, not a 500 | |

---

## 5. Email

**Every case here ends in "a mailbox receives something" — P-7 is mandatory.**

| ID | Case | Preconditions | Steps | Expected | Result |
|---|---|---|---|---|---|
| EM-01 | IMAP inbound creates a conversation | P-7 | Email the tenant address | Conversation created, body and subject intact | |
| EM-02 | SMTP outbound delivered | P-7 | Reply from Chatwoot | Email arrives, correct From | |
| EM-03 | Auto-acknowledgement sent | P-7 | Send an inbound email | Auto-ack arrives using the operator's `EMAIL_AUTOACK_TEMPLATE` | |
| EM-04 | Escalation: customer thread | P-7 | Escalate an email conversation | Customer acknowledgement arrives in the **existing** thread | |
| EM-05 | Escalation: PIC/dealer thread | P-7 | Same escalation | PIC/dealer forward arrives as a **separate** thread | |
| EM-06 | Reply linking | P-7 | Reply to the customer acknowledgement | The reply lands on the **same** conversation, not a new one | |
| EM-07 | Dealer reply linking | P-7 | Reply to the dealer forward | Lands correctly and does not leak to the customer thread | |
| EM-08 | Attachments carried to the PIC | P-7 | Escalate a conversation with a photo and a PDF, with a non-zero attachment budget | Both arrive on the PIC mail | |
| EM-09 | Attachment budget respected | P-7 | Escalate with attachments over budget | Over-budget items dropped with a log; the mail still sends | |
| EM-10 | Bounce handled | P-7 | Escalate to a non-existent address | The DSN does **not** become a live customer case | |
| EM-11 | Blocklist enforced | P-7 | Attempt to mail an address in `EMAIL_BLOCKED_RECIPIENTS` | Blocked at the transport level | |
| EM-12 | Reply acknowledgement | P-7 | Reply to an escalated case with the flag on | Acknowledgement behaves as designed | |
| EM-13 | Operator template used | P-7 | Edit the escalation ack template, then escalate | The **edited** wording is sent | |
| EM-14 | Presence check | P-7 | Escalate with `ESCALATION_PRESENCE_CHECK_ENABLED` and nobody on duty | Routed per the on-duty rules rather than into a void | |

---

## 6. BigQuery

**None of the 33 views has ever been executed** (risk R13).

| ID | Case | Preconditions | Steps | Expected | Result |
|---|---|---|---|---|---|
| BQ-01 | `ensure_views()` creates every view | P-4 | Run it once against a real dataset | All 33 created. **Note: it re-creates every view; this is not additive** | |
| BQ-02 | Conversation sync loads rows | P-4 | Run a sync | Rows land in `conversations` with correct types | |
| BQ-03 | Window-over-aggregate | P-4 | Query `v_ai_vs_human` | Returns without error — `SUM(COUNT(*)) OVER (PARTITION BY …)` is unproven | |
| BQ-04 | `RANK()` inside a `CASE` | P-4 | Query `v_csat_by_agent` with `CSAT_BY_AGENT_ENABLED` | Returns without error; ranking correct | |
| BQ-05 | Time-zone agreement | P-4 | Query `v_channel_anomaly_hourly` | `EXTRACT(HOUR FROM created_at AT TIME ZONE '<z>')` and `DATE(created_at, '<z>')` agree on the same bucket. **The two take the zone in different positions; only a real query proves this** | |
| BQ-06 | Last complete hour | P-4 | Same view at a few minutes past the hour | Resolves to the last **complete** hour in the reporting zone | |
| BQ-07 | Zero-volume channel yields a row | P-4 | Query with a channel that had no conversations in the reference hour | A row with `current_volume` 0 via `COALESCE`, **not a missing row** | |
| BQ-08 | Sparse-hour baseline bias measured | P-4 | Inspect `baseline_days` for a sparse hour | The bias is quantified. It is conservative (a higher baseline suppresses rather than invents detections) but its size on real data is unmeasured | |
| BQ-09 | `token_usage` views created by hand | P-4 | Create the three cost views | They create; `v_ai_cost` requires `token_usage` to exist first, since BigQuery resolves base tables at creation time | |
| BQ-10 | `v_ai_cost` labels the agent service unmetered | P-4 | Query it | The `agent` service reads `cost_status='unmetered'` with a reason, **never zero spend** | |
| BQ-11 | `v_kb_staleness` cannot be created | P-4 | Attempt it | **Fails — it reads a `faq_entries` table that does not exist and that nothing populates.** Deliberately excluded from `ensure_views` so it cannot abort the loop | |
| BQ-12 | `qa_labels` migration | P-4 | With a pre-existing `qa_labels` table, enable `CALL_QA_ENABLED` and run the `ALTER TABLE` | Columns added; existing rows read `NULL`, not `0` | |
| BQ-13 | `ai_actions` migration | P-4 | Run the `ALTER TABLE` on a populated `ai_actions` | Additive and safe; existing rows read `NULL` | |
| BQ-14 | Schema compatibility | P-4 | Sync after the migrations | No column mismatch | |
| BQ-15 | Freshness stamp | P-4 | Query `/metrics/freshness` after a sync | A real `as_of`. **Then restart the container and re-query: it must report `as_of: null` / `unknown`, not a stamp of `now` on data of unknown age** | |
| BQ-16 | Dealer-escalation keying | P-4 | Create a case in one month, escalate it the next; query `v_dealer_escalation` | It appears in the **escalation** month, so the monthly total deliberately does not sum to the case count | |
| BQ-17 | Timezone change re-buckets | P-4 | Run `scripts/compare-reporting-timezone.py`, change `REPORTING_TIMEZONE`, re-run `ensure_views()` | Totals unchanged; cases move between adjacent periods **exactly as the comparison predicted** | |

---

## 7. Firestore

Every store, because a store nobody reads is a recurring failure mode here.

| ID | Case | Preconditions | Steps | Expected | Result |
|---|---|---|---|---|---|
| FS-01 | PIC store round-trip | P-6, P-9 | Add a department → contact mapping in the admin UI; escalate to it | The **stored** value is used, not `PIC_MAP_JSON` | |
| FS-02 | PIC env fallback | P-6 | Clear the store; keep `PIC_MAP_JSON`; escalate | The env value is used (store-first, env fallback) | |
| FS-03 | Dealer store round-trip | P-6, P-9 | Add a dealer slug → email; escalate | Mail reaches the stored address | |
| FS-04 | Routing priority store | P-6 | Set channel priorities; trigger routing | Priorities honoured | |
| FS-05 | SLA policy store | P-6, P-9 | Set a per-inbox SLA policy; breach it | The per-inbox policy applies, not the default | |
| FS-06 | SLA policy migration | P-6 | Against a pre-existing `sla_policies` table, verify the added columns | Present (`create_all` does nothing to an existing table — this repository has no Alembic) | |
| FS-07 | Taxonomy store round-trip | P-6, P-9 | Add a taxonomy node; categorise a case | Available and selectable | |
| FS-08 | Taxonomy retire, never delete | P-6, P-9 | Retire a node in use | Retired, not deleted; historical cases keep their category | |
| FS-09 | Taxonomy sync is downstream | P-6 | Break the Chatwoot sync; edit the taxonomy | **The edit succeeds.** A sync failure never rolls back an edit | |
| FS-10 | Category → department is suggest-only | P-6 | Pick a category with a mapped department | The department is **suggested**, never silently applied | |
| FS-11 | Targets store and seeding | P-6 | Seed targets, edit one, restart | The edit **survives** — seeding is create-only | |
| FS-12 | Status catalogue seeding | P-6 | Enable custom statuses; restart | Ten documents seeded; an operator's re-tinted "Lunch" survives | |
| FS-13 | Catalogue resolves when unseeded | P-6 | With the flag off, let ACW and the threshold sweeper run | Both resolve their statuses from the shipped definitions. The flag gates **selecting and editing**, not resolving | |
| FS-14 | Presence events written | P-6 | Change an agent's status | An event is written with the right source (`agent` vs `admin`) | |
| FS-15 | Alert rule store | P-6, P-9 | Set a personal alert preference; restart | It persists and overrides the account default | |
| FS-16 | Alert rules disabled reports a reason | P-6 | Set `ALERT_RULES_ENABLED=false`; open the preferences page | The page renders `{"disabled": true, "reason": …}` **verbatim**, not an empty table | |
| FS-17 | DMS config store | P-6, P-9 | Save DMS settings; run the connection test | Saved; the test reports honestly | |
| FS-18 | Presence collection growth | P-6 | Run the poller for a measured period; count documents | The growth rate is **quantified**, so risk R11's retention decision has a number behind it | |

---

## 8. Gemini / Vertex AI

**No real model has ever answered in any test run to date** (risk R8).

| ID | Case | Preconditions | Steps | Expected | Result |
|---|---|---|---|---|---|
| AI-01 | Bot decision returns a tool call | P-3 | Trigger the agent-bot | One of the three tool calls, forced by `function_calling_config` mode `ANY` | |
| AI-02 | Non-tool output falls back to handoff | P-3 | Force plain text from the model | Falls back to `handoff_to_human` — a conversation must never silently stall | |
| AI-03 | Model error after retry falls back | P-3 | Make the model fail | Same handoff fallback | |
| AI-04 | The SDK call does not block the loop | P-3 | Send concurrent turns | Both progress — the sync SDK call runs via `asyncio.to_thread` | |
| AI-05 | Embeddings generated | P-3 | Author a live-FAQ entry | It embeds and becomes searchable | |
| AI-06 | KB search returns grounded hits | P-3, P-4 | Query `/kb/suggest` | Relevant hits with scores | |
| AI-07 | Vertex hits carry no score | P-3 | Inspect Vertex Search results | No `score` field, so they never clear the FAQ-suggestion confidence threshold **by construction** | |
| AI-08 | pgvector KB search | P-3 | Upload a document to `/kb/knowledge`; search | Retrieved | |
| AI-09 | Enabled with no embedder does not mount | P-3 | Enable the pgvector KB with credentials unavailable | `/kb/knowledge` is **not mounted** — uploads 404 rather than every document silently failing to embed | |
| AI-10 | Sentiment classification | P-3 | Send a negative message with the classifier on | Classified; **an unclassified case must never bucket as `neutral`** | |
| AI-11 | Calibration baseline, for real | P-3 | Run `test_calibration.py` with real credentials, per §5 of the baseline document | A report with `mode != "stub"`, and four real scores recorded. **This case is the entire point of R8: the stub scores 97–100% because one author wrote both the labels and the rules** | |
| AI-12 | Malay corpus, for real | P-3 | Run the 56-case corpus twice, with the query raw and normalised | Both pass rates recorded, with sample sizes. Only a measured improvement justifies enabling the normaliser | |
| AI-13 | Auto-summary on resolve | P-3 | Resolve a conversation with the flag on | A summary is posted as a private note | |
| AI-14 | Resolved-case index writes | P-3 | Same, with the index enabled | The summary is embedded and stored, labelled `resolved_case`. **No agent-facing surface queries it (R16) — this case proves the write path only** | |
| AI-15 | Cost metering records tokens | P-3 | Run several assist calls with metering on | `token_usage` rows with real usage metadata | |
| AI-16 | `chat.turn` records nothing | P-3 | Run a WhatsApp turn through the ADK agent | **No `chat.turn` token row exists.** The report labels the surface unmetered rather than free (R9) | |
| AI-17 | Cost report emits no total | P-3, P-4 | Query `/metrics/ai-cost` | `priced_subtotal_usd` only; unmetered surfaces carry `cost_usd: null` **and** `calls: null` | |

---

## 9. DMS / TSP integration shell

**The shell's `not_connected` behaviour is itself testable, and that is the whole
of what can be tested** (risk R3).

| ID | Case | Preconditions | Steps | Expected | Result |
|---|---|---|---|---|---|
| DM-01 | Unconfigured reads as not connected | P-9 | Open Customer 360 with the DMS unconfigured | **`not_connected`.** Never an empty "ok", which a service advisor would read as "this customer has no vehicles" | |
| DM-02 | Configured but unreachable | P-9 | Configure a bad endpoint; look up a customer | Reported as unreachable, not as empty | |
| DM-03 | Mock client is never the default | — | Inspect a fresh tenant | `DMS_MOCK_CLIENT_ENABLED` is off and no mock client is constructed anywhere | |
| DM-04 | Mock client is labelled when on | P-9 | Enable it deliberately; look up a customer | Data returns and is **visibly marked as demo data** | |
| DM-05 | Credentials never echo in an error | P-9 | `PUT` a malformed `credential` | The 422 does **not** contain the credential in its `input` field | |
| DM-06 | Connection test is honest | P-9 | Run it against a bad endpoint | Reports failure with a reason | |
| DM-07 | Customer 360 without RSA | P-9 | Enable RBAC, disable RSA | `/admin/customer360/search` **404s** and the boot log records `customer360_prerequisites_missing`. An easily-missed third mount condition | |
| DM-08 | Phone-number normalisation | P-9 | Look up the same Malaysian number in four formats (`+60…`, `60…`, `01…`, with spaces) | All four match the same contact. A lookup matching only one format presents a known customer as a stranger | |
| DM-09 | Vehicle-number lookup | P-9 | Look up by vehicle number | Correct contact and history | |

---

## 10. Access control (cross-cutting)

Included because scoping bugs are data-exposure bugs, not reporting
inconveniences.

| ID | Case | Preconditions | Steps | Expected | Result |
|---|---|---|---|---|---|
| AC-01 | Agent cannot reach an admin endpoint | P-9 | As the agent user, call `/authz/roles` | **403** | |
| AC-02 | Agent can set their own status | P-9 | As the agent, `POST /routing/presence/status` | Succeeds (`presence.set_own_status`) | |
| AC-03 | Agent cannot set another's status | P-9 | As the agent, set a colleague's status | **403** (`workforce.manage`) | |
| AC-04 | Agent can set their own alert preferences | P-9 | As the agent, `PUT /alerts/rules/mine/{event}` | Succeeds | |
| AC-05 | Agent cannot set account defaults | P-9 | As the agent, `PUT /alerts/rules/defaults/{event}` | **403** (`alerts.manage`) | |
| AC-06 | Missing token is 401, never an allow | P-9 | Call any gated endpoint with no Chatwoot token | **401** | |
| AC-07 | Invalid token is 401 | P-9 | Call with a corrupted token | **401** | |
| AC-08 | Chatwoot unreachable is 401 | P-9 | Block Chatwoot; call a gated endpoint | **401 — never a silent allow** | |
| AC-09 | RBAC off falls back to the shared secret | — | Disable RBAC; call a gated endpoint with `x-api-key` | Succeeds. **Confirm the client understands that every holder of that secret is then effectively an administrator** | |
| AC-10 | Data scopes are intersective | P-9 | Give a user two roles with different scopes | Adding a role **never widens** access | |
| AC-11 | Scoping is enforced in the query layer | P-9 | Call the API **directly**, bypassing the UI | Scoping still applies. Hiding UI is not enforcement | |
| AC-12 | Cannot revoke your own `roles.manage` | P-2, P-9 | Attempt it in the Roles & Permissions page | Guarded with an explicit confirmation across all roles — you cannot lock yourself out | |
| AC-13 | Partial save is reported | P-2, P-9 | Make several role changes, fail one mid-save | Reported as partial, **not half-applied silently**; the page re-reads the server and re-stages what never ran | |
| AC-14 | Audit trail written | P-9 | Perform an escalation and a reassignment | An audit row per action, visible at `/admin/audit` | |

---

## 11. Integration points that cannot be tested

**Listed with reasons rather than omitted.** A script that quietly covers only
what happens to work is a weaker deliverable than one that says where the limits
are — and these two will be the client's first questions, so answering them here
is better than being asked.

```
- [ ] DMS/TSP: no real endpoint exists — no API specification, no sandbox (Q4).
      Only the shell's not_connected behaviour is testable (section 9).

- [ ] Facebook / Instagram: no inbox can be created — blocked on Meta Business
      verification, a client-side process gate.
```

**DMS/TSP (open question Q4).** There is no endpoint, no API specification, no
sandbox and no credentials. Eight requirements depend on it. Section 9 tests the
shell — that an unreachable DMS reads as *"we could not ask"* rather than
*"this customer has no vehicles"* — and that is the entire testable surface. **No
SIT case can validate DMS data, because the only DMS data this platform can
produce is fabricated.** Unblocked by PROTON supplying a specification and a
sandbox.

**Facebook / Instagram.** An inbox **cannot be created at all** until Meta
Business verification completes for PROTON's business account. This is not a
technical gap — the code path is Chatwoot's own and unmodified — so there is
nothing to test and nothing to schedule. There are no cases to write, not zero
cases passing. Unblocked by PROTON completing verification; **the specific ask is
a target date**, because these cases, the training content and the acceptance
criteria all queue behind it.

Two further limitations that are not integration points but will affect the run:

- **Patches 0052–0060 have never been applied to a real Chatwoot checkout.** If
  the P-2 build fails, every case marked P-2 is `BLOCKED` rather than `FAIL`, and
  the SIT cannot start.
- **Automated call-QA scoring does not exist and is deliberately not tested.** The
  five QA criteria are only ever set from explicit `POST /qa/label` fields. A
  scorer built on a transcript pipeline that has never run would produce confident
  noise, so the live-call verification (section 4) must come first.

---

## 12. Case count and sign-off

| Area | Cases |
|---|---|
| 1. Chatwoot ↔ agent | 20 |
| 2. agent ↔ backend | 14 |
| 3. Twilio WhatsApp | 15 |
| 4. Twilio Voice | 17 |
| 5. Email | 14 |
| 6. BigQuery | 17 |
| 7. Firestore | 18 |
| 8. Gemini / Vertex | 17 |
| 9. DMS shell | 9 |
| 10. Access control | 14 |
| **Total** | **155** |
| Untestable integration points, with reasons | 2 |

### Sign-off

| | Name | Role | Date | Signature |
|---|---|---|---|---|
| Prepared by | | Delivery (Devoteam) | 2026-08-08 | |
| Reviewed by | | PROTON | | |
| **Agreed by** | | PROTON | | |

**Execution may not begin before the "Agreed by" row is complete**, and
`2026-08-08-sit-report.md` must quote that date. A report against an unagreed
script does not close §2.2.6.
