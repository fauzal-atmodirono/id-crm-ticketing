# Phone Channel — Package C Manual Verification Runbook

> **Nothing in this document has been executed.** Package C (transcript-at-
> call-start / live transcript, post-call classification, call recording,
> and real human hand-off) shipped across six tasks, commits `5c2659f` …
> `a19eaff` on `dev-yuda`, entirely against the unit-test suite and hand-
> written fakes — **no real Twilio call has ever hit this code.** Unit
> tests cannot catch a mis-built TwiML verb, a real Twilio error code
> (e.g. 13214 on a bad caller id), or actual Gemini Live/latency behaviour.
> Every scenario below is a prediction of what the code should do, derived
> from reading it — not a report of what it did. Treat every "Expected"
> block as a hypothesis to falsify, not a known-good result.
>
> This is Task 7 of
> `.superpowers/sdd/2026-08-04-pkg-c-telephony-handoff-transcript-recording/`.
> It exists because Task 7 cannot be run in the environment that authored
> it — no Twilio number, no phone, no deployed tenant. Whoever executes
> this runbook should tick each checkbox as they personally verify it, not
> as they read the code and agree it looks right.

**Relationship to the other phone smoke test:** `backend/docs/testing/
phone-channel-smoke-test.md` predates Package C and a Chatwoot-based CRM
entirely — it documents `apps/backend`'s original Zendesk-only POC
(`CRM_PROVIDER=zendesk`), before the recording/classification/handoff/
live-transcript features below existed. Its Scenarios A–C (greeting
latency, KB grounding, barge-in) still exercise real, unchanged bridge
behaviour and are worth running first if the basic audio path itself has
never been proven on this deployment. Its Scenarios D–F (Zendesk ticket
shape, CSAT) are **stale** — the shipped default is `CRM_PROVIDER=chatwoot`
and none of Package C's Chatwoot-specific attributes/labels are covered
there. This document is the one to follow for anything Chatwoot- or
Package C-specific; a pointer back to this doc has been added at the top
of that file.

---

## 0. Feature flags this package added

All ten settings live in `backend/apps/backend/src/chatbot/platform/
config.py` (`Settings` class) and `backend/apps/backend/.env.example`.
**Every one of them defaults to today's (pre-Package-C) behaviour** — the
system is byte-identical with all of them off/empty. Verified by reading
`config.py` lines 293–376 directly (each has an inline comment naming the
task that added it) and cross-checked against `.env.example` lines
226–288.

