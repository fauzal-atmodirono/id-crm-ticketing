# Proton Demo Run Sheet — 2026-08-06

One page to hold during the call. Ordered so the items Proton cared most
about land first, while attention is highest and the connection is freshest.

Status source: `proton-demo-feedback-coverage-2026-07-28.md` (refreshed
2026-08-06). Item numbers below (#n) refer to that document.

---

## 0. Pre-flight — do this before joining

| # | Check | Why it matters |
|---|---|---|
| 1 | **Set the CRM to the light theme** (profile → change appearance → Light) | They asked for this twice on 07-28 and lost minutes to it |
| 2 | **Populate Escalation Routing** — at least one department→PIC and one dealer→email entry, saved, then refresh to confirm they persisted | Empty mappings make EM-7 — the thing they called "the most important" — demo as a no-op |
| 3 | Confirm the sidebar shows **Cases**, **Customer 360**, **Escalation Routing** on the account you'll present from | All three are permission-gated; if your role lacks them they silently don't render |
| 4 | Confirm flags on the proton tenant: `RBAC_ENABLED`, `DMS_MOCK_CLIENT_ENABLED`, `WHATSAPP_MEDIA_UNDERSTANDING_ENABLED` | Each one is the difference between a working demo and a blank panel |
| 5 | If demoing telephony: `phone_handoff_enabled` + `phone_handoff_target_number` + **`phone_handoff_caller_id`** | Without the caller ID the handoff deliberately refuses to dial rather than dropping the call — it will look broken |
| 6 | Dry-run **one** Customer 360 search with a real phone number and a real vehicle number | Vehicle lookup is approximate; find out now, not on the call |
| 7 | Wired connection / backup tethering | You dropped four times on 07-28 |

**Nothing in section 2 has ever been clicked through in a browser.** Do a
silent dry run of at least the top three before presenting them.

---

## 1. Open by closing last meeting's loops (≈10 min)

These are the two questions you couldn't answer live. Lead with them — it
signals the feedback was taken seriously.

**"Where can I follow up all my pending cases?"** → **Cases** (sidebar)
1. Filters across the top: Division, Case type, Status, Channel, Dealer.
2. Columns: Case ID, Division, Concern, Purchased From, Escalated To, Car
   Plate, **Aging (days)**, Status.
3. Filter to Aftersales + Complaint, sort by Aging — "every open aftersales
   complaint across all dealers, oldest first."
4. Click a Case ID to open the underlying conversation.

> If a banner appears saying it's showing the first N of a larger total, say
> so plainly — past that limit the filters and totals no longer describe the
> whole account.

**"Is there a per-ticket dashboard — who the caller is, phone, status?"** →
the Cases row *is* that record, and **Customer 360** is the per-customer
rollup. Be straight that the CRM does not keep a case object separate from
the conversation, and ask whether they want one. That's a genuine open
design question (#14), not something to paper over.

---

## 2. The five they asked for by name (≈30 min)

### Email escalation, EM-7 (#17) — *"this is the most important"*
Sidebar → **Escalation Routing** (needs `escalation.manage`). Show the
department→PIC and dealer→email CRUD, then trigger an escalation on an email
inbox and show **both** legs: the customer acknowledgement and the separate
internal/dealer forward, with no CC/BCC linking them.

> Honest caveat: inbound email is still blocked on their SMTP/IMAP
> credentials (#10). Hand them
> `docs/analysis/2026-08-05-email-channel-questions-for-proton.md` on this
> call — it's drafted and still unsent.

### Customer 360 (#15)
Sidebar → **Customer 360**. Search a phone number, then a vehicle number.
Contact → Conversations across all channels → RSA incidents → DMS block.

> Say the asymmetry out loud before they find it: phone search is an **exact**
> contact match; vehicle search matches conversations whose noted vehicle
> model contains that value, because Chatwoot has no true vehicle-number
> field. The two modes can return slightly different conversation sets for
> the same customer.
>
> Their open question from 07-28 is still open: **which identifier is the
> master key** — CIF-style ID, phone, or vehicle (#16)? Ask for a decision.

### Case category hierarchy (#30)
Conversation sidebar → pick a main category, show the subcategory list narrow
to that category's children. This is exactly what they described and
explicitly wanted instead of flat multi-label.

### Reports (#29, #5)
**Reports → Weekly Report** — this was built against *their own*
`Weekly Report Proton e.MAS.pptx`. Week picker, Case Volume + WoW change,
Case Status Trend, Departments & PIC detail, Call Centre & SLA, WIP/Aging,
Dealer Escalation Turnaround, Per-Case Detail.

Then **Reports → Departments & PIC** for the sales-vs-aftersales breakdown
they asked for, including Case Reopen Rate and Category × Vehicle Model.

> Pre-empt one question: Per-Case Detail reads live conversations while Case
> Volume reads the reporting warehouse, so the counts can differ slightly.
> That's two data sources, not a bug — say it before they spot it.
>
> PowerBI embed vs. native charts is still **their** decision (#5).

### FAQ bulk upload (#1)
Knowledge → FAQs → **Bulk upload (CSV)**, columns
`question,answer,keywords,tags`. Directly answers *"we don't like to go live
item by line item."* Then re-run the query that returned "couldn't find any
information" on 07-28, now that the KB is grounded (#7).

---

## 3. Only if there's time and the connection holds

- **WhatsApp media (#25, #26)** — voice note *and* **video**. Video
  understanding is genuinely built end-to-end; what you told them on 07-28
  was correct. Keep the clip short; anything over ~14 MB is dropped by the
  media budget.
- **Telephony (#23, #27)** — conversational LLM call, handoff to a human,
  call recording. Highest risk items on the sheet: never tested against a
  real Twilio number. Skip rather than fumble.
- **DMS/TSP (#4)** — Settings → Integrations → DMS/TSP, connection test, then
  the vehicle/service block in Customer 360. **Point at the "Mock data" badge
  yourself.** Letting them assume it's live data is the one mistake that
  costs trust later.

---

## 4. Answers to have ready

| If they ask | Say |
|---|---|
| Agent status "lunch"/"toilet break" (#32) | Not built. Routing honours Chatwoot's native online/busy/offline today; operator-defined labels don't exist yet |
| Auto-busy when an agent is on a call (#21) | Not built. Presence is read-only today. It's the top remaining routing gap and shares a boundary with the item above — we'd build them together |
| Upload images to the knowledge base (#2) | A PDF or DOCX **containing** pictures uploads fine — text extracted, images ignored. A bare `.jpg`/`.png` as a knowledge source is not supported |
| Round-robin ticket cap (#20) | Built, ships at `0` = unlimited. Needs a number from them |
| Facebook / Instagram (#11) | Blocked on Meta Business verification — their side |
| IVR press-1 vs conversational (#22) | Still their decision. Both paths exist; conversational is what we demo |
| Report visibility by role (#28) | Built behind `RBAC_ENABLED`; Reports is a checkbox per role |
| Why did the AI answer English to a Bahasa message? | Prompt-side language handling, tunable in the assistant persona — offer to fix it in the prototype session |

---

## 5. Leave-behinds

- `docs/client-materials/PROTON - CRM Feature Guide.docx` — 13 chapters,
  "Where to find it" / "How to use it" per feature, 42 live screenshots.
- `docs/analysis/2026-08-05-email-channel-questions-for-proton.md` — unsent.
- `docs/analysis/2026-08-05-dms-tsp-integration-questions-for-proton.md` — unsent.

## 6. Decisions to walk away with

1. Master customer identifier: CIF-style ID, phone, or vehicle number? (#16)
2. Email: subdomain + SMTP/IMAP credentials, and who hosts (#10, #18)
3. IVR press-1/2 vs. pure conversational (#22)
4. PowerBI embedded, or native reports only? (#5)
5. Concurrent-ticket cap per agent — what number? (#20)
6. Do they want a case record distinct from the conversation? (#14)
