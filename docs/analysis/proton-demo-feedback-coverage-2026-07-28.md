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

**Legend:** ✅ Fully covered · ⚠️ Partially covered · ❌ Not covered ·
🔲 Needs a decision/input from Proton, not an engineering gap

---

## Knowledge base / FAQ

| # | What PRO-NET said/asked (≈timestamp) | Status | Evidence |
|---|---|---|---|
| 1 | Bulk CSV upload for FAQ Q&A pairs, instead of one-by-one manual entry (~00:06–08, ~00:45–46) | ❌ | Gap analysis §4 and testing-guide §2 both still say "one-by-one only today" |
| 2 | KB document upload limited to PDF — asked whether uploads could carry pictures (~00:45–47) | ❌ | `backend/apps/backend/.../features/chat/kb_ingest.py::extract_text` only handles `.pdf`/`.docx`/`.md`/`.txt`; anything else raises `UnsupportedFileType`. No image/video ingestion exists |
| 3 | KB document-upload button returned a raw error live ("still failed... 404") (~00:07, ~00:46) | ✅ | Patch `0033` fixed the raw-404 alert; `KNOWLEDGE_PG_ENABLED=true` live on proton since 2026-08-03 — Uploads screen works today |
| 4 | Custom AI tools / DMS-API integration, demoed as mock (~00:08) | ❌ | Same root gap as Customer 360/DMS (gap analysis §6) — no DMS client exists in the repo |
| 5 | PowerBI integration named in the original requirement (~00:10, ~01:20–22) | 🔲 | Testing guide §8 item 9: Proton is to share target report examples so the team can decide embed-vs-native; only native WebBI-style charts exist today |
| 6 | Idle-warning / auto-close threshold configuration (~00:13–15) | ✅ | Demoed live and works; writable per `Settings → Inboxes → Business Hours → Inactivity & auto-close` (testing guide §2). SOP ambiguity on the exact 10 vs 15 min figure remains a separate confirm-with-Proton item (gap analysis §8) |
| 7 | FAQ Assist / suggested-reply rated "not helpful" live; Copilot returned "couldn't find any information" (~00:50–53) | ⚠️ | Gap analysis §4: `/suggest` was rewritten 2026-07-27 (day before) to synthesize the whole thread; the live miss was attributed to that conversation's KB not being grounded yet (a setup step) — no further fix documented since, relevance quality unverified |

## Channels / routing

| # | What PRO-NET said/asked (≈timestamp) | Status | Evidence |
|---|---|---|---|
| 8 | How to see which channel a conversation came from (~00:20–21) | ✅ | Demoed via the channel filter |
| 9 | Confusion over inbox naming ("Proton API" / "Twilio Proton" / "website demo") (~00:21–23) | 🔲 | Demo-environment naming; production inboxes will presumably get clearer names — no code change implied |
| 10 | Live inbound email not wired (~00:23–26) | ❌ | Still blocked on SMTP/IMAP credentials per interaction guide §5 |
| 11 | Facebook/Instagram connection blocked (~00:29) | ❌ | Blocked on Meta Business verification, unchanged per both 2026-08-04 docs |
| 12 | No total summary of all channels in one glance (~00:30–31) | ✅ | Existing Reports overview dashboard covers this |
| 13 | Where can an agent check all pending/follow-up cases (~00:31, presenter uncertain live) | ✅ | Gap analysis §3: `chatwoot-my-tasks` dashboard app + `features/tasks/deadline.py` already implement this — just not confidently demoed |
| 14 | Per-ticket detail dashboard (caller, phone, status) for every case (~00:31–34) | ⚠️ | Conversation/contact panel already shows phone + status; no separate structured "ticket dashboard" record — overlaps the Customer 360 gap |
| 15 | Vehicle-number / phone-number lookup across conversations (Customer 360) (~00:34–35) | ❌ | Gap analysis §6 largest acknowledged gap; matches WA-13. **Raised 3 separate times in this meeting — most-repeated ask** |
| 16 | Which unique customer identifier to use for grouping (CIF-like ID vs phone vs vehicle) (~00:42–44) | 🔲 | Fauzal: "we need to discuss with Rafael and team" — genuinely open design question feeding Customer 360, not resolved anywhere in docs |
| 17 | Email escalation as two separate emails (customer ack + internal/dealer forward), no CC/BCC (~00:35–37, ~01:30–32) | ⚠️ | Built and deployed 2026-08-04 (EM-7); customer-ack leg live, but PIC/dealer internal legs need a dept→PIC and dealer→email mapping filled in (ops/config task, not code) before they notify anyone |
| 18 | Who hosts/owns the email service — Devoteam-provided or Proton's own SMTP? (~00:38–39) | 🔲 | Fauzal confirmed Devoteam can host; explicit ask for Proton to supply a subdomain + credentials (testing guide §8 item 1) |
| 19 | Is there a count of cases created/closed entirely by AI, no human? (~00:38–42, presenter uncertain live) | ✅ | `features/metrics/bigquery_schema.py` has a `resolved_by` field with `closed_by_bot`/`transfer_to_agent` counts already in the schema — just not confidently demoed |

