# Agent softphone — manual verification plan

The automated suite (backend unit/integration tests across the token
endpoint, the agent registry, the `<Dial>`/fan-out resolver, and the TwiML
routing) covers token shape, resolver branches, TwiML generation, and
routing logic. It cannot cover a real browser ringing on a real Twilio call
— the same gap `backend/docs/testing/phone-channel-smoke-test.md` documents
for the phone bridge itself. This plan closes that gap.

Prerequisites: a tenant with `PHONE_AGENT_SOFTPHONE_ENABLED=true`,
`agent_softphone` in that tenant's `PROTON_FEATURES`, a live Twilio number
routed through the phone bridge, and at least two agent accounts — one
granted the `voice.answer` RBAC permission, one not.

Each scenario below states setup, action, and the observable pass
condition. Run them in order where a scenario depends on the state a prior
one leaves behind (e.g. A before C).

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