| Setting (env var) | Type / default | What turning it on does |
|---|---|---|
| `phone_transcript_live_enabled` (`PHONE_TRANSCRIPT_LIVE_ENABLED`) | bool / `false` | **Also controls ticket-at-call-start**, not just live streaming — both are gated by this one flag (`bridge.py:186`). On: the Chatwoot conversation is created the moment the Twilio "start" event arrives, and completed transcript turns stream into it every `phone_transcript_flush_seconds`-ish. Off: the conversation is created only in `finalize()`, from the complete transcript, exactly as before this package. |
| `phone_transcript_flush_seconds` (`PHONE_TRANSCRIPT_FLUSH_SECONDS`) | float / `15.0` | Soft interval `TranscriptSink` uses to decide a block is "due" to post. Only has any effect when `phone_transcript_live_enabled=true`. It's polled, not a real timer — see §4 caveats. |
| `phone_transcript_classification_enabled` (`PHONE_TRANSCRIPT_CLASSIFICATION_ENABLED`) | bool / `false` | On: `finalize()` runs a one-shot Gemini call on the completed transcript to derive `case_type`/`division`/`concern`/`status`, writes them as Chatwoot custom attributes + a `division_<slug>` label, and can flip the closing status from `solved` to `open` if the model reads the call as unresolved. Off: status stays the exact "open if handoff else solved" binary rule, no classification attributes written. Runs only in `finalize()`, never in the live audio path. |
| `phone_recording_enabled` (`PHONE_RECORDING_ENABLED`) | bool / `false` | On: starts a dual-channel Twilio recording on the live call and, once Twilio's `/webhooks/phone/recording-status` reports `completed`, stores `recording_sid`/`recording_duration`/`recording_url` as **internal-only** Chatwoot custom attributes (never a customer/agent-visible comment). Requires `phone_recording_announcement` and `twilio_webhook_base_url` — see §1. |
| `phone_recording_announcement` (`PHONE_RECORDING_ANNOUNCEMENT`) | str / `""` | The PDPA (Malaysia) recorded-line notice text, operator-authored, bilingual free text. Required for recording to actually start — see §1. |
| `phone_recording_retention_days` (`PHONE_RECORDING_RETENTION_DAYS`) | int / `90` | **Informational only.** No automated deletion job reads this setting today. Recorded so the retention *policy* is operator-visible from day one, not enforced. |
| `phone_handoff_enabled` (`PHONE_HANDOFF_ENABLED`) | bool / `false` | On: `request_human_handoff` actually redirects the live call into a real `<Dial>` to `phone_handoff_target_number`, gated by business hours on the tenant's default Chatwoot inbox. Off (default): the tool call keeps returning `{"status": "ticket_created"}` exactly as before this package — no real transfer is ever attempted. |
| `phone_handoff_target_number` (`PHONE_HANDOFF_TARGET_NUMBER`) | str / `""` | The static hunt-group E.164 number to dial. Empty = handoff resolves to "do not attempt", identical to the flag being off. |
| `phone_handoff_timeout_seconds` (`PHONE_HANDOFF_TIMEOUT_SECONDS`) | int / `30` | Twilio `<Dial timeout>` — how long it rings the target before posting `DialCallStatus=no-answer` to `/webhooks/phone/dial-status`. |
| `phone_handoff_caller_id` (`PHONE_HANDOFF_CALLER_ID`) | str / `""` | **Required for handoff to dial at all.** See §1 — without it, `HandoffTargetResolver.resolve()` deliberately refuses to resolve a target rather than dial with a broken caller id. |

**Enable order** (each layer only matters once the one below it works):

1. `twilio_webhook_base_url` and `twilio_auth_token` (pre-existing, not
   new to this package, but load-bearing for everything below — §1).
2. `phone_transcript_live_enabled` — verify the ticket-at-start + live
   transcript behaviour alone first (§2 Scenarios 1–2) before turning
   anything else on, since classification/recording/handoff all build on
   the ticket existing.
3. `phone_transcript_classification_enabled` — §2 Scenario 3.
4. `phone_recording_enabled` + `phone_recording_announcement` together
   (never one without the other) — §2 Scenario 4.
5. `phone_handoff_enabled` + `phone_handoff_target_number` +
   `phone_handoff_caller_id` together — §2 Scenarios 5–7.

---

## 1. Prerequisites (the ones easy to miss)

- [ ] **`twilio_webhook_base_url` is set to a real public HTTPS base.**
      Two different things silently degrade without it, in two different
      directions:
      - Recording: `PhoneBridge._maybe_start_recording` fails **closed**
        (refuses to start recording at all, logged
        `phone_recording_no_callback_base_configured`) — this is the one
        deliberate exception to this package's fail-*open* rule, because
        an untracked/orphaned recording with no way to attach it to a
        ticket is worse than no recording.
      - The PDPA `<Say>` announcement in the initial TwiML response
        (`router.py::phone_incoming`) is **only emitted** when
        `phone_recording_enabled AND phone_recording_announcement AND
        twilio_webhook_base_url` are ALL set — miss any one of the three
        and the caller is never told the call is recorded, even if
        recording is otherwise configured correctly.
- [ ] **`twilio_auth_token` is set.** Both `/webhooks/phone/recording-
      status` and `/webhooks/phone/dial-status` verify Twilio's
      `X-Twilio-Signature` and **refuse (401), not skip**, when this is
      empty (`phone_recording_status_no_auth_token_configured` /
      `phone_dial_status_no_auth_token_configured`). Without it, a real
      recording will never attach and a real transfer's outcome will
      never be recorded, even though the call itself proceeds normally.
