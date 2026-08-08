# P11 — Voice Partials Not Blocked by the Call Queue

**Date:** 2026-08-08
**Programme:** `docs/superpowers/specs/2026-08-08-rfp-partials-program-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-rfp-p11-voice-partials.md`
**Closes:** 12 PARTIAL requirements
**Effort:** 3 weeks · **Wave:** 3
**Depends on a decision, not on code:** Twilio Studio DTMF flow vs the AI voice bridge

---

## 1. Framing: what this package deliberately excludes

The largest gap in the entire analysis is **R9 — there is no call queue.** No
`<Enqueue>`, no TaskRouter, no wait music. Calls `<Dial>` a number and fall to
voicemail after 20 seconds. Abandon rate has nothing to abandon *from*, so AQT,
AHT, % answered in 20 s and After-Call-Work have no source. That blocks six of
the fourteen monthly control items and all of C1 §2, and it is 4–6 weeks of work
in its own programme.

**P11 is everything else.** Seven voice capabilities are PARTIAL for reasons that
have nothing to do with the queue, and every one of them is deliverable now:
recordings are already being made and their IDs stored with nothing to play them;
voicemail is already being recorded and reaches nobody; the AI bridge has no
after-hours message at all; the Studio flow routes to two placeholder phone
numbers that a human is supposed to replace by hand.

None of these need a queue. All of them are visible to a client.

## 2. The structural problem to resolve first

There are **two phone implementations** and no decision recorded about which
ships:

| | `deploy/twilio/ivr-studio-flow.json` | `backend/.../chat/phone/` |
|---|---|---|
| Nature | Hand-imported Twilio Studio flow | Conversational AI voice bridge |
| DTMF menu | Yes — Appendix B's exact bilingual prompts | **None at all** |
| Office-hours check | Yes, MYT Liquid epoch math, matches Appendix B exactly | A business-hours gate on handoff only |
| After-hours greeting + voicemail | Yes, correct bilingual text | **Nothing** |
| RSA vs non-RSA routing | Yes — to `+60300000001` / `+60300000002`, **placeholders** | One static hunt group, no RSA bypass |
| Deployed | **No code in this repo deploys, reads or tests it** | Yes — this is the live path |
| Verified against a real call | No | **No** |

Appendix B specifies the DTMF version. The deployed path is the AI bridge. The
gap analysis flags this as an open decision that "should be closed before this
section is answered to the client".

**This spec proceeds on the assumption that the AI bridge is the product**, and
that P11 brings it up to Appendix B — rather than reviving Studio. Reasons:

- The bridge is deployed, tested in the repo's suite, and is where every other
  investment (Gemini Live, transcript sink, CSAT tools, handoff resolver) sits.
- Studio is not deployed, read, or tested by any code here. Adopting it would
  mean owning a second implementation with no test coverage and an
  import-by-hand deployment story.
- A conversational AI front door is a better product than a DTMF tree, and it is
  the thing the proposal is actually built around.

**But the decision is the client's**, because Appendix B is their written SOP and
it specifies DTMF. So §3.2 delivers the DTMF menu *inside* the bridge — the
customer gets Appendix B's menu, and the bridge stays the implementation. That
satisfies both readings and costs less than maintaining two systems.

If PRO-NET insists on Studio proper, tasks 2, 4 and 5 change target and the
effort roughly doubles. **Raise this before building.**

## 3. Design

### 3.1 Recording retrieval and playback (§4.7)

The most obviously finishable item in the package.

Dual-channel recording already starts (`call_control.py:96`), the status webhook
already lands, and `recording_sid` / `recording_url` / `recording_duration` are
stored as internal-only conversation custom attributes. A `call_recording.listen`
permission already exists — and `authz/seed.py` documents, in a comment, that it
was registered ahead of time *"so retrieval can never ship un-gated by
omission"*. The gate was built before the door.

What is missing is the door: no retrieval endpoint, no player.

- `GET /calls/{conversation_id}/recording` — permission-gated, returning a
  short-lived signed URL rather than proxying the audio. Proxying customer voice
  data through the application adds bandwidth, a caching question and a second
  place recordings can leak.
- A player in the conversation sidebar (fork patch), visible only with the
  permission.
- **Every retrieval writes an audit entry.** This is customer voice data; who
  listened, and when, is exactly what an audit trail is for, and §3.2.6 asks for
  the audit trail to be complete.

