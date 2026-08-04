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

---

## Knowledge base / FAQ

| # | What PRO-NET said/asked (≈timestamp) | Status | Evidence |
|---|---|---|---|
| 1 | Bulk CSV upload for FAQ Q&A pairs, instead of one-by-one manual entry (~00:06–08, ~00:45–46) | 🧪 | **Built 2026-08-04.** `POST /kb/faq/bulk` CSV endpoint (`features/chat/faq_admin_router.py`, commit `bad395c`) + "Bulk upload (CSV)" button on the fork's FAQs page (patch `0040`, commit `71afea0`). Deployed to `default`+`proton`; **not yet uploaded a real CSV through the UI** |
| 2 | KB document upload limited to PDF — asked whether uploads could carry pictures (~00:45–47) | ❌ | `backend/apps/backend/.../features/chat/kb_ingest.py::extract_text` only handles `.pdf`/`.docx`/`.md`/`.txt`; anything else raises `UnsupportedFileType`. No image/video ingestion exists |
| 3 | KB document-upload button returned a raw error live ("still failed... 404") (~00:07, ~00:46) | ✅ | Patch `0033` fixed the raw-404 alert; `KNOWLEDGE_PG_ENABLED=true` live on proton since 2026-08-03 — Uploads screen works today |
| 4 | Custom AI tools / DMS-API integration, demoed as mock (~00:08) | ❌ | Same root gap as Customer 360/DMS (gap analysis §6) — no DMS client exists in the repo |
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
| 13 | Where can an agent check all pending/follow-up cases (~00:31, presenter uncertain live) | ✅ | Gap analysis §3: `chatwoot-my-tasks` dashboard app + `features/tasks/deadline.py` already implement this — just not confidently demoed |
| 14 | Per-ticket detail dashboard (caller, phone, status) for every case (~00:31–34) | 🧪 | Conversation/contact panel already showed phone + status; the new **Customer 360** page (see #15) now aggregates contact + all their conversations + RSA incidents into one view, which is most of what was asked. **Needs a browser pass** to judge whether it satisfies the ask or a per-case record is still wanted |
| 15 | Vehicle-number / phone-number lookup across conversations (Customer 360) (~00:34–35) | 🧪 | **Foundational version built 2026-08-04.** `GET /admin/customer360/search?q=` (`features/chat/customer360_router.py`, commit `41d7271`, permission `customer360.view`) + fork search page (patch `0041`, commit `cd31ea5`). Phone lookup is exact against Chatwoot contacts; **vehicle lookup is approximate** — it matches RSA incident `vehicle_no` plus the conversation `vehicle_model` custom attribute, because Chatwoot exposes no true vehicle-number field. Still not a DMS integration (#4). **Untested in a browser; test with a real Proton phone + vehicle number** |
| 16 | Which unique customer identifier to use for grouping (CIF-like ID vs phone vs vehicle) (~00:42–44) | 🔲 | Fauzal: "we need to discuss with Rafael and team" — genuinely open design question feeding Customer 360, not resolved anywhere in docs |
| 17 | Email escalation as two separate emails (customer ack + internal/dealer forward), no CC/BCC (~00:35–37, ~01:30–32) | 🧪 | EM-7 built/deployed 2026-08-03; the dept→PIC and dealer→email mappings that were an "edit raw config" ops task are now **self-service**: `/admin/escalation` CRUD router (`pic_admin_router.py` + Firestore `PicStore`/`DealerStore`, commits `d9b43d2`…`d0d4406`, permission `escalation.manage`) behind a fork **Escalation Routing** page (patch `0039`). **The mappings are still empty for proton** — populate them, then send a test escalation and confirm both legs actually land |
| 18 | Who hosts/owns the email service — Devoteam-provided or Proton's own SMTP? (~00:38–39) | 🔲 | Fauzal confirmed Devoteam can host; explicit ask for Proton to supply a subdomain + credentials (testing guide §8 item 1) |
| 19 | Is there a count of cases created/closed entirely by AI, no human? (~00:38–42, presenter uncertain live) | ✅ | `features/metrics/bigquery_schema.py` has a `resolved_by` field with `closed_by_bot`/`transfer_to_agent` counts already in the schema — just not confidently demoed |

## Agent management / assignment

| # | What PRO-NET said/asked (≈timestamp) | Status | Evidence |
|---|---|---|---|
| 20 | Is there a configurable cap on how many tickets round-robin to one agent? (~00:59–01:00) | 🧪 | **Built 2026-08-04.** `ROUTING_MAX_CONCURRENT_PER_AGENT` (`platform/config.py::routing_max_concurrent_per_agent`); `PresenceFetcher.fetch_agent_open_counts` tallies each agent's open conversations (commit `2e1c2e6`, fail-open per `b81cb70`) and `RoutingService.pick_agent` drops agents at/over the cap from the `online` pool before all three tiers (commit `e056cf2`). **Ships at `0` = unlimited on every tenant** — set it on a tenant and verify overflow actually skips the capped agent |
| 21 | Auto-set agent status to "busy" during a phone call, so WhatsApp doesn't also route to them (~01:01–07) | ❌ | `features/routing/presence.py` only *reads* Chatwoot presence; nothing in `phone/bridge.py` writes back an availability/busy status when a call starts |

## Phone / IVR

| # | What PRO-NET said/asked (≈timestamp) | Status | Evidence |
|---|---|---|---|
| 22 | DTMF ("press 1") vs. conversational LLM routing — which ships? (~01:07–09) | 🔲 | Explicitly flagged as an open choice in the meeting; matches IVR-5, still unresolved as of 2026-08-04 |
| 23 | Hand-off to a live human agent on a call (~01:11–13) | 🧪 | **Built 2026-08-05** (Package C Task 6, commits `83160fe`…`a19eaff`): `request_human_handoff` now redirects the live call into a real Twilio `<Dial>` to `phone_handoff_target_number`, with explicit fallback TwiML for `no-answer`/`busy`/`failed` and business-hours gating. Default off (`phone_handoff_enabled=false`); requires `phone_handoff_caller_id` or it deliberately refuses to dial rather than drop the call (Twilio error 13214 on the browser-softphone path). **Nothing has been proven against a real Twilio number or a real second phone** — a full manual verification runbook exists at `docs/testing/phone-channel-package-c-verification.md` (Scenarios 5–7) but no one has executed it yet |
| 24 | RSA (accident/road-side) after-hours routing to the 24/7 line (~01:14–15) | ✅ | Business-hours-aware transfer logic confirmed live in the orchestrator. (Separate RSA incident-log *page* is code-complete but not deployed on every tenant yet — a deploy step, not a gap in this specific ask) |
| 25 | WhatsApp voice notes — text-only during the demo (~01:15–16) | 🧪 | `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true` live on proton since 2026-08-03, code complete — **still unconfirmed against a real WhatsApp number**. Send a voice note to the proton number and check the bot transcribes/answers it |
| 26 | Customer-sent videos (e.g. of the car) should be processable on website/WhatsApp (~01:16–18) | ❌ | **Discrepancy worth flagging to Proton.** Presenter said live "the functionality is there... we can process the video" — but both 2026-08-04 docs explicitly state video understanding is "genuinely unbuilt (audio/image only), out of scope by design." What was told to the client contradicts current engineering status |
| 27 | Call recording for QA/compliance (~01:18) | 🧪 | **Built 2026-08-05** (Package C Task 5, commits `cdf70aa`, `14685c6`): dual-channel Twilio recording, gated behind `phone_recording_enabled` (default off), attaches `recording_sid`/`recording_duration`/`recording_url` as internal-only Chatwoot custom attributes (never a customer/agent-visible comment) retrievable only with the `call_recording.listen` permission. Requires `phone_recording_announcement` (PDPA notice text) and `twilio_webhook_base_url` or recording refuses to start. Retention (`phone_recording_retention_days`, default 90) is policy-only — no automated deletion job enforces it yet. **Never tested against a real call** — see `docs/testing/phone-channel-package-c-verification.md` (Scenario 4) |

## Reporting / RBAC

| # | What PRO-NET said/asked (≈timestamp) | Status | Evidence |
|---|---|---|---|
| 28 | Separate report views for admin vs. team leader vs. agent (~01:18–19, presenter uncertain live) | 🧪 | Patch `0028-chatwoot-access-permissions.patch` (commit `5991893`, 2026-08-02 — *after* the demo) added a "Chatwoot access" section to Roles & Permissions with a `Reports` checkbox, gated behind `RBAC_ENABLED`. Built and deployed, **never verified by logging in as a non-admin role, and never shown to the client** |
| 29 | Proton to share reference report/visualization examples (~01:22–24, ~01:35–37) | 🔲 | Same open item as #5, tracked in testing guide §8 item 9 |
| 30 | Category → subcategory cascading dropdown, main category unique / subcategory dependent (~01:23–30) | 🧪 | Fork patch for cascading `case_category`→`case_subcategory` built and deployed to proton 2026-08-03/04 (patch `0036`, commit `6d08afb`) — **still needs the manual browser confirmation that the subcategory list actually filters by the chosen category** |
| 31 | General "some adjustment to the UI, some more on the UX" (~01:35–37) | 🔲 | Vague, no specifics given — flag for a follow-up conversation |

---

## Summary

| Status | At audit (2026-07-28) | Now (2026-08-05) |
|---|---|---|
| ✅ Fully covered | 7 | 7 |
| 🧪 Built, needs testing | — | 11 |
| ⚠️ Partially covered | 6 | 0 |
| ❌ Not covered | 11 | 6 |
| 🔲 Needs Proton's input | 7 | 7 |
| **Total** | **31** | **31** |

Three items moved ❌ → 🧪 (#1 FAQ bulk CSV, #15 Customer 360, #20 round-robin
cap) and all six ⚠️ items moved to 🧪 (#7, #14, #17, #25, #28, #30). Two more
moved ❌ → 🧪 on 2026-08-05 (#23 real human hand-off, #27 call recording —
Package C, commits `5c2659f`…`a19eaff`). Nothing
moved to ✅ — that requires the verification below.

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

### ❌ items still genuinely unbuilt, worth prioritizing next

1. **Auto-busy status during calls (#21)** — `features/routing/presence.py`
   still only *reads* Chatwoot availability; nothing in
   `features/chat/phone/bridge.py` writes a busy status when a call starts.
   Now the most valuable remaining routing gap, since its sibling (#20) is
   built. Blocked on the same open per-agent-numbers decision (spec §5.2)
   that also blocks a routing-aware handoff target — see
   `.superpowers/sdd/2026-08-04-pkg-c-telephony-handoff-transcript-recording/task-7-brief.md`.
2. **Video-understanding discrepancy (#26)** — needs clarifying with Proton
   before it becomes a support commitment the platform can't currently
   keep, since the presenter stated live capability that current
   engineering docs contradict.
3. **KB uploads with pictures (#2)** — `kb_ingest.py::extract_text` still
   only accepts `.pdf`/`.docx`/`.md`/`.txt`; unchanged since the audit.
4. **DMS-API integration (#4)** — unchanged. Note Customer 360 (#15) ships
   *without* it, so it aggregates CRM data only; if Proton expects
   DMS-sourced vehicle data behind that search box, this is the gap.
5. Externally blocked, no engineering action available: inbound email SMTP/IMAP
   credentials (#10) and Meta Business verification (#11).
