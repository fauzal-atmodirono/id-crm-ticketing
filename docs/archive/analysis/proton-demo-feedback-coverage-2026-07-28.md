# Proton Demo Feedback Coverage — 2026-07-28 Meeting Audit

**Source:** `docs/client-materials/Proton x Devoteam _ CRM System Update –
2026_07_28 09_58 WIB – Notes by Gemini.pdf` (raw timestamped transcript,
Yuda Adi PRATAMA / Caroline KRISTANTO of Devoteam demoing the CRM to
PRO-NET).

**Method:** every distinct request, confusion, live bug, aside, or promised
follow-up in the transcript was extracted, then cross-referenced against
`proton-crm-gap-analysis-2026-07-27.md` (written the day before, so
pre-dates this meeting), `crm-channel-ui-testing-guide.md` and
`crm-channel-interaction-guide.md` (both current as of 2026-08-04), and the
`dev-yuda` git history for anything built since 2026-07-28 not yet reflected
in docs. Pure small talk and demo-mechanics glitches (screen refreshes,
"can we see the background in white for the screen share") are excluded.

**Legend:** ✅ Fully covered · 🧪 Built since the demo — **needs testing /
verification** · ⚠️ Partially covered · ❌ Not covered · 🔲 Needs a
decision/input from Proton, not an engineering gap