- [ ] **`phone_handoff_caller_id` is set before enabling `phone_handoff_
      enabled`.** The only wired inbound path in this repo is the
      browser softphone (Twilio Voice JS SDK → TwiML App), whose parent
      leg's `From` is `client:<identity>` (the identity minted in
      `router.py::phone_token`, hard-coded as `"proton-web-caller"`).
      Twilio rejects `client:...` as a caller id for a PSTN `<Number>`
      dial (error 13214) — a TwiML error that would otherwise **drop the
      call** mid-transfer, right after the bot has already told the
      caller it's connecting them. `HandoffTargetResolver.resolve()`
      refuses to resolve a target at all when this is unset
      (`phone_handoff_no_caller_id_configured`), so the practical
      symptom of forgetting this setting is NOT a dropped call — it's
      every handoff silently falling back to "ticket_created" as if the
      feature were off. Set it to a Twilio-verified number (your Twilio
      DID, or a number verified in the Twilio console).
- [ ] **`phone_recording_announcement` and `phone_recording_enabled` are
      set/unset together.** One without the other means either "recording
      never starts because no announcement is configured" (flag on, text
      empty) or a syntactically-valid but pointless TwiML `<Say>` (there
      isn't one — the announcement param is `None` unless recording is
      also enabled, so this direction can't actually happen; only the
      first direction is a real trap).
- [ ] **The Chatwoot inbox's business hours are configured** (or
      deliberately left unconfigured) before testing handoff.
      `HandoffTargetResolver._within_business_hours` fails **open**
      (returns `True`, will attempt the dial) whenever
      `chatwoot_inbox_id` is unset or the hours lookup fails — so an
      out-of-hours test that expects "no transfer attempted" will
      surprise you if the inbox has no working-hours configured at all.
- [ ] **RBAC**: to verify the recording-gating scenario (§2 Scenario 6),
      have one Chatwoot login WITH the `call_recording.listen` permission
      and one WITHOUT it (`features/authz/seed.py`).

---

## 2. Per-scenario steps and expected observations

Each scenario assumes the flags listed in its heading are on, everything
else in §0's table off, unless stated otherwise. Run them roughly in the
order below — later scenarios depend on earlier ones passing.

### Scenario 1 — Ticket exists at call start (`phone_transcript_live_enabled=true`)

