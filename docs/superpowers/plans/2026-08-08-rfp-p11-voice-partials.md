# P11 — Voice Partials Not Blocked by the Call Queue: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the voice capabilities that do not need a call queue — recordings that can be played, voicemail that reaches somebody, an after-hours message on the path that is actually deployed, and RSA reachable at 2 a.m.

**Architecture:** The AI voice bridge is the product; Appendix B's DTMF menu is implemented **inside** it as a `<Gather>` in front of the media stream, rather than reviving the un-deployed Studio flow. The bilingual prompt strings from the Studio flow are correct and are reused verbatim.

**Tech Stack:** Python 3.12, Twilio Voice + TwiML, Gemini Live, Chatwoot API, fork patch for the player and transcript panel, pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-rfp-p11-voice-partials-design.md`

## ⚠️ Read before task 1

**This plan assumes the AI voice bridge ships and the Twilio Studio flow is
retired.** That decision is not recorded anywhere and Appendix B specifies the
DTMF version. §2 of the spec sets out the reasoning. If PRO-NET requires Studio
proper, **tasks 2, 4 and 5 change target and the effort roughly doubles.**

Raise it before writing code. This is a half-day conversation that can waste two
weeks.

## Global Constraints

- **An after-hours RSA caller must never reach voicemail.** §8.1.6 requires RSA
  24/7. This is the highest-consequence behaviour in the package and it gets its
  own tests, its own flag, and a default of **on**.
- **No phone requirement is reported as MET without a real-call verification.**
  No real Twilio call has ever hit this code
  (`docs/testing/phone-channel-package-c-verification.md`). Code-complete is not
  done here.
- **Recordings are customer voice data.** Permission-gated, short-lived signed
  URLs, never proxied through the app, every access audited.
- **A declared retention policy must actually run.** `PHONE_RECORDING_RETENTION_DAYS`
  currently enforces nothing — a written commitment the system contradicts.
- **Placeholder numbers must fail loudly**, at startup, not at dial time.
- **A DTMF menu must never trap a caller.** Timeout and `0` fall through to the
  conversational bridge.
- Env vars in `config.py` + `deploy/tenants/example.env` + `tests/conftest.py`.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/.../features/chat/phone/recording_router.py` | **New.** Signed-URL retrieval, audited |
| `backend/.../features/chat/phone/retention.py` | **New.** The deletion job |
| `backend/.../features/chat/phone/dtmf_menu.py` | **New.** `<Gather>` + Appendix B prompts |
| `backend/.../features/chat/phone/after_hours.py` | **New.** Message, voicemail, **RSA bypass** |
| `backend/.../features/chat/phone/voicemail_ingest.py` | **New.** `RecordingUrl` → conversation |
| `backend/.../features/chat/phone/handoff_target.py` | **Modify.** Real RSA/non-RSA, `kind=="client"` |
| `backend/.../features/chat/phone/transcript_sink.py` | **Modify.** Per-utterance when an agent is present |
| `deploy/chatwoot-fork/patches/00NN-call-recording-player.patch` | **New.** Player + live transcript panel |
| `docs/testing/2026-08-08-phone-real-call-verification.md` | **New.** The evidence |

---

### Task 0: Close the Studio-vs-bridge decision

**Not a code task.** Produce a one-page note for PRO-NET stating: the two
implementations, what each does and does not do, that the bridge is the deployed
and tested path, that Appendix B's DTMF menu will be delivered inside it, and
what changes if they require Studio.

**Do not start task 2, 4 or 5 until this is answered.** Tasks 1, 6, 7 and 8 are
safe to start regardless — they are bridge-side and Studio-agnostic.

---