**Retention.** `PHONE_RECORDING_RETENTION_DAYS=90` exists and its own comment
says nothing enforces it. P11 adds the enforcement — a job that deletes
recordings past the window from Twilio and clears the stored attributes — because
a declared retention policy that does not run is worse than none: it is a written
commitment the system contradicts.

### 3.2 DTMF menu in the bridge (B-IVR-06, B-IVR-05)

Appendix B's menu — RSA 1, Inquiry 2, Complaint 3, repeat 0, after an EN/BM
language choice — implemented as a `<Gather>` in front of the bridge's media
stream, with the exact bilingual prompts from the Studio flow's
`main_menu_en`/`main_menu_ms` (they are already correct; reuse the strings
verbatim).

The selection is passed to the bridge as context, so the AI opens knowing the
customer chose "complaint" rather than having to ask.

**A timeout or `0` falls through to the conversational bridge** rather than
looping. A caller who cannot use a keypad, or is driving, must still reach help;
a menu that traps them is a worse failure than a menu that is skipped.

### 3.3 After-hours on the bridge (§3.1.4, B-IVR-03)

The bridge has no after-hours message at all — the gap analysis found this
explicitly (channel guide §6.5). The Studio flow has the correct bilingual text
(`after_hours_en`, `after_hours_ms`, `vm_prompt`).

Port the text, gate on the same business-hours check `handoff_target.py::
_within_business_hours` already performs, and record voicemail.

**With the RSA exception, which is the operationally important part.**
§8.1.6 requires RSA to be available 24/7. An after-hours caller who selects RSA
must **not** get the voicemail prompt — they must reach the RSA path. The gap
analysis notes the bridge has "no RSA after-hours bypass at all". This is the
single highest-consequence item in P11: a stranded motorist at 2 a.m. reaching a
voicemail box is the failure that ends a contract.

### 3.4 Voicemail ingestion (B-IVR-03)

Today the after-hours promise — "our team will reach out on the next business
day" — is backed by nothing. **No `RecordingUrl` handler, no transcription, no
follow-up task.** The voicemail is recorded into Twilio and reaches nobody.

Design: a `RecordingUrl` webhook handler that creates a Chatwoot conversation on
the phone inbox, attaches the recording, transcribes it (the same Gemini path the
transcript classifier uses), sets the caller as the contact by number, and stamps
P1's `attend_after` to the next working instant so it surfaces on the right day's
queue.

This is the item that turns a stated promise into a delivered one, and it is
about a week.

### 3.5 Real routing targets (§4.5.2, B-IVR-07)

Two disconnected implementations, both wrong in different ways: Studio routes to
placeholder numbers `+60300000001` / `+60300000002` that must be replaced by
hand; the bridge resolves **one static hunt-group number**, with per-agent
routing present in the `HandoffTarget` dataclass (`kind == "client"`) but
explicitly unreachable.

- Configure real RSA and non-RSA targets in the escalation-routing admin, where
  operators already manage PIC and dealer contacts — not in env vars.
- Make `HandoffTarget.kind == "client"` reachable, so a call can route to a
  specific agent, and hand it to P6's `pick_agent` so voice uses the same
  selection logic as every other channel.
- **Placeholder numbers must fail loudly.** A startup check refuses to boot with
  `+6030000000*` configured. A placeholder that dials silently is how a demo
  becomes a production incident.

### 3.6 Live transcript on the agent screen (§3.1.2, §4.5.1)

`live_events.py` + `transcript_sink.py` + `bridge.py:424-487` already post
transcripts into the Chatwoot conversation *during* the call, flushed every 15
seconds behind `PHONE_TRANSCRIPT_LIVE_ENABLED`.

The requirement is "real-time on the agent screen", and two things are missing:
there is no live-call agent screen, and the cadence is 15 seconds rather than
per-utterance.

- Tighten the flush to per-utterance when a human agent is on or joining the
  call, keeping 15 seconds otherwise. Per-utterance writes for an AI-only call
  are Chatwoot API load for an audience of nobody.
- A live-transcript panel in the conversation view that appends as the call runs.

**Honesty:** this is "transcript appearing within a second or two", not
word-by-word streaming captions. The design says so rather than letting
"real-time" carry more than it earns.

### 3.7 End-of-call rating (§4.9)

In-call CSAT exists — the AI asks verbally and `handoff_csat_tools.py::
parse_csat_score` records 1–5. What is missing is the *transfer to a rating
system* the requirement names: no `<Redirect>` to a survey IVR, and
`features/chat/nps.py` is never invoked from any phone path.