## Agent management / assignment

| # | What PRO-NET said/asked (≈timestamp) | Status | Evidence |
|---|---|---|---|
| 20 | Is there a configurable cap on how many tickets round-robin to one agent? (~00:59–01:00) | ❌ | `features/routing/service.py::RoutingService.pick_agent` and `store.py`/`assigner.py` have no max-concurrent/cap logic at all |
| 21 | Auto-set agent status to "busy" during a phone call, so WhatsApp doesn't also route to them (~01:01–07) | ❌ | `features/routing/presence.py` only *reads* Chatwoot presence; nothing in `phone/bridge.py` writes back an availability/busy status when a call starts |

## Phone / IVR

| # | What PRO-NET said/asked (≈timestamp) | Status | Evidence |
|---|---|---|---|
| 22 | DTMF ("press 1") vs. conversational LLM routing — which ships? (~01:07–09) | 🔲 | Explicitly flagged as an open choice in the meeting; matches IVR-5, still unresolved as of 2026-08-04 |
| 23 | Hand-off to a live human agent on a call (~01:11–13) | ❌ | Confirmed mocked only, not a real transfer — matches IVR-6, unchanged |
| 24 | RSA (accident/road-side) after-hours routing to the 24/7 line (~01:14–15) | ✅ | Business-hours-aware transfer logic confirmed live in the orchestrator. (Separate RSA incident-log *page* is code-complete but not deployed on every tenant yet — a deploy step, not a gap in this specific ask) |
| 25 | WhatsApp voice notes — text-only during the demo (~01:15–16) | ⚠️ | `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED=true` now live on proton (2026-08-03) but unconfirmed against a real WhatsApp number |
| 26 | Customer-sent videos (e.g. of the car) should be processable on website/WhatsApp (~01:16–18) | ❌ | **Discrepancy worth flagging to Proton.** Presenter said live "the functionality is there... we can process the video" — but both 2026-08-04 docs explicitly state video understanding is "genuinely unbuilt (audio/image only), out of scope by design." What was told to the client contradicts current engineering status |
| 27 | Call recording for QA/compliance (~01:18) | ❌ | Confirmed nothing recorded in the demo build; matches IVR-8, still unimplemented |

## Reporting / RBAC

| # | What PRO-NET said/asked (≈timestamp) | Status | Evidence |
|---|---|---|---|
| 28 | Separate report views for admin vs. team leader vs. agent (~01:18–19, presenter uncertain live) | ⚠️ | Patch `0028-chatwoot-access-permissions.patch` (commit `5991893`, 2026-08-02 — *after* the demo) added a "Chatwoot access" section to Roles & Permissions with a `Reports` checkbox, gated behind `RBAC_ENABLED`. Built, but post-dates the demo and hasn't been shown to the client |
| 29 | Proton to share reference report/visualization examples (~01:22–24, ~01:35–37) | 🔲 | Same open item as #5, tracked in testing guide §8 item 9 |
| 30 | Category → subcategory cascading dropdown, main category unique / subcategory dependent (~01:23–30) | ⚠️ | Fork patch for cascading `case_category`→`case_subcategory` built and deployed to proton 2026-08-03/04 (commit `6d08afb`), still needs a manual browser confirmation — not yet verified live |
| 31 | General "some adjustment to the UI, some more on the UX" (~01:35–37) | 🔲 | Vague, no specifics given — flag for a follow-up conversation |

---

## Summary

| Status | Count |
|---|---|
| ✅ Fully covered | 7 |
| ⚠️ Partially covered | 6 |
| ❌ Not covered | 11 |
| 🔲 Needs Proton's input | 7 |
| **Total** | **31** |

### ❌ items worth prioritizing next

1. **Customer 360 vehicle/phone lookup (#15)** — the single most-repeated
   client ask across the whole meeting (raised 3 separate times), and
   already the platform's biggest acknowledged gap.
2. **Round-robin ticket cap per agent (#20)** and **auto-busy status during
   calls (#21)** — both concrete, code-verifiable gaps in the routing
   engine that block the exact "agent overload" scenario PRO-NET walked
   through live; neither needs an external dependency to build.
3. **Real IVR human hand-off (#23)** — currently fully mocked; customers
   will notice immediately in a real deployment, and it also blocks call
   recording (#27) and any real RSA hand-off value.
4. **Video-understanding discrepancy (#26)** — needs clarifying with Proton
   before it becomes a support commitment the platform can't currently
   keep, since the presenter stated live capability that current
   engineering docs contradict.
