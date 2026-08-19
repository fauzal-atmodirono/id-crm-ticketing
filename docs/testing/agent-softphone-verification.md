# Agent softphone — manual verification plan

The automated suite (backend unit/integration tests across the token
endpoint, the agent registry, the `<Dial>`/fan-out resolver, and the TwiML
routing) covers token shape, resolver branches, TwiML generation, and
routing logic. It cannot cover a real browser ringing on a real Twilio call
— the same gap `backend/docs/testing/phone-channel-smoke-test.md` documents
for the phone bridge itself. This plan closes that gap.

Test in four tiers. Each one is cheap and catches a different class of
failure, and each is a gate on the next — **do not skip ahead to live calls**,
because a misconfigured tenant produces failures that look exactly like code
bugs and will cost you an afternoon.

| Tier | What it proves | Cost |
|---|---|---|
| 0 — automated suite | The logic is correct | 3 min, no setup |
| 1 — config + deploy | The tenant can actually boot with the feature on | 10 min |
| 2 — smoke, no phone call | Tokens mint, browsers register, the flag reaches both services | 5 min |
| 3 — live scenarios A–M | It works on a real call | ~1 hour, 2 people |

---

## Tier 0 — automated suite

From `backend/apps/backend`:

```bash
GEMINI_API_KEY=test-dummy-key GOOGLE_API_KEY=test-dummy-key .venv/bin/pytest src/ -q
```

Expected: **3091 passed, 2 skipped**.

The env vars are not optional. Without a Gemini credential, five wiring
modules fail at *collection* with `ValueError: No API key was provided` and
you get ~107 errors that have nothing to do with this feature. Any dummy
string works — nothing calls Gemini in the suite.

## Tier 1 — configuration and deploy

### The flag chain (the app REFUSES to boot if this is wrong)

```bash
PHONE_TRANSCRIPT_LIVE_ENABLED=true     # required by handoff
PHONE_HANDOFF_ENABLED=true             # required by the softphone
PHONE_AGENT_SOFTPHONE_ENABLED=true     # the feature
RBAC_ENABLED=true                      # + RBAC_DATABASE_URL
```

These dependencies are enforced at startup on purpose. `RBAC_ENABLED` is not
optional: the agent's identity is derived from their validated Chatwoot
session, so with RBAC off the token endpoint returns 401 by design, and the
softphone can never register.

### Twilio credentials

The AI bridge's existing `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` /
`TWILIO_WEBHOOK_BASE_URL` are not sufficient. The softphone additionally needs
**`TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET`, `TWILIO_TWIML_APP_SID`** —
those three mint the Voice access tokens. `TWILIO_WEBHOOK_BASE_URL` must be
the **public HTTPS** base, because signature verification reconstructs the
public URL; behind Caddy the internal one will not match and every callback
401s mid-call.

### The PSTN fallback must be real

```bash
PHONE_HANDOFF_TARGET_NUMBER=+60...     # a REAL E.164 number
PHONE_HANDOFF_CALLER_ID=+60...         # required, or the PSTN leg won't resolve
```

The app refuses to boot on a placeholder. Note `deploy/twilio/README.md` uses
`+60300000001` as its example and **that exact number is on the blocklist** —
copying it from there is the most likely way to fail this step.

### Deploy

```bash
# proton only. Do NOT point default/wahchan at this.
CHATWOOT_IMAGE=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom-rc10

docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env \
  pull chatwoot-rails chatwoot-sidekiq
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env \
  up -d --force-recreate chatwoot-rails chatwoot-sidekiq
docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env \
  up -d --build backend agent
```

Then grant **`voice.answer`** in the Roles admin UI to the agents who should
receive calls. Administrators receive it automatically, so any admin with a
CRM tab open joins the ring rotation — untick it there if that is not what you
want.

**Boot gate:** `docker compose logs backend | tail -30`. A refusal here names
the exact variable; fix it before going further.

## Tier 2 — smoke test (no phone call needed)

This catches every configuration failure before you waste call time.