### Task 1: Recording retrieval

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/recording_router.py`
- Create: its test file
- Create: `deploy/chatwoot-fork/patches/00NN-call-recording-player.patch`

**Interfaces:**
- Consumes: the stored `recording_sid` / `recording_url` custom attributes, `call_recording.listen`.
- Produces: `GET /calls/{conversation_id}/recording` returning a short-lived signed URL.

**Tests first:**

```python
async def test_a_caller_without_call_recording_listen_is_rejected():
async def test_a_permitted_caller_receives_a_signed_url():
async def test_the_audio_is_not_proxied_through_the_application():
async def test_the_signed_url_expires():
async def test_every_retrieval_writes_an_audit_entry_naming_the_listener():
async def test_a_conversation_with_no_recording_returns_a_clear_empty_state():
async def test_a_recording_deleted_by_retention_returns_a_distinct_state():
async def test_the_flag_off_returns_404():
```

**Test five is why the permission was registered ahead of the endpoint.**
`authz/seed.py` says in a comment that `call_recording.listen` exists "so
retrieval can never ship un-gated by omission" — honour that intent by auditing
access, not merely gating it.

Test seven: "no recording was made" and "the recording was deleted under the
retention policy" are different answers, and a client asking why they cannot hear
a call deserves the right one.

**Verify:** `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/phone/test_recording_retrieval.py -q`

---

### Task 2: DTMF menu *(blocked on task 0)*

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/dtmf_menu.py`
- Create: its test file
- Modify: `twiml.py`

**Interfaces:**
- Consumes: the bilingual prompt strings from `deploy/twilio/ivr-studio-flow.json` (`main_menu_en`, `main_menu_ms`, `language_gather`) — **reuse verbatim**, they already match Appendix B.
- Produces: a `<Gather>` before the media stream; the selection passed to the bridge as context.

**Tests first:**

```python
def test_the_english_menu_prompt_matches_appendix_b_verbatim():
def test_the_malay_menu_prompt_matches_appendix_b_verbatim():
def test_pressing_1_routes_to_the_rsa_path():
def test_pressing_2_and_3_pass_inquiry_and_complaint_context_to_the_bridge():
def test_pressing_0_repeats_the_menu_once():
def test_a_second_zero_falls_through_to_the_conversational_bridge():
def test_a_timeout_falls_through_to_the_conversational_bridge():
def test_an_invalid_key_falls_through_rather_than_looping():
def test_the_flag_off_goes_straight_to_the_bridge_exactly_as_today():
```

**Tests five to eight are one requirement stated four ways:** the menu must never
trap a caller. Someone driving, or on a poor line, or using a phone whose keypad
does not register, still needs to reach help.

**Verify:** `uv run pytest src/chatbot/features/chat/phone/test_dtmf_menu.py -q`

---

### Task 3: After-hours message and the RSA bypass

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/after_hours.py`
- Create: its test file

**Interfaces:**
- Consumes: `handoff_target.py::_within_business_hours`, the Studio flow's `after_hours_en` / `after_hours_ms` / `vm_prompt` strings.
- Produces: an after-hours branch with a voicemail record — **and an RSA bypass**.

**Tests first:**

```python
async def test_an_out_of_hours_call_plays_the_bilingual_after_hours_message():
async def test_the_message_text_matches_appendix_b_verbatim():
async def test_an_out_of_hours_caller_who_selects_rsa_bypasses_the_message():
async def test_an_out_of_hours_rsa_caller_reaches_the_rsa_target():
async def test_an_out_of_hours_rsa_caller_never_reaches_voicemail():
async def test_an_in_hours_call_is_completely_unchanged():
async def test_the_bypass_flag_defaults_to_on():
async def test_disabling_the_bypass_is_logged_as_a_deliberate_configuration():
async def test_a_business_hours_lookup_failure_treats_the_call_as_in_hours():
```

**Tests three, four, five and seven are the same requirement**, written four
times deliberately. §8.1.6 requires RSA available 24/7; a stranded motorist at
2 a.m. reaching a voicemail box is the failure mode that ends a contract.

Test nine is the fail-open direction: if business hours cannot be resolved, treat
the call as in-hours and connect it. Failing the other way sends a daytime caller
to voicemail.

**Verify:** `uv run pytest src/chatbot/features/chat/phone/test_phone_after_hours.py -q`

---

### Task 4: Voicemail ingestion *(blocked on task 0)*

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/voicemail_ingest.py`
- Create: its test file

**Interfaces:**
- Consumes: Twilio's `RecordingUrl` webhook, the transcript path, P1's `next_working_instant`.
- Produces: a Chatwoot conversation on the phone inbox with the audio attached, transcribed, contact matched, and `attend_after` stamped.

**Tests first:**

