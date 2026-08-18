# In-CRM Agent Softphone — AI-First Call with Human Takeover — Design

**Date:** 2026-08-18
**Status:** Approved (brainstorm complete)
**Scope:** Let a human agent take over a live phone call **inside the Chatwoot
fork, in the browser**, when the Gemini Live phone bridge hands off. Replaces
today's transfer-to-a-real-telephone behaviour with a transfer to a browser
softphone owned by us.

## Context

A caller reaches the AI today over a real Twilio number: Twilio Media Streams ⇄
Gemini Live, bridged by `backend/apps/backend/src/chatbot/features/chat/phone/
bridge.py`. When the model calls `request_human_handoff`, `_attempt_transfer()`
redirects the live call to `<Dial><Number>` — a static PSTN hunt-group number
(`phone_handoff_target_number`). **The human answers on an actual telephone**,
with none of the CRM context they would have in Chatwoot, and nothing links the
person who answered back to the conversation the bridge opened.

The goal is that the same handoff instead rings the agent **in the CRM they are
already working in**, so they answer with the customer's history, the AI's
transcript, and the case record on screen.

### Why not Chatwoot's native Voice Channel

Upstream v4.15.1 does ship a Voice Channel that rings agents in-browser, and
`config/features.yml` gates it on a per-account bit flag (`channel_voice`,
`premium: true`) with **no runtime licence check** — so it would technically
switch on. But every server-side component lives under `enterprise/`
(`enterprise/app/controllers/twilio/voice_controller.rb`,
`enterprise/app/services/voice/**`), which is Chatwoot's **commercially
licensed** code. Running it in a productised multi-tenant offering requires a
paid self-hosted plan per tenant.

Building our own also keeps the AI bridge in the call path, which the native
channel would bypass entirely — the native channel owns the phone number and
answers the call itself, leaving no room for the Gemini-first flow that is the
whole point here.

### Decisions locked during brainstorming

1. **AI answers first, human takes over.** Not "route to a human immediately".
2. **Our own softphone**, as a fork patch — no Chatwoot `enterprise/` code.
3. **Ring the assigned agent first, then fan out** to everyone available.
4. **`<Dial><Client>` redirect, not `<Conference>`** — see "Rejected
   alternatives".

## Goals

- A handoff on a live call rings the right agent's browser inside the CRM.
- The agent sees who is calling, why the AI escalated, and a link to the
  conversation the bridge already opened — **before** deciding to accept.
- Accept / reject / mute / hang up, with a visible call timer.
- If nobody takes it, the caller gets today's bilingual apology and the case is
  still tagged `unanswered_handoff` — i.e. **no regression** on the existing
  fallback.

## Non-goals

- The AI listening in, transcribing, or coaching **during** the human portion.
  The Media Stream ends at redirect. (See "Future: conference mode".)
- Agent-initiated **outbound** calls from the CRM.
- Replacing `deploy/twilio/ivr-studio-flow.json`. The IVR is a separate
  front door and is untouched.
- Call recording of the human portion beyond what `<Dial record>` gives us.
- Warm transfer (AI announcing the caller to the agent before connecting).

## Architecture

### Call flow

```
Customer ──PSTN──► Twilio number
                     │
                     ▼
          /webhooks/phone/incoming  ──►  <Connect><Stream>
                     │
                     ▼
          PhoneBridge  ⇄  Gemini Live          (today, unchanged)
                     │
          model calls request_human_handoff
                     │
                     ▼
          _attempt_transfer()
                     │  resolve() → HandoffTarget
                     ▼
          CallControl.redirect(call_sid, dial_twiml(...))
                     │        ends the Media Stream; finalize() opens the ticket
                     ▼
   STAGE 1   <Dial action=/webhooks/phone/dial-status timeout=N>
               <Client><Identity>agent_17</Identity>
                       <Parameter name="conversation_id" .../>
                       <Parameter name="reason" .../></Client>
             </Dial>
                     │
        ┌────────────┴─────────────┐
    answered                   no-answer/busy/failed
        │                           │
        ▼                           ▼
   agent talks            /webhooks/phone/dial-status
   to customer                      │
        │                    STAGE 2  <Dial> with one <Client> per
        ▼                            available+registered agent (≤10)
   dial-status "completed"           │
   → ACW (existing)          ┌───────┴────────┐
                         answered          nobody
                             │                │
                             ▼                ▼
                        agent talks    apology TwiML + `unanswered_handoff`
                                       (today's exact behaviour)
```