1. **Token mints.** Log in as an agent with `voice.answer`. In DevTools →
   Network, expect `POST /voice/agent/token` → **200**, then
   `POST /voice/agent/heartbeat` → 200 roughly every 30 s while the tab
   stays open. Backend logs show `softphone_token_issued` with that agent id.
2. **No panel is visible.** Correct — the panel only renders on a ring.
3. **Permission gate.** Repeat as an agent *without* `voice.answer`: no token
   request should fire at all.
4. **Registry is populated.** After a heartbeat, the Firestore collection
   `softphone_registrations` should hold a doc `agent-<id>` with a fresh `at`.

If all four pass, the plumbing is correct and any remaining failure is
behavioural — which is what Tier 3 is for.

## Tier 3 — live scenarios

Each scenario states setup, action, and the observable pass condition. Run
them in order where one depends on the state a prior one leaves behind
(A before C).

**Run these two first** — they cover the two defects this build actually
fixed, and a regression in either is silent:

- **C → D** — the assigned agent's browser rings and they can talk. Before the
  final review, `prefetch()` cached a "no ticket yet" answer and the feature
  was inert: stage 1 never rang and every handoff fell through to PSTN while
  every test passed. If stage 1 dials `<Number>` instead of ringing a browser,
  that bug is back.
- **M** — a non-assignee answers the fan-out. Check the **workforce
  dashboard**, not the logs: after-call-work must land on whoever answered.

| # | Scenario | Setup | Action | Pass condition |
|---|---|---|---|---|
| A | Agent opens the CRM with `voice.answer` granted | Agent has `voice.answer`; flag + feature on | Log in, open any dashboard page | Panel is hidden (no active call); backend logs `softphone_token_issued` for this agent; a heartbeat request lands roughly every 30s while the tab stays open |
| B | Agent without `voice.answer` | Agent lacks `voice.answer` | Log in, open any dashboard page; separately, call `POST /voice/agent/token` directly against the backend for this agent | No panel renders and no token request fires from the browser; the forced direct call returns `403` |
| C | Call in, AI answers, asks for a human; conversation assigned to the open agent | Agent A's tab open and registered (scenario A state) | Call the Twilio number; let the AI ask to be handed to a human; confirm the conversation is assigned to agent A | Agent A's browser rings within ~2s of the handoff, showing the caller's number and the AI's escalation reason |
| D | Agent accepts | Continue from C | Agent A clicks Accept | Two-way audio is audible both directions; the in-call timer runs; the backend's `dial-status`/fan-out callback receives `completed`; an ACW (after-call-work) entry is logged for agent A |
| E | Agent rejects | Repeat C with a fresh call | Agent A clicks Reject | The fan-out rings the other available agents next; agent A's phone does not ring again for this call |
| F | Assignee's tab closed mid-call | Repeat C, then close agent A's browser tab before answering | Wait | Stage 1 (ringing the assignee) fails fast (no heartbeat, no live registration); the fan-out proceeds to ring other available agents within the stage-1 timeout |
| G | Nobody registered | No agent tabs open/registered anywhere | Call the Twilio number and let it escalate | The call falls through to the PSTN hunt group exactly as it did before this feature existed |
| H | Nobody answers anywhere | At least one agent registered but nobody picks up (including the PSTN hunt group) | Call in, let every leg ring out unanswered | The bilingual apology message plays; the conversation is tagged `unanswered_handoff` |
| I | Two tabs open for one agent | Agent A opens the CRM in two separate browser tabs (both registered) | Trigger an inbound handoff assigned to agent A | Both tabs ring; whichever tab's user clicks Accept first wins the call; the other tab stops ringing cleanly (no lingering ring state, no error) |
| L | Two different agents both hit Accept at the same moment during fan-out | Two agents registered and both ringing for the same fanned-out call | Both agents click Accept within the same second | Exactly one agent gets two-way audio; the other agent's panel shows the neutral "Call ended" state for ~3 seconds before clearing, rather than the panel vanishing without explanation; the customer hears one continuous call with no glitch, silence, or double-connect |
| M | A non-assignee answers the fan-out | Call assigned to agent A; agent A does not answer; agent B (a different available agent) answers during fan-out | Let the call reach fan-out and have agent B accept | After-call-work is applied to agent B (the agent who actually answered), not agent A; agent A's availability/status is untouched. Verify this on the workforce dashboard, not just by reading logs |
| J | Mute / hang up | Agent on an active call (from D) | Click Mute, speak, then Unmute; then click Hang up | While muted, the caller hears nothing from the agent; unmuting restores audio; hanging up ends the call for both parties immediately |
| K | Flag off | `PHONE_AGENT_SOFTPHONE_ENABLED=false` (or `agent_softphone` absent from `PROTON_FEATURES`) on the tenant | Call the Twilio number and let it escalate to a human; separately, load the CRM as any agent | The handoff dials the PSTN number directly; there is no softphone route reachable from the browser (no panel, no token calls); behavior is identical to before this feature existed |