```python
async def test_a_voicemail_creates_a_conversation_on_the_phone_inbox():
async def test_the_audio_is_attached_to_the_conversation():
async def test_the_voicemail_is_transcribed_into_the_conversation():
async def test_the_caller_is_matched_to_an_existing_contact_by_number():
async def test_an_unknown_caller_creates_a_contact():
async def test_attend_after_is_set_to_the_next_working_instant():
async def test_a_transcription_failure_still_creates_the_conversation_with_the_audio():
async def test_a_duplicate_webhook_delivery_does_not_create_two_conversations():
async def test_the_flag_off_leaves_the_voicemail_in_twilio_as_today():
```

**Test seven states the priority order:** the audio reaching a human matters far
more than the transcript. A transcription failure must not swallow the voicemail.

Test six is what makes the after-hours promise — "our team will reach out on the
next business day" — actually true.

**Verify:** `uv run pytest src/chatbot/features/chat/phone/test_voicemail_ingest.py -q`

---

### Task 5: Real routing targets *(blocked on task 0)*

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/phone/handoff_target.py`
- Modify: the escalation-routing admin (store RSA / non-RSA targets there)
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/test_handoff_targets.py`

**Interfaces:**
- Consumes: the escalation-routing store, P6's `pick_agent`.
- Produces: distinct RSA and non-RSA targets; `HandoffTarget.kind == "client"` reachable.

**Tests first:**

```python
async def test_rsa_and_non_rsa_resolve_to_different_targets():
async def test_targets_are_read_from_the_admin_store_not_from_env():
async def test_a_client_kind_target_routes_to_a_specific_agent():
async def test_agent_selection_reuses_pick_agent_and_not_a_second_implementation():
async def test_the_service_refuses_to_start_with_a_placeholder_number_configured():
async def test_the_startup_error_names_the_offending_setting():
async def test_an_unconfigured_rsa_target_is_a_startup_error_not_a_runtime_surprise():
```

**Tests five to seven exist because `+60300000001` is currently a live default.**
A placeholder that dials silently turns a demo into a production incident, and
the failure surfaces at 2 a.m. on an RSA call. Fail at boot.

Test four keeps voice on the same routing logic as every other channel — a second
agent-selection implementation is how the two drift.

**Verify:** `uv run pytest src/chatbot/features/chat/phone/test_handoff_targets.py -q`

---

### Task 6: Live transcript cadence and panel

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/phone/transcript_sink.py`
- Modify: the fork patch from task 1 (add the live panel)
- Modify: `test_transcript_sink.py`

**Tests first:**

```python
async def test_the_flush_is_per_utterance_when_a_human_agent_is_on_the_call():
async def test_the_flush_stays_at_fifteen_seconds_for_an_ai_only_call():
async def test_an_agent_joining_mid_call_switches_the_cadence():
async def test_the_panel_appends_rather_than_replacing():
async def test_the_flag_off_reproduces_todays_fifteen_second_behaviour():
async def test_a_chatwoot_write_failure_does_not_interrupt_the_call():
```

**Test two guards the API budget:** per-utterance writes for an AI-only call are
load for an audience of nobody.

Test six is the priority: a transcript write must never be able to drop a live
customer call.

**Documentation requirement:** describe this as **near-real-time append**, not
streaming captions. It is a transcript appearing within a second or two.

**Verify:** `uv run pytest src/chatbot/features/chat/phone/test_transcript_sink.py -q`

---

### Task 7: Phone CSAT into the shared survey flow

**Files:**
- Modify: the phone post-call path
- Modify: `backend/.../features/chat/nps.py` call site (shared with P8 task 5)
- Create: its test file

**Tests first:**

```python
async def test_an_in_call_csat_score_reaches_the_same_store_as_whatsapp_csat():
async def test_a_phone_csat_appears_in_v_csat_by_agent():
async def test_nps_is_recorded_from_the_phone_path_when_sampled():
async def test_no_separate_survey_ivr_is_dialled():
async def test_a_call_with_no_csat_response_records_nothing_rather_than_a_zero():
```

**Test four is a design decision worth asserting:** dropping a caller into a
second robot after they have already answered the question conversationally is a
worse experience than the requirement's literal reading, and the conversational
capture satisfies the intent.

Test five: no answer is not a score of zero.

**Verify:** `uv run pytest src/chatbot/features/chat/phone/test_handoff_csat_tools.py -q`

---

### Task 8: Retention enforcement

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/phone/retention.py`
- Create: its test file