1. [ ] Place a call (browser softphone or a real PSTN number pointed at
       the TwiML app's Voice URL, `POST /voice/phone/incoming`).
2. [ ] **Immediately** after the greeting starts — before saying
       anything — check Chatwoot for a new conversation with external id
       `phone-<CallSid>`.

**Expected:** the conversation already exists at this point, with an
empty or near-empty body — not just at hangup. If it's missing, check
the log for `phone_ticket_create_failed` (see §5).

### Scenario 2 — Live transcript arrives mid-call (`phone_transcript_live_enabled=true`)

1. [ ] Continue the call from Scenario 1. Say several distinct things,
       pausing between them, for at least `phone_transcript_flush_seconds`
       (default 15s) of total call time.
2. [ ] **While still on the call**, refresh the Chatwoot conversation
       from Scenario 1 in a second browser tab.

**Expected:** new USER/ASSISTANT turns append to the conversation as the
call progresses — not only after hangup. This is the thing a subtle bug
most easily fakes: a transcript that looks complete and correct once the
call ends but only actually posted at the very end (see §4, first row).
Confirm you can see it grow **before hanging up**, not just check its
final state afterward.

### Scenario 3 — Classification lands on the ticket (`phone_transcript_classification_enabled=true`, plus Scenario 1's flag)

1. [ ] Place a call and describe a concrete, classifiable issue (e.g. an
       obvious warranty question, or an obvious complaint).
2. [ ] Let the call end normally (no handoff).
3. [ ] Open the resulting Chatwoot conversation's custom attributes.

**Expected:**
- [ ] `case_type` and `concern` custom attributes are populated with
      something resembling what was said.
- [ ] `division` (UI display spelling, e.g. "After Sales") **and**
      `case_category` (canonical classifier spelling, e.g. "Aftersales" —
      one of `{Apps, Sales, Aftersales, Charging, Product, Marketing,
      Others}`, `features/metrics/mapping.py::CATEGORY_TO_DIVISION`'s
      values) are BOTH set — see §4, third row, for why checking only one
      is not enough.
- [ ] A `division_<slug>` label (e.g. `division_aftersales`, lower-cased
      and space-to-underscore normalized) is present on the conversation.
- [ ] Backend log shows `phone_transcript_classified` with the derived
      keys.
- [ ] If the call sounded genuinely unresolved to a reasonable person,
      confirm the ticket's final status is `open`, not `solved` — the
      classifier can flip the default binary rule.

### Scenario 4 — Recording attaches after hangup (`phone_recording_enabled=true`, `phone_recording_announcement` set)

1. [ ] Place a call. **Listen for the recorded-line notice** — it should
       be spoken via TwiML `<Say>` before the call connects to the AI at
       all (i.e., before you even hear the greeting), not sometime after.
2. [ ] Have a short conversation, then hang up.
3. [ ] Wait — Twilio posts the recording-status callback asynchronously,
       sometimes tens of seconds after hangup.
4. [ ] Refresh the conversation from Scenario 1/3 (same call, same
       ticket).

**Expected:**
- [ ] `recording_sid`, `recording_duration`, `recording_url` custom
      attributes appear on the SAME conversation the transcript is in —
      not a second, empty conversation (see §4, second row).
- [ ] Every attribute/label written earlier in the call (classification,
      `external_id`, any labels) is **still present** — the recording
      write must merge, not clobber (see §4, fourth row).
- [ ] Backend log shows `phone_recording_started` at call time and
      `phone_recording_status_ignored` (for the `in-progress` callback,
      no URL yet) followed eventually by a successful write (no
      `phone_recording_status_write_failed`).
- [ ] `recording_url` is **not readable via a normal Chatwoot login**
      without `call_recording.listen`, and **is** readable with it — this
      is the whole reason the recording data is a custom attribute, not
      a comment. Verify both directions with the two logins from §1.

### Scenario 5 — Successful transfer to a human (`phone_handoff_enabled=true` + target number + caller id, during business hours)

1. [ ] Have a person standing by at `phone_handoff_target_number`.
2. [ ] Place a call and say something that should trigger a handoff
       (e.g. "I need to speak to a human" or describe a complaint).
3. [ ] Listen for the AI to say a handover line — something like
       *"Let me try to get a specialist for you now — if I can't connect
       you right away, they'll call you back soon"* — **before** the
       transfer happens, not after (see §4, first bullet under
       limitations: the tool response can race the WebSocket teardown,
       so anything queued to be said AFTER the tool call may never
       reach the caller).
4. [ ] Have the standby person answer.

**Expected:**
- [ ] Audio connects both ways between the caller and the human.
- [ ] The Chatwoot conversation was already flipped to `open` with a
      `[Handoff to human agent]` note (reason + summary) **the moment
      the transfer was dialled** — check this while the transfer call is
      still in progress, not after everyone hangs up.
- [ ] After both parties hang up, backend log shows
      `phone_dial_status_completed`. No further CRM write happens for
      "completed" — that's by design, not a bug (see design note in
      `router.py::phone_dial_status_webhook`'s docstring): nothing more
      is knowable about how the human resolved it from the phone side.

### Scenario 6 — Unanswered handoff (`phone_handoff_enabled=true`, target number that will not answer)

1. [ ] Place a call, trigger a handoff (as in Scenario 5), but do not
       answer at `phone_handoff_target_number` — let it ring past
       `phone_handoff_timeout_seconds` (default 30s).
2. [ ] Listen to what the caller hears once Twilio gives up.

**Expected:**
- [ ] The caller hears, in English then Bahasa Melayu (each in the
      correct TTS voice — Google.en-US-Standard-C / Google.ms-MY-
      Standard-A, not the default English-only Polly voice): *"We're
      sorry, none of our agents are available to take your call right
      now. We've noted your call and someone will call you back soon.
      Goodbye"* / the Malay equivalent, then the call **hangs up**.
- [ ] It does **not** return the caller to the AI bot — this is an
      accepted limitation (§3), not a bug to chase.
- [ ] The Chatwoot conversation carries an `unanswered_handoff` tag and
      an internal `[Handoff unanswered -- no-answer]` (or `busy`/
      `failed`) comment, status `open`.
- [ ] Backend log shows `phone_dial_status_unanswered` with
      `status=no-answer` (or `busy`/`failed`).
- [ ] **Nothing actually schedules a callback.** The apology promises
      one; only a human working the `unanswered_handoff` tag in Chatwoot
      makes that true. Confirm you understand this before treating the
      apology's wording as a functional guarantee.

Repeat this scenario for `busy` (target number busy-signals or rejects)
and `failed` (an invalid/unreachable target number) if you can force
those conditions — Twilio's `DialCallStatus` distinguishes them, and the
code branches identically for all three (`no-answer`/`busy`/`failed`, and
in fact anything other than `completed`, e.g. Twilio's `canceled`) —
same apology, same tag, same log event pattern, just a different `status=`
value in the log line and comment text. `completed` (Scenario 5) is the
only outcome that does NOT trigger this path.

### Scenario 7 — Handoff refuses to dial without a caller id (`phone_handoff_enabled=true`, `phone_handoff_caller_id` left empty)

1. [ ] With `phone_handoff_caller_id` deliberately blank, trigger a
       handoff.

**Expected:** no real transfer is attempted — the call continues with
the AI exactly as if `phone_handoff_enabled` were off, the tool response
is `{"status": "ticket_created"}`, and the AI says the "a specialist has
the details and will call them back" line instead of transferring.
Backend log shows `phone_handoff_no_caller_id_configured`. This is the
correct, safe behaviour — the alternative (dialling with no caller id)
is a dropped call, not a graceful fallback. Confirm you see the log line
and NOT a dropped call.

---

## 3. Accepted limitations to know before judging any result above

These are documented, deliberate trade-offs recorded in the six task
reports and the code's own docstrings — not bugs to file:

- **The PDPA announcement's sequencing is now provable, its accuracy is
  not.** The TwiML `<Say>` genuinely runs before `<Connect><Stream>` (and
  therefore before recording can start) — this is a real fix from a real
  review finding, not aspirational. But it plays through Twilio's default
  TTS voice with no `language=`/`voice=` attributes (a review-noted
  minor left unfixed — operator text is free-form prose, not split into
  language segments the way the hard-coded unanswered-handoff apology
  is), so a bilingual operator announcement may not be pronounced
  correctly in its Bahasa Melayu portion. The separate in-session text
  hint to the Gemini model (asking it to *say* the notice) is best-effort
  only and not guaranteed to be spoken verbatim, or at all, if the caller
  talks over it.
- **An unanswered/busy/failed handoff hangs up, it does not return the
  caller to the bot.** This is the brief's design as written (Task 6),
  flagged as worth revisiting with the client, not a defect against what
  was asked for.
- **The apology promises a callback that nothing schedules.** There is
  no automated reminder, task, or queue entry — only the
  `unanswered_handoff` tag, which a human must notice and act on.
- **Recording retention is policy, not enforcement.**
  `phone_recording_retention_days` (default 90) is operator-visible but
  no deletion job reads it. A recording persists indefinitely until
  someone builds that job.
- **A transfer's "transferring" status is not a reliable spoken cue.**
  Redirecting the call tears down the Media Stream (and this WebSocket)
  as soon as Twilio accepts it, which can race the tool response
  actually reaching the live model — so the system prompt tells the
  model to say its handover line *before* calling the tool, never after.
  If you hear the caller's sentence cut off mid-word followed by
  ringback with no "putting you through", that is the known race, not
  necessarily a new bug — but note it, since it means the line was
  queued too late in that particular case.
- **Classification is best-effort and structurally cannot override a
  human handoff.** `finalize()` only reaches the classifier's status
  decision when `self.handoff is None`; an explicit handoff-derived
  `open` status can never be overwritten by a classifier reading.
- **Auto-busy status during calls is not built** (feature #21 in the
  demo-feedback coverage doc) — an agent taking a phone call is not
  marked busy for WhatsApp routing purposes. Explicitly out of scope for
  this package (blocked on an open per-agent-numbers decision).

---

## 4. How to tell a subtle failure from success

The package's own reviews caught each of these; they look correct from
the outside if you only check the end state.

| What it looks like from the outside | Why it's actually a failure | The concrete check |
|---|---|---|
| The Chatwoot conversation has a complete, correct-looking transcript after the call ends. | It may have posted **only in `finalize()`, all at once** — i.e., `phone_transcript_live_enabled` isn't actually doing anything live, or live posting silently failed and finalize()'s fallback (whole-transcript re-post) papered over it. | Refresh the conversation **during** the call (Scenario 2) and watch it grow turn by turn, not just check the final state after hangup. |
| A recording (or classification, or `external_id`) attribute appears on *a* conversation. | The recording-status callback resolves the ticket by session id independently of the live `PhoneBridge` instance (it can fire on a different process, after a restart). If resolution goes wrong, it can write to — or create — the **wrong** conversation, leaving the caller's real one empty. | Confirm it's the *same* conversation you were watching grow live in Scenario 2/3, not a second, otherwise-empty one. Search Chatwoot for `phone-<CallSid>` and check there is exactly one match. |
| The `division` custom attribute shows the right value in the conversation sidebar. | `division` alone is a **UI-only** field the Cases List reads; the BigQuery reporting pipeline (`features/metrics/mapping.py`) reads `case_category` (canonical spelling) or a `division_<slug>` label instead — never `division` itself. A call could look correctly categorized in the UI and still be invisible to reporting. | Check `case_category` **and** the `division_<slug>` label are both present, not just `division`. |
| A label you set earlier (PIC/escalation/dealer routing, or a classification label) is gone after the call ends. | Chatwoot's labels endpoint **replaces** the whole set on any write; a naive single-element write anywhere in the pipeline silently wipes every other label. This was a real, three-times-repeated defect this package's reviews caught and fixed with a GET-then-union helper — but it's the shape of bug that recurs if a future change bypasses that helper. | After a recording/classification/CSAT write lands, check that labels set **earlier** in the same call (or by a prior conversation state) are still all present, not just the newest one. |
| A `phone_dial_status_unanswered` log line appears with no corresponding Chatwoot update. | A redelivered Twilio callback is supposed to be idempotent (gated on the `unanswered_handoff` tag already being present) — but a Chatwoot read failure during the tag check fails to "not yet handled," so a genuine outage window could double-post rather than silently drop. Absence of any update at all, though, means the ticket resolution itself failed. | Check for `phone_dial_status_ticket_update_failed` or `phone_dial_status_unknown_call` in the log alongside the unanswered event — either explains a missing CRM update; neither present with no update is worth escalating. |

---

## 5. What to do when something fails — log events to grep for

All events are `structlog` calls (`_log.info/warning/error(...)`), so
grep the backend's JSON/console logs for the event name (first
positional arg). Names below were read directly out of `bridge.py`,
`router.py`, `handoff_target.py`, `call_control.py`, and
`transcript_classifier.py` — not guessed.

**Ticket / transcript (`phone/bridge.py`):**
- `phone_ticket_create_failed` — call-start ticket create failed (or
  Chatwoot fail-open sentinel detected). Not raised when Chatwoot is
  deliberately disabled — see the next line for that case.
- `phone_ticket_create_skipped_chatwoot_disabled` — expected, quiet, not
  a failure: `chatwoot_enabled=False`.
- `phone_transcript_flush_no_ticket` / `phone_transcript_flush_failed` —
  a live transcript block failed to post (no ticket, exception, or a
  non-OK `ConversationLogResult`).
- `phone_finalize_failed` — the closing write in `finalize()` failed;
  logged with the **full transcript body** attached so the call is
  recoverable from logs alone even if Chatwoot never got it.
- `phone_finalize_ticket_task_failed` / `phone_finalize_recording_task_failed`
  / `phone_finalize_flush_drain_failed` — one of the bounded awaits in
  `finalize()` (ticket-create settle, recording-start settle, flush
  drain) hit its timeout or raised.

**Classification (`phone/bridge.py`, `phone/transcript_classifier.py`):**
- `phone_transcript_classify_client_init_failed` — couldn't build a
  genai client (bad Vertex/API-key config).
- `phone_transcript_classify_bounded_call_failed` — the classify call
  itself timed out or raised.
- `phone_transcript_classify_failed` / `_not_a_dict` /
  `_invalid_case_type` / `_invalid_division` / `_invalid_status` — the
  model's response was malformed or failed schema validation.
- `phone_transcript_classify_write_failed` — classification succeeded
  but writing the resulting custom attributes/label back to Chatwoot
  failed.
- `phone_transcript_classified` (info) — success; logs which keys were
  derived.

**Recording (`phone/bridge.py`, `phone/call_control.py`, `router.py`):**
- `phone_recording_no_announcement_configured` /
  `phone_recording_no_callback_base_configured` — recording refused to
  start (fail-closed, by design — see §1).
- `phone_recording_announcement_hint_failed` — queuing the in-session
  text hint to Gemini failed (recording is also refused in this case).
- `call_recording_start_failed` — the Twilio API call itself failed.
- `phone_recording_started` (info) — recording actually started;
  carries the `recording_sid`.
- `phone_recording_status_no_auth_token_configured` /
  `_signature_invalid` — the recording-status webhook rejected the
  callback (see §1's `twilio_auth_token` prerequisite).
- `phone_recording_status_unknown_call` — Twilio's callback couldn't be
  matched to any conversation (`find_conversation_ticket` found
  nothing).
- `phone_recording_status_write_failed` — matched the ticket, but the
  attribute write itself failed.

**Handoff (`phone/bridge.py`, `phone/handoff_target.py`, `router.py`):**
- `phone_handoff_no_caller_id_configured` — see §1/Scenario 7; the
  expected, safe outcome of a missing `phone_handoff_caller_id`.
- `phone_handoff_hours_check_failed` — business-hours lookup failed
  (fails open — the transfer is still attempted).
- `phone_handoff_no_action_url_configured` — `twilio_webhook_base_url`
  missing, so the `<Dial action>` callback URL couldn't be built;
  transfer refused.
- `phone_handoff_resolve_failed` / `phone_handoff_redirect_failed` — the
  resolve or the Twilio `calls().update()` call itself raised or timed
  out.
- `call_control_unconfigured` — no Twilio credentials configured at all.
- `phone_dial_status_no_auth_token_configured` / `_signature_invalid` —
  same auth prerequisite as the recording-status webhook.
- `phone_dial_status_completed` — the "successful transfer" branch
  (Scenario 5).
- `phone_dial_status_unanswered` — the "someone didn't pick up" branch
  (Scenario 6); carries the actual `DialCallStatus` value in `status=`.
- `phone_dial_status_unknown_call` / `_ticket_resolve_failed` /
  `_tag_check_failed` / `_ticket_update_failed` — various points where
  resolving or updating the ticket for an unanswered handoff failed.

---

## 6. Teardown

- [ ] If real credentials were used for this test, disable
      `phone_handoff_enabled` and `phone_recording_enabled` again unless
      the deployment is genuinely ready to run these features live.
- [ ] Close out any test conversations in Chatwoot (or leave them,
      clearly labelled, for reference).
- [ ] If a real recording was created, note its `recording_sid` and
      confirm whether it should be deleted given
      `phone_recording_retention_days` is not automatically enforced.

---

## 7. Reporting results

Whoever runs this should update
`docs/analysis/proton-demo-feedback-coverage-2026-07-28.md` items **#23**
(real human hand-off) and **#27** (call recording) — currently both
marked ❌ "not covered" — with which specific scenarios above were
actually exercised on a real number, and which were not. Do not mark
either ✅ on the strength of this document alone; only a person who has
actually run the scenarios and observed the results should do that.

---

*Package C code: `backend/apps/backend/src/chatbot/features/chat/phone/`
(`bridge.py`, `call_control.py`, `handoff_target.py`, `transcript_classifier.py`,
`transcript_sink.py`, `twiml.py`), `backend/apps/backend/src/chatbot/features/
chat/router.py` (`/voice/phone/*`, `/webhooks/phone/*` routes),
`backend/apps/backend/src/chatbot/features/chat/adapters/chatwoot.py`
(`set_ticket_classification`, `set_call_recording`, `_merge_custom_attributes`).
Settings: `backend/apps/backend/src/chatbot/platform/config.py` lines
266–376, `backend/apps/backend/.env.example` lines 208–288.*
