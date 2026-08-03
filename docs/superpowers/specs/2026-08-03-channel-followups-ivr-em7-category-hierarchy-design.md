# Channel follow-ups: IVR-4 language nudge, EM-7 two-thread escalation, category hierarchy

**Status:** Approved, ready for planning.
**Source:** brainstormed 2026-08-03, in the same session that closed out the
RSA deploy backlog (`docs/analysis/crm-channel-ui-testing-guide.md`'s three
remaining ❌/⚠️ items from the 2026-07-28 client demo: IVR-4, EM-7, and the
category-hierarchy decision item in §8).

Three independent, small features bundled into one spec because they were
scoped together — each ships behind its own flag/config and can land as its
own commit series. No shared code between them.

---

## 1. IVR-4 — per-turn language reminder

**Problem, with evidence.** Pulled the real 2026-07-28 demo call transcript
(Chatwoot conversation #35 on proton) via `rails runner`. The bot correctly
switched to Bahasa Melayu when the caller committed fully to Bahasa (turn 2),
but stayed in Bahasa on turn 3 when the caller's utterance was code-switched
("kalau Sorry sorry sorry to interrupt, how about the security features?" —
mostly English). The system prompt (`router.py:1254`) already explicitly
instructs "ALWAYS reply in the SAME language the caller uses... switch
immediately if they switch" — so this isn't a missing-instruction bug like
the earlier WA-2 text-channel fix. The model appears to anchor to the
conversation's established language rather than re-evaluating fresh each
turn, a known-plausible native-audio-model limitation with code-switched
speech.

**Approach.** Don't build our own language detector (fragile across
English/Bahasa Melayu/Chinese, and code-switching is exactly the failure
mode). Instead, force the model to re-evaluate every turn by injecting a
short, content-free reminder immediately after each caller utterance. The
`google-genai` Live SDK (confirmed installed, `AsyncSession.send_realtime_input`)
supports a `text` parameter alongside audio — send a fixed reminder string
(not derived from transcript content) right after each `InputTranscript`
event is observed in `bridge.py`'s pump loop, before the model's next
response.

**Scope:**
- `backend/apps/backend/src/chatbot/features/chat/phone/bridge.py` — on each
  `InputTranscript` event, after handling it as today, call a new
  `live.send_language_reminder()` (or equivalent) if the feature flag is on.
- `backend/apps/backend/src/chatbot/features/chat/phone/gemini_live.py` —
  extend `LiveSession` Protocol + `_GeminiLiveSession` with a `send_text_hint(text: str) -> None`
  wrapping `send_realtime_input(text=...)`.
- `backend/apps/backend/src/chatbot/platform/config.py` — new
  `phone_language_nudge_enabled: bool = False`.