> **Status refresh — 2026-08-04 (post overnight build).** Nine items that were
> ❌/⚠️ at audit time have since been built and (mostly) deployed to `default`
> + `proton`, but **none of them has been manually clicked through in a
> browser or confirmed against a real customer/number**. They are re-marked
> 🧪 rather than ✅: the code exists and is unit-tested, the behaviour is not
> yet proven. See "Test-before-you-demo checklist" at the bottom. No ⚠️ rows
> remain — everything previously partial is now either 🧪 or still ❌.
>
> **Status refresh — 2026-08-06 (Packages D/E/F + re-verification).** Four ❌
> rows were re-checked against the source rather than against the previous
> revision of this document, and one of them was wrong: **video understanding
> (#26) is built end-to-end** and the "presenter contradicted engineering"
> flag on it is withdrawn. Packages D, E and F also landed after the previous
> refresh and were not reflected here: the **Cases list** page (#13, #14), the
> **Weekly Report** page reconciling Proton's own deck (#29), and the
> **DMS/TSP integration shell** (#4). One genuinely new item was added from
> the transcript that the original audit folded into #21 — **custom agent
> status labels (#32)**. Auto-busy (#21), KB image-file uploads (#2) and
> custom statuses (#32) were re-confirmed as still unbuilt, with the negative
> evidence recorded inline so they don't need re-checking a third time.

---

## Knowledge base / FAQ

| # | What PRO-NET said/asked (≈timestamp) | Status | Evidence |
|---|---|---|---|
| 1 | Bulk CSV upload for FAQ Q&A pairs, instead of one-by-one manual entry (~00:06–08, ~00:45–46) | 🧪 | **Built 2026-08-04.** `POST /kb/faq/bulk` CSV endpoint (`features/chat/faq_admin_router.py`, commit `bad395c`) + "Bulk upload (CSV)" button on the fork's FAQs page (patch `0040`, commit `71afea0`). Deployed to `default`+`proton`; **not yet uploaded a real CSV through the UI** |
| 2 | KB document upload limited to PDF — asked whether uploads could carry pictures (~00:45–47) | ❌ | **Re-verified 2026-08-06, unchanged.** `kb_ingest.py::extract_text` accepts `.pdf`/`.docx`/`.md`/`.txt` only; anything else raises `UnsupportedFileType`, and the fork's upload input is `accept=".pdf,.docx,.md,.txt,.markdown"` (patch `0021` line 270). **Nuance worth saying out loud to Proton:** a PDF or DOCX that *contains* pictures uploads fine — the text is extracted and the images ignored. What is unsupported is uploading a bare image file (`.jpg`/`.png`) as a knowledge source. Their transcript question ("if you want to download PDF with pictures") is arguably already satisfied; the follow-up ("does it support image [files]") is not |
| 3 | KB document-upload button returned a raw error live ("still failed... 404") (~00:07, ~00:46) | ✅ | Patch `0033` fixed the raw-404 alert; `KNOWLEDGE_PG_ENABLED=true` live on proton since 2026-08-03 — Uploads screen works today |
| 4 | Custom AI tools / DMS-API integration, demoed as mock (~00:08) | 🧪 | **Integration shell built 2026-08-05 (Package F).** `GET/PUT /admin/integrations/dms` config store with a write-only credential + connection test (`5c1311e`, `e555e8b`), a client port with null and mock implementations (`14d27e2`), and a **DMS/TSP card + config form** in the fork (patch `0045`, commit `7181522`). Customer 360 renders an optional DMS vehicle/service-history block, fail-open and off by default (`5993e87`, `c85fa89`). What exists is the *shell*: `DMS_MOCK_CLIENT_ENABLED=true` returns demo data behind a visible "Mock data" badge. **No real Proton DMS is connected** — the open questions are drafted at `docs/analysis/2026-08-05-dms-tsp-integration-questions-for-proton.md` and still unsent |
| 5 | PowerBI integration named in the original requirement (~00:10, ~01:20–22) | 🔲 | Testing guide §8 item 9: Proton is to share target report examples so the team can decide embed-vs-native; only native WebBI-style charts exist today |
| 6 | Idle-warning / auto-close threshold configuration (~00:13–15) | ✅ | Demoed live and works; writable per `Settings → Inboxes → Business Hours → Inactivity & auto-close` (testing guide §2). SOP ambiguity on the exact 10 vs 15 min figure remains a separate confirm-with-Proton item (gap analysis §8) |
| 7 | FAQ Assist / suggested-reply rated "not helpful" live; Copilot returned "couldn't find any information" (~00:50–53) | 🧪 | Gap analysis §4: `/suggest` was rewritten 2026-07-27 (day before) to synthesize the whole thread; the live miss was attributed to that conversation's KB not being grounded yet (a setup step). Code side is done — **relevance quality has still never been re-tested against a grounded KB**. Now easier to set up given #1 |

## Channels / routing

| # | What PRO-NET said/asked (≈timestamp) | Status | Evidence |
|---|---|---|---|
| 8 | How to see which channel a conversation came from (~00:20–21) | ✅ | Demoed via the channel filter |
| 9 | Confusion over inbox naming ("Proton API" / "Twilio Proton" / "website demo") (~00:21–23) | 🔲 | Demo-environment naming; production inboxes will presumably get clearer names — no code change implied |
| 10 | Live inbound email not wired (~00:23–26) | ❌ | Still blocked on SMTP/IMAP credentials per interaction guide §5 |
| 11 | Facebook/Instagram connection blocked (~00:29) | ❌ | Blocked on Meta Business verification, unchanged per both 2026-08-04 docs |
| 12 | No total summary of all channels in one glance (~00:30–31) | ✅ | Existing Reports overview dashboard covers this |
| 13 | Where can an agent check all pending/follow-up cases (~00:31, presenter uncertain live) | 🧪 | **Superseded by a better answer than the one recorded here.** The `chatwoot-my-tasks` dashboard app still exists, but the direct answer is now the **Cases list** (patch `0043`, commit `c5b99b4`, Package D): one filterable table over existing conversation data with Division / Case type / Status / Channel / Dealer filters and an Aging (days) column, so an agent or supervisor can scan the whole book of work. Gated by the same permission as Customer 360. **Never demoed** |
| 14 | Per-ticket detail dashboard (caller, phone, status) for every case (~00:31–34) | 🧪 | Now answered from two directions: the **Cases list** (#13) gives the per-case row — Case ID, Division, Concern, Purchased From, Escalated To, Car Plate, Aging, Status, clicking through to the conversation — and **Customer 360** (#15) gives the per-*customer* rollup. Between them this is most of what was asked. **Still needs a browser pass** to judge whether Proton also wants a distinct case record separate from the conversation |
| 15 | Vehicle-number / phone-number lookup across conversations (Customer 360) (~00:34–35) | 🧪 | **Foundational version built 2026-08-04.** `GET /admin/customer360/search?q=` (`features/chat/customer360_router.py`, commit `41d7271`, permission `customer360.view`) + fork search page (patch `0041`, commit `cd31ea5`). Phone lookup is exact against Chatwoot contacts; **vehicle lookup is approximate** — it matches RSA incident `vehicle_no` plus the conversation `vehicle_model` custom attribute, because Chatwoot exposes no true vehicle-number field. Still not a DMS integration (#4). **Untested in a browser; test with a real Proton phone + vehicle number** |
| 16 | Which unique customer identifier to use for grouping (CIF-like ID vs phone vs vehicle) (~00:42–44) | 🔲 | Fauzal: "we need to discuss with Rafael and team" — genuinely open design question feeding Customer 360, not resolved anywhere in docs |
| 17 | Email escalation as two separate emails (customer ack + internal/dealer forward), no CC/BCC (~00:35–37, ~01:30–32) | 🧪 | EM-7 built/deployed 2026-08-03; the dept→PIC and dealer→email mappings that were an "edit raw config" ops task are now **self-service**: `/admin/escalation` CRUD router (`pic_admin_router.py` + Firestore `PicStore`/`DealerStore`, commits `d9b43d2`…`d0d4406`, permission `escalation.manage`) behind a fork **Escalation Routing** page (patch `0039`). **The mappings are still empty for proton** — populate them, then send a test escalation and confirm both legs actually land |
| 18 | Who hosts/owns the email service — Devoteam-provided or Proton's own SMTP? (~00:38–39) | 🔲 | Fauzal confirmed Devoteam can host; explicit ask for Proton to supply a subdomain + credentials (testing guide §8 item 1) |
| 19 | Is there a count of cases created/closed entirely by AI, no human? (~00:38–42, presenter uncertain live) | ✅ | `features/metrics/bigquery_schema.py` has a `resolved_by` field with `closed_by_bot`/`transfer_to_agent` counts already in the schema — just not confidently demoed |

## Agent management / assignment

| # | What PRO-NET said/asked (≈timestamp) | Status | Evidence |
|---|---|---|---|
| 20 | Is there a configurable cap on how many tickets round-robin to one agent? (~00:59–01:00) | 🧪 | **Built 2026-08-04.** `ROUTING_MAX_CONCURRENT_PER_AGENT` (`platform/config.py::routing_max_concurrent_per_agent`); `PresenceFetcher.fetch_agent_open_counts` tallies each agent's open conversations (commit `2e1c2e6`, fail-open per `b81cb70`) and `RoutingService.pick_agent` drops agents at/over the cap from the `online` pool before all three tiers (commit `e056cf2`). **Ships at `0` = unlimited on every tenant** — set it on a tenant and verify overflow actually skips the capped agent |
| 21 | Auto-set agent status to "busy" during a phone call, so WhatsApp doesn't also route to them (~01:01–07) | ❌ | **Re-verified 2026-08-06, unchanged.** `presence.py` exposes only `fetch_agents` / `fetch_agent_availability` / `fetch_agent_open_counts` — all reads. A repo-wide search for a write path (`profile/availability`, `update_agent`, any assignment to `availability_status`) returns nothing outside `service.py`'s read comparison and `presence.py`'s own parse. Nothing in `phone/bridge.py` or `phone/call_control.py` writes a status when a call starts or ends. Still blocked on the same open per-agent-numbers decision (Package C spec §5.2) |
| 32 | Custom agent status labels beyond available/offline — "toilet break", "lunch", follow-up (~01:02) | ❌ | **New row; the original audit folded this into #21, but it's a separate ask.** Routing already honours the *native* Chatwoot `online`/`busy`/`offline` triple — `RoutingService.pick_agent` filters to `availability_status == "online"` across all three tiers — and patch `0024` renders those states in the Agent Priorities table. What does not exist is any way for an operator to define *additional named* statuses, or to map a custom label onto "don't route to me". Integration point is the same `presence.py` boundary as #21, so the two are best built together |

## Phone / IVR

| # | What PRO-NET said/asked (≈timestamp) | Status | Evidence |
|---|---|---|---|
| 22 | DTMF ("press 1") vs. conversational LLM routing — which ships? (~01:07–09) | 🔲 | Explicitly flagged as an open choice in the meeting; matches IVR-5, still unresolved as of 2026-08-04 |
| 23 | Hand-off to a live human agent on a call (~01:11–13) | 🧪 | **Built 2026-08-05** (Package C Task 6, commits `83160fe`…`a19eaff`): `request_human_handoff` now redirects the live call into a real Twilio `<Dial>` to `phone_handoff_target_number`, with explicit fallback TwiML for `no-answer`/`busy`/`failed` and business-hours gating. Default off (`phone_handoff_enabled=false`); requires `phone_handoff_caller_id` or it deliberately refuses to dial rather than drop the call (Twilio error 13214 on the browser-softphone path). **Nothing has been proven against a real Twilio number or a real second phone** — a full manual verification runbook exists at `docs/testing/phone-channel-package-c-verification.md` (Scenarios 5–7) but no one has executed it yet |
| 24 | RSA (accident/road-side) after-hours routing to the 24/7 line (~01:14–15) | ✅ | Business-hours-aware transfer logic confirmed live in the orchestrator. (Separate RSA incident-log *page* is code-complete but not deployed on every tenant yet — a deploy step, not a gap in this specific ask) |
| 25 | WhatsApp voice notes — text-only during the demo (~01:15–16) | 🧪 | `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true` live on proton since 2026-08-03, code complete — **still unconfirmed against a real WhatsApp number**. Send a voice note to the proton number and check the bot transcribes/answers it |
| 26 | Customer-sent videos (e.g. of the car) should be processable on website/WhatsApp (~01:16–18) | 🧪 | **Correction — this row was wrong, and the discrepancy flag is withdrawn.** Video understanding *is* built end-to-end (commits `57e5d54`, `efbcb96`): `orchestrator._MEDIA_KINDS = ("audio", "image", "video")` pulls the first video attachment off the turn, `_apply_media_budget` bounds it by `whatsapp_video_max_bytes` (default 14 MB, applied to the whole audio+image+video payload, not per attachment), and `video_base64`/`video_mime_type` flow through `clients/proton.py` → backend `chat/router.py` → `chat/service.py`, which decodes them into a Gemini `Part`. Shares the `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED` flag with #25. What the presenter told Proton was accurate; the 2026-08-04 docs asserting "audio/image only, out of scope by design" describe an earlier state and are the stale source. **Still never tested against a real WhatsApp video** |
| 27 | Call recording for QA/compliance (~01:18) | 🧪 | **Built 2026-08-05** (Package C Task 5, commits `cdf70aa`, `14685c6`): dual-channel Twilio recording, gated behind `phone_recording_enabled` (default off), attaches `recording_sid`/`recording_duration`/`recording_url` as internal-only Chatwoot custom attributes (never a customer/agent-visible comment) retrievable only with the `call_recording.listen` permission. Requires `phone_recording_announcement` (PDPA notice text) and `twilio_webhook_base_url` or recording refuses to start. Retention (`phone_recording_retention_days`, default 90) is policy-only — no automated deletion job enforces it yet. **Never tested against a real call** — see `docs/testing/phone-channel-package-c-verification.md` (Scenario 4) |

## Reporting / RBAC

| # | What PRO-NET said/asked (≈timestamp) | Status | Evidence |
|---|---|---|---|
| 28 | Separate report views for admin vs. team leader vs. agent (~01:18–19, presenter uncertain live) | 🧪 | Patch `0028-chatwoot-access-permissions.patch` (commit `5991893`, 2026-08-02 — *after* the demo) added a "Chatwoot access" section to Roles & Permissions with a `Reports` checkbox, gated behind `RBAC_ENABLED`. Built and deployed, **never verified by logging in as a non-admin role, and never shown to the client** |
| 29 | Proton to share reference report/visualization examples (~01:22–24, ~01:35–37) | 🧪 | **Delivered and built against.** Proton shared `Weekly Report Proton e.MAS.pptx` and `MONTHLY REPORTING FOR Proton e.MAS.pptx` (both now tracked under `docs/client-materials/`), and Package E built to them: a **Weekly Report** page (patch `0044`, commit `a1952e8`) reconciling case volume + WoW change, case status trend, department/PIC detail, call-centre & SLA performance, dealer escalation turnaround, WIP aging and per-case detail against a movable 7-day window; plus **Departments & PIC**, **Case Lifecycle** and **Anomaly** report pages (patch `0034`). Note the page's own reconciliation caveat: Per-Case Detail reads live conversations while Case Volume reads the reporting warehouse, so small count differences are expected. **Never walked through with Proton** |
| 30 | Category → subcategory cascading dropdown, main category unique / subcategory dependent (~01:23–30) | 🧪 | Fork patch for cascading `case_category`→`case_subcategory` built and deployed to proton 2026-08-03/04 (patch `0036`, commit `6d08afb`) — **still needs the manual browser confirmation that the subcategory list actually filters by the chosen category** |
| 31 | General "some adjustment to the UI, some more on the UX" (~01:35–37) | 🔲 | Vague, no specifics given — flag for a follow-up conversation |

---

## Summary

| Status | At audit (2026-07-28) | 2026-08-05 | Now (2026-08-06) |
|---|---|---|---|
| ✅ Fully covered | 7 | 7 | 6 |
| 🧪 Built, needs testing | — | 11 | 15 |
| ⚠️ Partially covered | 6 | 0 | 0 |
| ❌ Not covered | 11 | 6 | 5 |
| 🔲 Needs Proton's input | 7 | 7 | 6 |
| **Total** | **31** | **31** | **32** |

Three items moved ❌ → 🧪 (#1 FAQ bulk CSV, #15 Customer 360, #20 round-robin
cap) and all six ⚠️ items moved to 🧪 (#7, #14, #17, #25, #28, #30). Two more
moved ❌ → 🧪 on 2026-08-05 (#23 real human hand-off, #27 call recording —
Package C, commits `5c2659f`…`a19eaff`). Nothing
moved to ✅ — that requires the verification below.

**2026-08-06 movements.** #26 video understanding ❌ → 🧪 (it was built all
along; the row was wrong). #4 DMS integration ❌ → 🧪 (Package F shell). #29
reference reports 🔲 → 🧪 (Proton delivered the decks, Package E built to
them). #13 follow-up queue ✅ → 🧪 (the Cases list is a better answer than the
`my-tasks` app previously cited, but unlike that app it has never been
demoed, so it can't stay ✅). #32 custom agent statuses added as a new ❌ row,
split out of #21. Still nothing has moved *to* ✅ — that requires hands on a
browser, not another pass over the code.

### 🧪 Test-before-you-demo checklist

Everything here is code-complete and unit-tested; none of it has been proven
by hand. Overlaps the "New this build" checklist in
`crm-channel-interaction-guide.md` §10.

| # | Item | Test | Where |
|---|---|---|---|
| 1 | FAQ bulk CSV upload | Upload a small `question,answer,keywords,tags` CSV; expect a created/errors count + refreshed list | Knowledge → FAQs → "Bulk upload (CSV)" |
| 15 | Customer 360 | Search a known proton phone number, then a vehicle number; check the contact + cross-channel conversations + RSA incidents come back, and that an unknown value returns an empty result, not an error | Sidebar → Customer 360 (needs `customer360.view`) |
| 14 | Per-case detail | While on #15, judge whether the aggregated view answers "caller/phone/status per case" or a separate per-case record is still wanted — this is a **question for Proton**, not just a test | same page |
| 17 | Two-thread escalation | Add a PIC dept entry + a dealer email, save, refresh (must persist), then trigger an escalation on an email inbox and confirm **both** the customer ack and the internal/dealer mail arrive, with no CC/BCC | Sidebar → Escalation Routing (needs `escalation.manage`) |
| 20 | Round-robin cap | Set `ROUTING_MAX_CONCURRENT_PER_AGENT` to a small number on a test tenant, load one agent to the cap, confirm the next conversation skips them | tenant env → restart backend |
| 30 | Category cascade | Pick a main category, confirm the subcategory list narrows to that category's children only | conversation sidebar custom attributes |
| 25 | WhatsApp voice note | Send a voice note to the proton WhatsApp number, confirm the bot answers its content | real device |
| 28 | RBAC report views | Log in as a non-admin role with `Reports` unchecked, confirm Reports is actually hidden (`RBAC_ENABLED` must be on) | Settings → Roles & Permissions |
| 7 | FAQ Assist relevance | With the KB now populated (via #1), re-run the exact query that returned "couldn't find any information" in the demo | conversation → Assist/Copilot |
| 23 | Real human hand-off | Enable `phone_handoff_enabled`+`phone_handoff_target_number`+`phone_handoff_caller_id`, call in, trigger a handoff, confirm audio connects both ways; then repeat with the target left unanswered and confirm the bilingual apology + `unanswered_handoff` tag | real Twilio number + a second phone — full runbook: `docs/testing/phone-channel-package-c-verification.md` §2 Scenarios 5–7 |
| 27 | Call recording | Enable `phone_recording_enabled`+`phone_recording_announcement`, call in, hang up, confirm the recording attaches to the SAME conversation and is only readable with `call_recording.listen` | real Twilio number — full runbook: `docs/testing/phone-channel-package-c-verification.md` §2 Scenario 4 |
| 13 | Cases list | Open Cases, apply each filter, confirm the table populates and a Case ID click-through opens the right conversation; watch for the "showing only the first N" banner, which means filters and totals no longer reflect the whole account | Sidebar → Cases (same permission as Customer 360) |
| 29 | Weekly Report | Pick a week with real traffic, confirm every section renders and the "This week" / "All time" badges are right; be ready to explain the Per-Case-Detail vs Case-Volume count difference as two data sources, not a bug | Reports → Weekly Report |
| 4 | DMS/TSP shell | With `DMS_MOCK_CLIENT_ENABLED=true`, save a config, run the connection test, then confirm Customer 360 shows the vehicle/service block with a visible "Mock data" badge — and that the badge is impossible to miss, since this is demo data | Settings → Integrations → DMS/TSP, then Customer 360 |
| 26 | WhatsApp video | Send a short video of a car to the proton WhatsApp number and confirm the bot answers its *content*. Also send one over ~14 MB and confirm it degrades gracefully rather than erroring (`whatsapp_video_max_bytes` drops it with a warning log) | real device |

### ❌ items still genuinely unbuilt, worth prioritizing next

1. **Agent availability write-back (#21 + #32)** — one gap, two asks, and now
   the most valuable remaining routing work since its sibling (#20) is built.
   `presence.py` is a read-only boundary; making it writable unlocks both
   auto-busy-during-a-call and operator-defined status labels. Build them
   together. Blocked on the open per-agent-numbers decision (Package C spec
   §5.2) that also blocks a routing-aware handoff target — see
   `.superpowers/sdd/2026-08-04-pkg-c-telephony-handoff-transcript-recording/task-7-brief.md`.
2. **KB image-file uploads (#2)** — `kb_ingest.py::extract_text` still accepts
   `.pdf`/`.docx`/`.md`/`.txt` only, and the fork's file input matches. Worth
   scoping honestly with Proton first: PDFs and DOCX files *containing*
   pictures already work, so the real ask may be narrower than it sounded.
3. **A real DMS connection behind the #4 shell** — Customer 360's vehicle and
   service-history block is wired but fed by a mock. Send
   `docs/analysis/2026-08-05-dms-tsp-integration-questions-for-proton.md`.
4. Externally blocked, no engineering action available: inbound email SMTP/IMAP
   credentials (#10) and Meta Business verification (#11).

**Withdrawn from this list:** the video-understanding discrepancy, which was
never a discrepancy — see #26.
