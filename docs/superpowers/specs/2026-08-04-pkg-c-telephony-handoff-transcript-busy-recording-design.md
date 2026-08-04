# Package C — Real call handoff, live transcript-to-ticket, auto-busy, call recording

**Date:** 2026-08-04
**Covers demo-feedback items:** #23 (live human hand-off on a call), #21 (auto-busy during a call), #27 (call recording), plus a new requirement raised 2026-08-04: **live transcription into the ticket, ticket created immediately, status derived from the transcript**
**Type:** the heaviest package. Four features, one shared piece of plumbing.
**Effort:** large. Strictly sequenced.

---

## 1. Goal

Make the phone channel behave like a real contact centre:

> Customer calls the contact-centre number → AI answers → AI can't handle it →
> **immediately** hands to a human agent, on the same call → a ticket exists
> from the start of the call, carries the live transcript, and lands in a
> status derived from what was actually said → the agent taking the call stops
> receiving WhatsApp routing → the whole call is recorded for QA.

## 2. Why these four ship together

Every one of them needs the same thing: **control of a live Twilio call from
our backend, keyed on `CallSid`.** We already capture `CallSid`
(`features/chat/phone/bridge.py:59-62`) but never act on it. Build that control
plane once, and handoff, auto-busy, and recording are each a modest addition on
top. Build them separately and the plumbing gets written three times.

Sequence: **transcript-to-ticket → handoff → auto-busy → recording.** Transcript
first because it is independent of Twilio call control and immediately useful;
auto-busy after handoff because "which agent is on a call" is meaningless until
calls reach agents.

## 3. Current state (verified)

`PhoneBridge` (`features/chat/phone/bridge.py`) bridges a Twilio Media Stream to
Gemini Live:

- `handle_twilio` captures `stream_sid` and `call_sid` on the `start` event.
- `pump()` streams audio both ways and accumulates `self.transcript` as
  `(role, text)` pairs from `InputTranscript` / `OutputTranscript` events —
  **the transcript already exists in memory, live, today.**
- `request_human_handoff` is a Gemini tool (`handoff_csat_tools.py`). When the
  model calls it, the bridge merely records `self.handoff` and answers the model
  with `{"status": "ticket_created"}` — **which is a lie at that moment**;
  nothing has been created yet.
- `finalize()` runs only when the socket closes, i.e. **after the call has
  ended**: it creates the Chatwoot conversation, posts the whole transcript as
  one comment, sets status `open` if a handoff was requested else `solved`.
- TwiML is `<Connect><Stream>` (`phone/twiml.py`), a bidirectional stream.
- Config already has `twilio_account_sid`, `twilio_auth_token`,
  `twilio_phone_number`, plus `twilio_api_key_sid` / `twilio_twiml_app_sid`
  (the browser-softphone path, half-configured). The `twilio` Python SDK is
  already a dependency (`phone/token.py`).

So: no live ticket, no real transfer, no recording, and presence is read-only
(`features/routing/presence.py` has no writer).

## 4. Feature 1 — Ticket created immediately, transcript streamed in

### 4.1 Design

Move ticket creation from `finalize()` to the **start of the call**, and append
the transcript as it accumulates.

- On the Media Stream `start` event, once `call_sid` is known, create the
  Chatwoot conversation for `session_id = f"phone-{call_sid}"` via the existing
  `ConversationLogPort.ensure_conversation_ticket`. Do it as a background task
  so the audio path is never blocked.
- Flush transcript increments to the conversation **on turn boundaries** (when
  the speaker role changes) with a floor of one flush per N seconds
  (`phone_transcript_flush_seconds`, default 15) so a long monologue still
  shows up. Batch, don't post per delta — Gemini streams many small fragments
  and one Chatwoot message per fragment would be unusable and rate-limited.
- `finalize()` keeps a final flush plus the closing status update, so a call
  that dies mid-stream still ends consistent. Creation becomes idempotent —
  `ensure_conversation_ticket` is already keyed on `session_id`, so a retry
  after a failed create returns the same conversation rather than a duplicate.
- Fail-open throughout: if ticket creation fails at call start, log it, keep
  the call running, and let `finalize()` retry. **A CRM failure must never drop
  a phone call.**

### 4.2 Status derived from the transcript

Today status is binary (`open` if handoff else `solved`). Extend to a small
classifier run once, at `finalize()`, over the full transcript:

- **case_type** — inquiry / complaint / feedback (the decks' top-level split)
- **division** and **concern** — the same taxonomy the reporting uses
  (`features/metrics/mapping.py` already owns this vocabulary; reuse it, don't
  invent a second one)
- **status** — `open` when a handoff happened or the classifier reads the issue
  as unresolved; `resolved` when the caller confirmed resolution; `pending`
  when awaiting a customer callback.

Write these as conversation custom attributes so reporting (Package E) and the
Contacts 360 panel (Package B) pick them up for free. Gate on a flag
(`phone_transcript_classification_enabled`, default off). Fail-open: a
classifier error falls back to today's binary rule exactly.

### 4.3 The human-leg gap — be explicit about it

Once the call transfers to a human (Feature 2), the Gemini Live stream ends,
so **the human half of the conversation is not transcribed by this design.**
Two ways to cover it, and this is a real decision to make before building:

| Option | How | Cost |
|---|---|---|
| **C1. Recording + post-call transcription** (recommended) | Dial with recording on (Feature 4), transcribe the recording after the call, append to the same conversation. | Cheap, simple, not live — transcript lands minutes after the call. |
| **C2. Fork a second stream** | `<Start><Stream>` on the dial leg feeding a transcription-only sink, appended live. | Live, but doubles streaming cost and adds a second concurrent socket per call. |

C1 satisfies "transcription in the ticket". C2 is what "during the call"
literally asks for. **Recommend C1 first**, since it rides on Feature 4 which we
are building anyway, and revisit C2 only if agents actually need to read the
human leg while it's still happening.

## 5. Feature 2 — Real hand-off to a human

### 5.1 Mechanism

When `request_human_handoff` fires, the backend calls the Twilio REST API to
**redirect the live call**: update the call identified by `CallSid` with new
TwiML that `<Dial>`s the human. Twilio ends the current `<Connect><Stream>`
verb and executes the new TwiML on the same call — that is the documented
redirect behaviour, and it's why capturing `CallSid` matters.

Sketch:

```
POST /2010-04-01/Accounts/{sid}/Calls/{CallSid}.json
  Twiml=<Response><Dial record="record-from-answer-dual"
           action="{base}/webhooks/phone/dial-status">
           <Number>{target}</Number></Dial></Response>
```

Then: `request_human_handoff` may finally answer `{"status": "transferring"}`
honestly, and the model should say a transfer line before the audio path
closes.

### 5.2 The open decision — who is dialled

**This is still unresolved and blocks implementation.** The options:

- **Existing careline hunt group.** One number Proton already has that rings
  their agents. Simplest, works with whatever PBX they run. Cost: we don't know
  which human answered, so Feature 3 (auto-busy) and per-agent reporting can't
  work.
- **Per-agent numbers held in the CRM.** Each Chatwoot agent gets a phone
  number; our `RoutingService.pick_agent` chooses an available one and Twilio
  dials that person. Needed for auto-busy and for attribution. Costs an admin
  UI plus collecting the numbers.
- **Phased:** hunt group now so the next demo is honest, per-agent later.

Assumption if no answer arrives before build starts: **phased.** Ship the hunt
group, design the target as a resolver interface
(`HandoffTargetResolver.resolve() -> target`) with a static-number
implementation first and a routing-backed one second, so Feature 3 slots in
without rework. The return type must be a target *descriptor* (PSTN number or
client identifier), not a bare E.164 string — see the appendix in §12.3, where
WhatsApp calls turn out to be unable to reach any PSTN endpoint.

### 5.3 Failure handling — the part that gets skipped and shouldn't

- **Nobody answers / busy / rejected.** The `<Dial action=...>` callback returns
  `no-answer`, `busy`, or `failed`. Respond with fallback TwiML: apologise, take
  a callback number or voicemail, and mark the conversation `open` with an
  `unanswered_handoff` label so it is visibly owed a callback. Silently dropping
  the caller is the worst possible outcome and is exactly what an untested
  implementation does.
- **Out of business hours.** Reuse the existing business-hours logic that
  already drives RSA after-hours routing (feedback #24) rather than adding a
  second notion of open hours.
- **Twilio API failure.** Keep the caller with the AI, apologise, fall back to
  today's behaviour (ticket + human follows up later). Fail-open.

## 6. Feature 3 — Auto-busy while on a call

### 6.1 Design

Two layers, because they fail differently:

1. **Our own routing truth (authoritative).** An `on_call` set keyed by agent
   id, written when a transfer is dialled and cleared on the dial-status
   callback (including the failure statuses — a failed transfer must not leave
   an agent permanently busy). `RoutingService.pick_agent` excludes those
   agents from `online`, exactly like the concurrent-conversation cap added on
   2026-08-04 (`features/routing/service.py:54-64`). This is the layer that
   actually stops WhatsApp from routing to them.
2. **Chatwoot availability (cosmetic but expected).** Best-effort write of the
   agent's availability to `busy` so the UI reflects reality, restored on call
   end. **Verify whether the Chatwoot API on `v4.15.1` permits an admin to set
   another user's availability before promising this** — if it doesn't, layer 1
   still delivers the actual requirement and the UI shows a custom label
   instead.

Add a **stale-entry sweep**: any `on_call` entry older than a configured
maximum call length is cleared automatically. Without it, one missed callback
takes an agent out of routing until the process restarts.

### 6.2 Dependency

Layer 1 requires knowing *which* agent was dialled, i.e. the per-agent target
option in §5.2. With a hunt group, this feature cannot work at all. **If the
hunt-group route is chosen, Feature 3 must be deferred**, and the coverage doc
should say so rather than claiming it shipped.

## 7. Feature 4 — Call recording

### 7.1 Design

Start recording via the Twilio REST recording resource on the live `CallSid`
when the stream starts, with `recording_channels="dual"` so caller and
agent/AI are separable for QA, and a `recording_status_callback` pointing at a
new `/webhooks/phone/recording-status` endpoint. On the callback, store the
recording SID, duration, and URL as conversation custom attributes so the
recording is reachable from the ticket. After a transfer, the `<Dial
record="record-from-answer-dual">` attribute covers the human leg.

**Storage decision:** leave recordings in Twilio (simplest, per-minute storage
cost, Twilio-hosted URLs need signed access) or copy to GCS on the callback
(one more moving part, cheaper at volume, keeps data in our control and
supports a retention policy). **Recommend copy-to-GCS with a retention period**,
because a QA/compliance feature with no retention policy is a liability, and
because the transcription option C1 in §4.3 needs the audio anyway.

### 7.2 Compliance — not optional

Malaysia's PDPA requires notice. The call must open with a recorded-line
announcement before recording starts, in both English and Bahasa Melayu, and
the announcement text must be operator-configurable (it belongs with the
existing lifecycle/persona messages, not hard-coded). If Proton declines the
announcement, recording should stay off — that is their call to make
knowingly, not ours to make silently.

### 7.3 Access control

Recordings are customer voice data. Gate retrieval behind a new permission
(`call_recording.listen`) using the existing `require_permission` pattern, and
log access. Do not expose raw Twilio URLs to the browser.

## 8. Config

New settings, each in **both** `platform/config.py` and
`deploy/tenants/example.env` per repo convention:

| Setting | Default | Purpose |
|---|---|---|
| `phone_handoff_enabled` | `false` | Master switch for real transfer |
| `phone_handoff_target_number` | `""` | Hunt-group number (phase 1) |
| `phone_handoff_timeout_seconds` | `30` | Dial timeout before fallback |
| `phone_transcript_live_enabled` | `false` | Create ticket at call start + stream transcript |
| `phone_transcript_flush_seconds` | `15` | Flush cadence |
| `phone_transcript_classification_enabled` | `false` | Derive status/case_type/division |
| `phone_recording_enabled` | `false` | Start recording |
| `phone_recording_storage` | `twilio` | `twilio` or `gcs` |
| `phone_recording_retention_days` | `90` | Deletion policy |
| `routing_on_call_max_seconds` | `3600` | Stale on-call sweep |

Every one defaults to today's behaviour. Turning them all off must be
byte-identical to the current build.

## 9. Testing

Unit, with the Twilio REST client injected and stubbed (`respx`), mirroring how
Gemini clients are injected today:

- handoff issues exactly one call-update with well-formed TwiML;
- each dial-status outcome (`completed`, `no-answer`, `busy`, `failed`) drives
  the right fallback and clears `on_call`;
- transcript flush batches by turn and by timer, never one message per delta;
- ticket creation at call start is idempotent under a retry;
- a Twilio API failure at any point leaves the call alive and degrades to
  today's finalize-only behaviour;
- `pick_agent` skips on-call agents, and the stale sweep releases them;
- recording callback persists attributes; retrieval without the permission 403s;
- **flags off → zero new API calls**, asserted explicitly.

Manual, on a real number, because none of the above proves the call works:
place a call, force a handoff, answer on the target phone, confirm audio both
ways, confirm the ticket exists mid-call with a live transcript, confirm the
recording lands and plays, confirm the agent stops receiving WhatsApp.

## 10. Risks

- **The transfer moment is audibly abrupt.** Twilio tears down the stream and
  dials; the caller hears silence unless the AI says a handover line first and
  hold music covers the dial. Script it deliberately.
- **Cost.** Every transferred call is now two legs plus recording storage plus
  (for C2) a second stream. Give Proton a per-call cost figure before enabling.
- **Twilio is now in the critical path of a live conversation.** Today a Twilio
  API failure is invisible; after this it can drop a caller. Every path needs a
  fallback, hence the emphasis in §5.3.
- **Recording changes the data-protection posture** of the whole platform.

## 11. Out of scope

- Browser softphone (Twilio Voice SDK) — `twilio_api_key_sid` /
  `twilio_twiml_app_sid` exist in config, but building an in-Chatwoot dialer is
  its own project.
- Call queueing, hold music management, IVR menu trees, DTMF routing (feedback
  #22 is still an open Proton decision).
- Outbound/click-to-call.
- Voice Intelligence / automated QA scoring on recordings.

## 12. Appendix — WhatsApp calling (raised 2026-08-04)

The inbox grid shows a greyed **WhatsApp Call (Beta)** card. Two different
routes exist to WhatsApp voice, and they have very different costs.

### 12.1 Route 1 — Chatwoot's native WhatsApp calling. Not viable for us.

Two independent blockers, both verified:

- Chatwoot's docs are explicit that the inbox **must be a WhatsApp Cloud API
  inbox**; inboxes connected through other providers cannot use calling. Ours
  is a **Twilio-provider** WhatsApp inbox
  (`crm-channel-interaction-guide.md:30`), so enabling it means migrating the
  WhatsApp channel off Twilio onto Meta Cloud API.
- Voice calling reached self-hosted in **v4.15.0 on paid plans only** — not
  community edition, which is what we run (we have been self-building the
  enterprise gaps into the fork: SLA policies patch `0025`, roles &
  permissions patch `0027`).

So the greyed card is not a toggle we're missing. Deprioritise.

### 12.2 Route 2 — Twilio WhatsApp Business Calling into our own bridge

WhatsApp Business Calling is **generally available** on Twilio Programmable
Voice, and the relevant property is that an inbound WhatsApp call *"will
generate a webhook to your Twilio Voice application, which will handle the call
in the same manner as inbound calls to a Twilio phone number."* That is the
same webhook and the same TwiML our AI bridge already answers — so in principle
`PhoneBridge` handles a WhatsApp call **unchanged**.

Unverified and worth testing before committing: Twilio's material says WhatsApp
calling streams connect to conversational AI, IVR, bots, recording and STT, but
does not explicitly confirm bidirectional `<Connect><Stream>` Media Streams on
WhatsApp calls. Treat that as the first experiment, not an assumption.

### 12.3 The constraint that changes §5.2

Twilio's docs state: **"Calls to WhatsApp destinations cannot be connected to
Public Switched Telephone Network (PSTN) endpoints."**

This directly contradicts §5's handoff design for the WhatsApp case: `<Dial>`
to an agent's mobile or to a careline hunt group is a PSTN endpoint and **will
not work on a WhatsApp call**. A WhatsApp call can only be handed to a
non-PSTN endpoint — in practice a Twilio Client browser softphone.

Consequence for the §5.2 decision:

| Choice | PSTN calls | WhatsApp calls |
|---|---|---|
| Hunt-group number | Works | **Impossible** |
| Per-agent mobile numbers | Works, enables auto-busy | **Impossible** |
| Browser softphone (Twilio Client) | Works | Works |

So if WhatsApp calling is genuinely wanted later, the softphone route is the
only one that serves both channels, and choosing a hunt group now closes that
door. This does not change the recommendation to ship a phased PSTN handoff
first — it does mean the `HandoffTargetResolver` interface in §5.2 must be able
to return a client identifier, not only an E.164 number.

### 12.4 Prerequisites, and why this isn't testable yet

- A **WhatsApp-activated sender** with a messaging limit of ≥2,000
  business-initiated conversations per 24h, which requires **Meta Business
  Verification**. Our current WhatsApp number is a **Twilio sandbox** number
  (`crm-channel-ui-testing-guide.md:127`), which cannot do calling at all.
- Inbound calling is available in Malaysia; outbound is excluded in the USA,
  Canada, Egypt, Nigeria, Turkey and Vietnam — Malaysia is unaffected.
- Outbound is limited to 5 calls per recipient per 24h and needs renewed
  permission after 7 days; the recipient must consent before a business can
  call them.

**Bundle the Meta ask.** Business Verification plus a real WABA number is a
single Proton-side request that unlocks three things currently tracked as
separate blockers: Facebook/Instagram production (feedback #11), real-number
WhatsApp media testing (#25), and WhatsApp calling. Ask once.

## 13. Definition of done

A real inbound call can be transferred to a human who actually answers; the
ticket exists before the call ends and carries the transcript and a derived
status; that agent is skipped by WhatsApp routing until the call ends; the
recording is retrievable by an authorised user; every failure path has been
exercised at least once; and the coverage doc records exactly which of #21/#23/#27
are now real and which were deferred by the §5.2 decision.