The redirect, the action URL, the signature verification, the apology, and the
`unanswered_handoff` tagging **already exist and are unchanged**. Stage 2 is a
new branch inside a handler that already runs on exactly this event.

### What already exists vs. what is new

| Piece | Status |
|---|---|
| `CallControl.redirect()` | exists |
| `dial_twiml()` `kind="client"` branch | **written, currently unreachable** |
| `/webhooks/phone/dial-status` (signature-verified, apology, tagging, ACW) | exists |
| `HandoffTargetResolver` as a documented swappable seam | exists by design |
| `ConversationLogPort.get_conversation_assignee(ticket_id)` | exists |
| `ConversationLogPort.find_conversation_ticket(session_id)` | exists |
| `PresenceFetcher` → `AgentRecord(id, name, availability_status, email)` | exists |
| Per-agent authenticated fork→backend calls (`adminRequest`) | exists |
| RBAC `require_permission` + `useProtonPermissions` | exists |
| Always-on background behaviour mounted from `Sidebar.vue` (patch 0057) | exists as a pattern |
| Voice token with `incoming_allow=True` and a stable identity | **new** |
| Softphone registration registry | **new** |
| `AgentClientResolver` (assignee → `<Client>`) | **new** |
| Stage-2 fan-out TwiML + the dial-status branch that emits it | **new** |
| Softphone UI in the CRM | **new** |

## Components

Five units, each independently testable.

### 1. `features/chat/phone/agent_token.py` — identity and the token

`POST /voice/agent/token`, authenticated exactly like the existing admin
endpoints: the fork forwards the devise_token_auth triplet
(`x-chatwoot-access-token` / `x-chatwoot-client` / `x-chatwoot-uid`), the
backend validates it against Chatwoot's `/auth/validate_token`, then
`require_permission("voice.answer")`.

```python
def mint_agent_voice_token(settings, chatwoot_user_id: int) -> str:
    token = AccessToken(..., identity=f"agent_{chatwoot_user_id}",
                        ttl=settings.phone_agent_token_ttl_seconds)
    token.add_grant(VoiceGrant(
        outgoing_application_sid=settings.twilio_twiml_app_sid,
        incoming_allow=True,          # <- the actual unlock
    ))
```

**The identity is derived server-side from the validated session and is never
accepted from the request body.** A client-supplied identity would let any
authenticated agent register as another agent and intercept their calls. This
is the single most security-sensitive line in the design.

The existing caller-side `mint_voice_token` (`token.py`, `incoming_allow=False`)
is **left exactly as it is** — the demo customer softphone must not gain the
ability to receive calls.

New permission in `features/authz/seed.py`:

```python
"voice.answer": "Answer transferred phone calls in the browser softphone",
```

Not granted by default to any existing role; an operator ticks it on. This
matters because a Voice grant is a **billable capability** on your Twilio
account, not just a UI affordance.

### 2. `features/chat/phone/softphone_registry.py` — who can actually be rung

Chatwoot availability (`online`/`busy`/`offline`) says whether an agent is *at
work*. It does **not** say whether a browser tab currently holds a registered
Twilio `Device`. A `<Client>` dial to an unregistered identity fails
immediately, so ringing on availability alone would burn a stage on a dead
identity.

A small Firestore-backed registry, following `PresenceEventStore`'s shape (the
backend runs multiple workers, so an in-process dict is wrong):