**Tests first:**

```python
async def test_a_recording_older_than_the_window_is_deleted_from_twilio():
async def test_the_stored_attributes_are_cleared_after_deletion():
async def test_a_recording_inside_the_window_is_untouched():
async def test_a_twilio_delete_failure_is_retried_and_logged():
async def test_the_job_is_idempotent():
async def test_the_flag_off_runs_no_deletions():
async def test_a_deleted_recording_is_distinguishable_from_one_that_never_existed():
```

**This closes a written commitment the system currently contradicts.**
`PHONE_RECORDING_RETENTION_DAYS=90` exists and its own comment says nothing reads
it. A declared retention policy that does not run is worse than no policy.

**Verify:** `uv run pytest src/chatbot/features/chat/phone/test_retention.py -q`

---

### Task 9: Real-call verification

**Files:**
- Create: `docs/testing/2026-08-08-phone-real-call-verification.md`

**This is the task that lets any phone requirement be reported as MET.** No real
Twilio call has ever hit this code. Execute against a scratch tenant and a real
Twilio number, and record the evidence:

- [ ] Inbound call connects
- [ ] Language menu plays in EN and BM; the voice is female as B-IVR-04 requires
- [ ] Each DTMF selection routes correctly
- [ ] Conversational AI responds in English
- [ ] Conversational AI responds in Bahasa Melayu — **record the reliability
      observed**; this is a known unresolved issue (channel guide §5.6) and the
      result must be reported honestly whichever way it goes
- [ ] Handoff to a human connects to a real number
- [ ] Recording produced, retrievable and playable through the new endpoint
- [ ] Live transcript appears during the call
- [ ] After-hours call plays the after-hours message
- [ ] **After-hours RSA selection bypasses it and connects**
- [ ] Voicemail ingested into a case with a transcript
- [ ] In-call CSAT captured and visible in reporting

**Any unchecked box is a requirement that stays PARTIAL.** Report it that way.

---

### Task 10: Flags, env, docs

**Files:**
- Modify: `deploy/tenants/example.env`, `backend/.../platform/config.py`
- Modify: `README.md`, `deploy/twilio/README.md`

**Tests first:**

```python
def test_the_seven_settings_are_present_in_example_env():
def test_phone_rsa_after_hours_bypass_defaults_to_true():
def test_every_other_new_setting_defaults_to_false():
def test_the_service_refuses_to_start_with_placeholder_numbers():
```

**Docs note (the deliverable):**

> **`PHONE_RSA_AFTER_HOURS_BYPASS` defaults to ON.** It is the only default-on
> flag in this programme. It takes effect only when `PHONE_AFTER_HOURS_ENABLED`
> is also on, and in that combination the safe default is that an out-of-hours
> RSA caller reaches help rather than voicemail (§8.1.6 requires RSA 24/7).
> Turning it off is a deliberate act and is logged as one.
>
> **Call recordings** are customer voice data. Retrieval requires
> `call_recording.listen`, returns a short-lived signed URL, and **every access
> is written to the audit log**. Recordings are deleted after
> `PHONE_RECORDING_RETENTION_DAYS` — enforced from this release; before it, the
> setting was declarative only.
>
> **The Twilio Studio flow (`deploy/twilio/ivr-studio-flow.json`) is retired.**
> Appendix B's DTMF menu is now delivered inside the AI voice bridge. Studio was
> never deployed, read or tested by any code in this repository.

**Verify:** suite green with defaults, then with every flag on.

---

## Definition of done

- [ ] Task 0 answered by the client **before** tasks 2, 4 and 5 were started.
- [ ] All flags at defaults → suite green, behaviour identical to `d85f0d4`.
- [ ] An out-of-hours RSA call provably reaches the RSA target and never voicemail.
- [ ] The service refuses to boot with a placeholder number.
- [ ] Recordings retrievable, playable, audited, and deleted on schedule.
- [ ] Voicemail creates a case with a transcript and a next-business-day `attend_after`.
- [ ] DTMF menu cannot trap a caller.
- [ ] **Task 9 executed against a real Twilio call, with every box checked or explicitly reported unchecked.**
- [ ] Nothing merged to `main`.