- Reminder text: fixed, e.g. `"(Reminder: match your next reply's language to what the caller just said, even mid-conversation.)"`
  — not spoken aloud (it's a text hint, not an audio turn), doesn't count as
  a caller turn.

**Testing.** Unit tests mock the `LiveSession` the same way
`test_live_config.py`/`test_gemini_live.py` already do — assert the hint is
sent after each `InputTranscript` when the flag is on, and never sent when
off (byte-identical to today). Cannot be verified against the real Gemini
Live API without a live call — ship default-off, flip on for proton only,
and re-run the same kind of transcript-pull recon done for this diagnosis to
confirm turn 3's failure mode is gone. If it doesn't help, this is cheap to
turn back off; no architecture is being bet on it.

**Explicitly out of scope:** rewriting the base system prompt further (it's
already about as explicit as prompt text can be); building a real
language-detection library; changing the underlying Gemini Live model.

---

## 2. EM-7 — two-thread email escalation

**Problem.** The SOP (confirmed in the 2026-07-28 client meeting) wants
email-channel escalations to send **two separate email threads**: a
customer-facing acknowledgement, and a separate internal/dealer forward —
not CC/BCC on one thread. Today's `EscalationNotifier._send_email`
(`backend/apps/backend/src/chatbot/features/chat/escalation_notifier.py`)
only sends one internal-facing email to the department PIC (+ CC if
`escalation_cc_pic`); there's no customer-facing email at escalation time at
all (the existing `EMAIL_AUTOACK_ENABLED` ack only fires on new-thread
*creation*, not on escalation).

**Scope, additive to `EscalationNotifier.notify()`:**
1. **Customer acknowledgement** (new) — when the escalation's originating
   conversation is on an Email inbox, send a second, separate email to the
   contact's email address. New config `EMAIL_ESCALATION_ACK_ENABLED`
   (default off) + `EMAIL_ESCALATION_ACK_TEMPLATE` (SOP-default text, e.g.
   "Your case has been escalated to a specialist team who will follow up
   shortly."). Reuses the existing `SmtpEmailSender`.
2. **Dealer forward** (new, additive to the existing PIC email — not a
   replacement) — if the conversation carries a `dealer_<slug>` label
   (already parsed for the Phase-3 reporting `dealer` dimension), forward
   the same internal case detail to that dealer's email. New config
   `DEALER_EMAIL_MAP_JSON` (slug → email, mirrors `PIC_MAP_JSON`'s shape),
   default empty map → no dealer email sent (byte-identical). Both PIC and
   dealer emails fire when both are resolvable — this is deliberate
   (matches how PIC email + WhatsApp already both fire today).
3. Existing PIC email and `case_state=WIP` write are unchanged.

All three sends stay independent best-effort try/except blocks (matches the
existing pattern) — one failing never blocks the others.

**Scope note:** "internal/dealer forward" content reuses the existing
`_send_email`'s body shape (subject, reference, summary) — no new template
needed there, just a new recipient resolution path.

**Testing.** TDD per-branch: ack-enabled + email-channel → ack sent;
ack-enabled + non-email-channel → no ack; dealer label present + map has
entry → dealer email sent alongside PIC; no dealer label or unmapped slug →
dealer send skipped, no error; all flags/maps empty → byte-identical to
current behavior (regression-guard test).

---

## 3. Category hierarchy — cascading `case_category`/`case_subcategory`

**Problem.** PRO-NET asked for main-category → subcategory dependency
(select "Sales" → only Sales subcategories selectable). The real taxonomy
now exists as `case_category`/`case_subcategory` Chatwoot custom conversation
attributes (provisioned live via `chatwoot-config/provision_case_taxonomy.py`,
same shape as `CASE_TAXONOMY_JSON`) — these are the structured fields that
also feed the new BigQuery reporting dimensions, distinct from the flat
`category_*` Labels the bot's auto-categorization (WA-8) applies. Chatwoot's
native custom-attribute editor has no concept of field dependency.

**Scope.** New Chatwoot fork patch. A small Vue component
(`ProtonCaseCategoryFields.vue` or similar) renders in place of the native
`case_category`/`case_subcategory` attribute inputs in the conversation info
panel: a `case_category` select, and a `case_subcategory` select whose
options are filtered client-side to the chosen category's subcategories
(using the same taxonomy data already available — need to expose
`CASE_TAXONOMY_JSON`'s structure to the frontend, likely via a small
read-only endpoint or by embedding it at build/config time — **plan should
confirm the cheapest way to get the taxonomy shape into the SPA**, e.g.
reusing an existing `/kb/*`-style config endpoint vs. a new one). If the
category changes and the currently-selected subcategory is no longer valid
for the new category, it's cleared (not left dangling as an invalid value).

**Scope note:** purely a frontend filtering behavior over data that already
exists — no backend/database change, no new custom attribute definitions.
Native attribute values are unaffected (same `case_category`/`case_subcategory`
keys), so existing reports/BigQuery views are untouched.

**Testing.** Local vite build must succeed (0 errors, matches every prior
fork-patch task); manual smoke (can't be automated — no frontend test
harness in this repo for Vue components) documented as a STILL TODO for the
human tester, same as every other UI patch in this program.

---

## Out of scope for all three

- Any further work on the `IVR-5` DTMF-vs-conversational decision (still
  blocked on Proton's choice).
- Extending category hierarchy to the `category_*` Labels picker (only the
  custom-attribute panel, per the approved design).
- A general-purpose language-detection library (IVR-4 deliberately avoids
  building one).