```python
async def heartbeat(agent_id: int) -> None      # called by the browser every 30s
async def unregister(agent_id: int) -> None     # on Device unregister / tab close
async def registered_ids() -> set[int]          # entries newer than the TTL
```

TTL = `phone_softphone_registration_ttl_seconds` (default 90 — three missed
heartbeats). Entries are advisory, never authoritative: see "A stale registry
must not strand a caller".

### 3. `features/chat/phone/agent_client_resolver.py` — who gets rung

A second implementation of the interface `HandoffTargetResolver`'s docstring
already anticipates ("a second implementation of the same
`resolve() -> HandoffTarget | None` interface, added once that decision
lands"). Stage 1 only:

1. The conversation id — **read from `PhoneBridge.ticket_id`, which
   `_create_ticket_at_start` already cached at call start**, not re-fetched via
   `find_conversation_ticket`. This is the inline audio path; a redundant
   Chatwoot round trip here is dead air. `ticket_id` is unset when the
   create failed or the tenant has `chatwoot_enabled=False`, in which case
   stage 1 resolves `None` without any I/O at all.
2. `get_conversation_assignee(ticket_id)` → the assigned agent id, or `None`.
3. If assigned **and** in `registered_ids()` → `HandoffTarget(kind="client",
   value=f"agent_{id}")`.
4. Otherwise → `None`, which makes `_attempt_transfer` fall through to the
   **existing** `HandoffTargetResolver` (the PSTN hunt group). Nothing
   regresses for a tenant that never enables this.

Because it reads bridge state, this resolver is constructed per-call by the
bridge rather than being a process-wide singleton — unlike
`HandoffTargetResolver`, which only needs settings and ports.

Composition, not replacement: a `ChainedResolver` tries
`[AgentClientResolver, HandoffTargetResolver]` in order and returns the first
non-`None`. Each resolver keeps its own gate (`phone_agent_softphone_enabled`,
`phone_handoff_enabled`) so either can be run alone.

**Two required changes to existing code, both small and both load-bearing:**

- `dial_twiml()` currently emits the shorthand `<Client>identity</Client>`,
  which has no place for parameters. It must emit the long form
  `<Client><Identity>…</Identity><Parameter …/></Client>` for `kind="client"`
  so the ringing browser receives context (see §4). The `<Number>` branch is
  untouched.
- `HandoffTargetResolver.resolve()` refuses to resolve at all when
  `phone_handoff_caller_id` is empty. That guard exists because a PSTN
  `<Number>` dial with a `client:` caller id is Twilio error 13214 — it is
  **PSTN-specific** and must not apply to the `<Client>` path, which has no
  such restriction. Left as-is, it would silently disable the whole feature for
  any tenant that never configured a PSTN caller id.

### 4. Fork patch `0068-agent-softphone.patch` — the UI

Follows patch 0057's established pattern for always-on behaviour: a composable
holding the state, mounted once from `Sidebar.vue` (present on every dashboard
page, so the phone rings wherever the agent is — a per-conversation component
would only ring while they happened to be looking at that conversation).

New files:

- `app/javascript/dashboard/api/protonVoice.js` — `adminRequest`-style calls to
  `/voice/agent/token` and `/voice/agent/heartbeat`.
- `app/javascript/dashboard/composables/useProtonSoftphone.js` — owns the
  `@twilio/voice-sdk` `Device`: fetch token → `device.register()` → heartbeat
  timer → `device.on('incoming', …)` → expose `incomingCall`, `activeCall`,
  `status`, `elapsed`, and `accept/reject/mute/hangup`. Re-mints on
  `tokenWillExpire`.
- `app/javascript/dashboard/components-next/softphone/ProtonSoftphonePanel.vue`
  — the ring/in-call panel.

Modified: `Sidebar.vue` (mount the panel, gated on
`hasFeature('agent_softphone')` **and** `useProtonPermissions` →
`voice.answer`).

Context reaches the ringing browser through Twilio's `<Parameter>` children,
which surface as `call.customParameters`: `conversation_id`, `caller_number`,
`reason`, `summary` (the AI's own handoff reason, already captured in
`bridge.handoff`). The ring UI shows the number and the reason, and links
straight to the conversation.

The panel reuses the fork's existing toast/sound/desktop-notification helpers
(`helper/protonAlerts.js`, added by patch 0057) rather than adding a second
notification stack.

### 5. Call outcome → CRM

`/webhooks/phone/dial-status` already handles `completed` (ACW entry) and
`no-answer`/`busy`/`failed` (apology + `unanswered_handoff`). Two additions:

- **Stage 2.** On a non-`completed` status where the call has not yet fanned
  out, return fan-out TwiML instead of the apology: one `<Client>` per agent in
  `registered_ids() ∩ {availability_status == "online"}`, **capped at 10**
  (Twilio's per-`<Dial>` noun limit). The apology only plays when stage 2 also
  fails, or when stage 2 has no one to ring.
- **Who answered.** On `completed`, note the answering agent and the duration
  on the conversation. Twilio's callback carries the answering leg, so a
  `<Client>` identity maps back to a Chatwoot user id by construction
  (`agent_<id>`).

Stage tracking rides on the `action` URL rather than server state, so a
redelivered callback cannot advance a caller past a stage and the handler stays
stateless — matching how it already resolves everything else from the
`CallSid`. It must use **two distinct paths**, not a query parameter:

| Stage | `action` URL |
|---|---|
| 1 (assigned agent) | `/webhooks/phone/dial-status` (existing) |
| 2 (fan-out) | `/webhooks/phone/dial-status/fanout` (new) |

A `?stage=2` query string would break authentication. Twilio signs the **exact
URL including its query string**, but the existing verification reconstructs
the URL as `f"{twilio_webhook_base_url}/webhooks/phone/dial-status"` and drops
any query — so the signature would never match, the handler would 401, and
Twilio would drop a caller who is still on the line. Separate paths keep that
reconstruction trivially correct and make route registration explicit.

The fan-out route registers only when `phone_agent_softphone_enabled` is on,
mirroring how `/webhooks/phone/dial-status` is registered only when
`phone_handoff_enabled` is on.

## Failure modes

The rule this feature is designed around: **a live caller is on the line, so
every failure must degrade to something audible, never to silence.**

| Failure | Behaviour |
|---|---|
| No assignee on the conversation | Stage 1 resolves `None` → straight to stage 2 |
| Assignee assigned but softphone not registered | Stage 1 skipped → stage 2 |
| Assignee registered but tab closed since last heartbeat | Twilio reports `no-answer`/`failed` fast → stage 2 |
| Nobody registered at all | Stage 2 has no nouns → falls back to the PSTN hunt group if `phone_handoff_enabled`, else the apology |
| Registry read fails | Treated as empty, logged, → stage 2 / PSTN / apology. **Never raises into the audio pump** |
| `/voice/agent/token` fails in the browser | Panel shows "softphone unavailable", agent keeps working; that agent is simply not in the fan-out |
| Token expires mid-call | Twilio keeps an in-progress call alive; `tokenWillExpire` re-mints for the next one |
| Two tabs open for one agent | Both register the same identity; Twilio rings both, first accept wins, the other cancels. Acceptable — no dedup needed |
| Agent rejects | Twilio reports `no-answer`-class status → stage 2, so a reject is not a dead end |

### A stale registry must not strand a caller

The registry is an **optimisation**, never a gate on whether the caller gets
help. Every path out of it leads to another dial or to the apology; none of
them ends in silence. Concretely: `registered_ids()` returning stale, empty, or
wrong data can cost one wasted ring timeout or one skipped stage — it can never
prevent the PSTN fallback or the apology from running, because those are
downstream of the `dial-status` callback that Twilio fires regardless.

### Concurrency

`_attempt_transfer()` runs **inline inside `pump()`**, the sole Gemini→Twilio
audio forwarder — a slow call there is dead air the caller actually hears. The
existing bounds (`_HANDOFF_RESOLVE_TIMEOUT_SECONDS`, and `CallControl`'s
shorter SDK-level HTTP timeout) apply unchanged to the new resolver, so its two
lookups must be bounded and fail-open.

`AgentClientResolver` therefore follows `HandoffTargetResolver.prefetch()`'s
precedent: **warm the assignee and registration lookups as a detached task at
call start**, so the handoff path reads a cached answer instead of making two
round trips while audio is stalled. A cold cache falls back to the inline
lookup, within the existing bound.

`_transfer_dialed` already suppresses a second `request_human_handoff` from
restarting a ring from zero; that guard covers the new path unchanged.

## Security

- Identity is server-derived from a validated Chatwoot session. Never from the
  request body.
- `voice.answer` is a distinct permission, off by default.
- The token endpoint reuses the existing per-agent rate limiter
  (`RateLimiter(phone_token_rate_limit, phone_token_rate_window_seconds)`)
  already applied to the caller-side token route.
- Token TTL stays short (default 300s) with browser-side re-minting.
- The caller-side token keeps `incoming_allow=False`.
- `<Parameter>` values are TwiML attribute content and must go through the same
  `quoteattr`/`escape` treatment `dial_twiml` already uses — the `reason` and
  `summary` strings are **model-generated**, so they are untrusted input into
  an XML document.

## Configuration

New settings in `platform/config.py` (and `deploy/tenants/example.env` —
names must match verbatim):

| Setting | Default | Meaning |
|---|---|---|
| `phone_agent_softphone_enabled` | `false` | Master gate for stage 1 + stage 2 |
| `phone_agent_token_ttl_seconds` | `300` | Agent Voice token TTL |
| `phone_softphone_registration_ttl_seconds` | `90` | Registry staleness bound |
| `phone_agent_ring_timeout_seconds` | `20` | Stage-1 `<Dial timeout>` |
| `phone_fanout_ring_timeout_seconds` | `25` | Stage-2 `<Dial timeout>` |
| `phone_fanout_max_agents` | `10` | Twilio's per-`<Dial>` noun limit |

Fork feature flag `agent_softphone`, via patch 0058's unified mechanism — the
flag list is built from `PROTON_FEATURES` **plus** the backend flags that are
set, so the client gate follows the server gate automatically.

Existing Twilio settings are reused unchanged: `twilio_account_sid`,
`twilio_api_key_sid`, `twilio_api_key_secret`, `twilio_twiml_app_sid`,
`twilio_auth_token`, `twilio_webhook_base_url`.

## Build constraint: adding `@twilio/voice-sdk` to the fork

`deploy/chatwoot-fork/Dockerfile` runs **`pnpm install --frozen-lockfile`**.
Adding a dependency to `package.json` in a patch without a matching
`pnpm-lock.yaml` entry **fails the image build**, and regenerating a pnpm
lockfile needs the upstream tree plus network access.

**Chosen approach:** add a single line to the Dockerfile after the frozen
install:

```dockerfile
RUN pnpm install --frozen-lockfile
RUN pnpm add @twilio/voice-sdk@2.18.3             # new
RUN pnpm exec vite build
```

Upstream's dependency graph stays reproducible; exactly one dependency is added
explicitly and visibly, pinned. Cloud Build has network access, which is where
this image is built anyway (never on the prod VM, never on arm64).

**Fallback if that proves unacceptable:** vendor the SDK's UMD bundle as a
static file inside the patch and import it directly — no lockfile, no
build-time network, at the cost of manual version bumps.

Note the runtime stage only copies `public/vite`, `vueapp.html.erb`, and
`.git_sha`. This design is frontend-only on the Chatwoot side (all server logic
lives in our FastAPI backend), so **no new `COPY` line is needed** — a
constraint worth preserving, since a Rails-side change here would silently not
ship.

## Testing

**Backend** (`pytest`, co-located, existing conventions):

- `test_agent_token.py` — identity is derived from the validated session and a
  body-supplied identity is ignored; `incoming_allow=True`; `voice.answer`
  enforced; rate limit applies; the caller-side token still has
  `incoming_allow=False`.
- `test_agent_client_resolver.py` — assignee + registered → `client` target;
  assignee not registered → `None`; no assignee → `None`; port failure →
  `None`, no raise; chain falls through to the PSTN resolver; flag off →
  `None` without any lookup; unset `ticket_id` → `None` with **zero** port
  calls (asserted, since the cost of that round trip is the reason it's cached).
- `test_softphone_registry.py` — heartbeat/TTL expiry; store failure returns
  empty rather than raising.
- `test_twiml.py` (extend) — `<Client>` long form with escaped
  `<Parameter>` values; a `reason` containing `"` / `<` / `&` produces valid
  XML; the `<Number>` branch is byte-identical to before; `timeout` coerced to
  `int`.
- `test_handoff.py` (extend) — stage 1 → stage 2 → apology; a replayed stage-1
  callback does not skip stage 2; `completed` still enters ACW; the fan-out
  route verifies its signature against **its own** path (a regression test for
  the query-string trap above); the route is absent when
  `phone_agent_softphone_enabled` is off; fan-out is capped at
  `phone_fanout_max_agents`.

**Manual** — the parts an automated suite genuinely cannot cover, as an
extension of `backend/docs/testing/phone-channel-package-c-verification.md`:
real ring in a real browser, accept/reject/mute, two tabs, tab closed mid-ring,
agent rejects → fan-out, nobody answers → apology.

## Rollout

All flags default off; with `phone_agent_softphone_enabled=false` the behaviour
is byte-identical to today. Per memory, **proton is the only tenant in scope** —
`default` and `wahchan` are not to be touched.

1. Backend units 1–3 + 5 behind the flag, deployed with the usual
   `docker compose … up -d --build backend`.
2. Fork patch 0068 built via Cloud Build for amd64, pushed to Artifact
   Registry, pulled on the VM.
3. Grant `voice.answer` to one test agent, enable the flag, run the manual
   plan.
4. Widen the permission once the manual plan passes.

## Rejected alternatives

**`<Conference>` instead of `<Dial><Client>`.** Redirect the caller into a
conference, add the agent as a participant, and keep a Media Stream attached so
Gemini keeps transcribing through the human conversation. This is genuinely
better on the merits — live transcript during the human portion (feeding the
existing `transcript_classifier` and the CRM timeline), warm transfer, whisper/
coaching, and the agent can drop without killing the call. It was rejected
**for now** because it requires building conference and participant lifecycle
management from scratch, restructures `_attempt_transfer` and `dial-status`
rather than extending them, and roughly doubles per-minute Twilio cost — all
before anyone has proven an agent can pick up in the browser at all. The
`resolve() -> HandoffTarget | None` seam is exactly where it slots in later.

**Chatwoot's native Voice Channel.** See "Context".

**Ring everyone immediately, no stage 1.** Simpler, and it was the first
instinct. Rejected because a returning caller reaching the agent who already
knows their case is the main experiential win over the current hunt-group
behaviour.

**Agent-initiated pickup from a "waiting calls" list.** No presence tracking
needed and no interruption mid-chat, but the caller waits on hold with no
guarantee anyone picks up — strictly worse for the person on the phone.

## Future: conference mode

The natural follow-up, and the reason the resolver seam is preserved: a
`ConferenceHandoffTarget` plus a conference-flavoured TwiML builder, swapped in
behind the same interface. It unlocks live transcript during the human portion,
warm transfer, supervisor whisper/barge, and returning the caller to the AI
after the human leaves. Its own spec.