## Notes for whoever runs this

- Scenarios C–F, I, L, and M require a live Twilio call each; budget call
  time accordingly rather than trying to simulate them from curl alone.
- For L and M specifically, coordinate two people (or two browser
  profiles/devices) so both agents can act within the same few seconds.
- Record pass/fail per row plus a timestamp and the tenant tested against.
  A failed row should capture the backend log lines around the event
  (`softphone_token_issued`, the dial-status/fan-out callback, and any ACW
  log) since the browser side alone will not show why a step failed.
- Known gap this plan does not attempt to resolve: the losing side of a
  race in scenario L cannot be told *why* its call ended (a colleague
  answered vs. the caller hung up) — Twilio's SDK does not distinguish the
  two and this fork has no push channel to ask the backend. The pass
  condition for L only requires the neutral "Call ended" state, not a
  specific reason string.

## Troubleshooting — symptom to cause

Every log name below is greppable in the backend container
(`docker compose logs backend | grep <name>`).

| Symptom | Almost certainly |
|---|---|
| `POST /voice/agent/token` → **404** | `PHONE_AGENT_SOFTPHONE_ENABLED` is false in the tenant env, or the backend was not rebuilt |
| → **401** | `RBAC_ENABLED` is false, or the browser session triplet is not reaching the backend |
| → **403** | The agent lacks `voice.answer` |
| No token request fires at all | The **client** flag never reached Rails. `backend` receives the tenant env via `env_file`, but `chatwoot-rails` needs the explicit `environment:` passthrough — check `PHONE_AGENT_SOFTPHONE_ENABLED` in `docker-compose.tenant.yml` and that you recreated `chatwoot-rails`, not just `backend` |
| Handoff dials the PSTN number, browser never rings | `agent_client_assignee_not_registered` → nobody registered (check heartbeats). If the registry has the agent, this is the inert-prefetch regression — treat as a **Critical** |
| Browser rings but accepting gives no audio | Browser mic permission, or the tab is not on HTTPS |
| Every dial-status callback 401s | `TWILIO_WEBHOOK_BASE_URL` is not the public HTTPS base. Look for `phone_dial_status_signature_invalid` / `phone_fanout_signature_invalid` |
| Fan-out rings nobody | `phone_fanout_nobody_available` → no agent is both `online` in Chatwoot **and** registered. `busy`/`offline` are excluded by design |
| Some agents never ring in a big team | `phone_fanout_capped` — Twilio's hard 10-noun limit. The dropped agents are named in that log line |
| Call drops right at handoff | `phone_fanout_no_action_url_configured` → `TWILIO_WEBHOOK_BASE_URL` is empty |
| Wrong agent goes into wrap-up | `phone_dial_status_acw_*` → the answering-agent lookup failed and fell back to the assignee |
| Caller hears the apology when agents were free | Work backwards: `phone_fanout_dialing` (did stage 2 run?) → `phone_fanout_nobody_available` → `phone_dial_status_unanswered` |