Wire the phone path into the same survey and NPS flow P8 builds, so a phone CSAT
lands in `v_csat_by_agent` alongside WhatsApp. A separate survey IVR is the wrong
answer — it drops the caller into a second robot after they have already answered
the question conversationally.

### 3.8 The verification debt

**No real Twilio call has ever hit any of this code.**
`docs/testing/phone-channel-package-c-verification.md` states it plainly, and it
is why every phone row in the gap analysis is capped at PARTIAL regardless of
implementation quality.

P11's definition of done includes a **real-call verification script** covering:
inbound connect, language menu, DTMF selection, AI conversation,
Bahasa Melayu reliability (a known unresolved issue, channel guide §5.6),
handoff to a human, recording produced and playable, after-hours path, voicemail
ingested, RSA bypass, CSAT captured.

**No phone requirement should be reported as MET without this evidence.** It is
the difference between "code-complete" and "works", and the analysis's own
standard of proof treats them differently.

## 4. What could go wrong

| Risk | Mitigation |
|---|---|
| Building against the wrong implementation | §2 states the assumption and the cost of being wrong; raise before building |
| An after-hours RSA caller reaches voicemail | The RSA bypass is a named, separately tested requirement, not a branch of the after-hours flow |
| A DTMF menu traps callers who cannot use it | Timeout and `0` fall through to the conversational bridge |
| Placeholder numbers reach production | Startup check refuses to boot on `+6030000000*` |
| Recording retrieval leaks customer voice data | Permission-gated, signed short-lived URLs, every access audited |
| Retention is declared and never enforced | The enforcement job is part of this package |
| Per-utterance flushing floods the Chatwoot API | Only when a human is on the call |
| "Real-time transcript" over-promises | Documented as near-real-time append, not streaming captions |

## 5. Testing

- **Recording** (`test_recording_retrieval.py`): permission required; signed URL
  not a proxy; every access audited; expired recording returns a clear state;
  retention job deletes past the window.
- **DTMF** (`test_dtmf_menu.py`): each selection routes; timeout falls through;
  `0` repeats once then falls through; prompts match Appendix B verbatim.
- **After hours** (`test_phone_after_hours.py`): out-of-hours plays the message;
  **RSA selection bypasses it**; in-hours unchanged.
- **Voicemail** (`test_voicemail_ingest.py`): creates a conversation; attaches
  audio; transcribes; contact matched by number; `attend_after` set to the next
  working instant.
- **Targets** (`test_handoff_targets.py`): RSA and non-RSA resolve separately;
  `kind == "client"` reachable; startup refuses placeholder numbers.
- **Transcript** (`test_live_transcript.py`): per-utterance with an agent
  present; 15 s otherwise.

## 6. Feature flags

| Setting | Default | Effect |
|---|---|---|
| `PHONE_RECORDING_RETRIEVAL_ENABLED` | `false` | Off = no endpoint, no player |
| `PHONE_RECORDING_RETENTION_ENFORCED` | `false` | Off = today's declarative-only policy |
| `PHONE_DTMF_MENU_ENABLED` | `false` | Off = straight to the conversational bridge |
| `PHONE_AFTER_HOURS_ENABLED` | `false` | Off = today's no-message behaviour |
| `PHONE_RSA_AFTER_HOURS_BYPASS` | `true` | **Defaults on** — see below |
| `VOICEMAIL_INGEST_ENABLED` | `false` | Off = voicemail stays in Twilio |
| `PHONE_TRANSCRIPT_PER_UTTERANCE` | `false` | Off = today's 15 s flush |

**`PHONE_RSA_AFTER_HOURS_BYPASS` defaults on, and it is the only flag in this
programme that does.** It only has an effect when `PHONE_AFTER_HOURS_ENABLED` is
also on, and in that combination the safe default is that a 2 a.m. breakdown call
reaches help. Defaulting it off would mean that switching on after-hours
messaging silently sends stranded motorists to voicemail.

## 7. Requirements closed

3.1.2, 3.1.4 (voice half), 4.5.1, 4.5.2, 4.7, 4.9, 4.26, B-IVR-01, B-IVR-03,
B-IVR-04, B-IVR-06, B-IVR-07 — **all conditional on the real-call verification in
§3.8.**

**Explicitly not closed:** B-IVR-08 (queue-busy prompt) and B-IVR-09
(answered-within-20-s) need R9. P11 makes the busy prompt *honest* — it stops
saying "please stay on the line" when there is no line to stay on — but it does
not build a queue.
