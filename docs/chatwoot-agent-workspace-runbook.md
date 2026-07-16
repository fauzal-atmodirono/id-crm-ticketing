# Chatwoot-native agent workspace — runbook (roadmap item 4)

**Decision:** for the "unified agent view + new-inbound alerting" requirement we
**standardise on the Chatwoot agent workspace** rather than build a bespoke Vue
inbox. Chatwoot already ships a unified agent inbox, assignment, presence, and
desktop/sound notifications — re-implementing that in our frontend would be
redundant. This runbook is how the CRM team operates and how to finish the
light configuration.

## How the flow reaches an agent

1. Customer talks to the AI in **our** frontend / WhatsApp / (email — see below).
2. On escalation the backend opens a **Chatwoot conversation in the API-channel
   inbox `Proton Demo` (id 3)** with the customer message seeded, an AI-summary
   private note, priority, and labels `ai-escalation,escalate` + the AI
   classification dims (`category_*`, `subcat_*`, `division_*`, `sla_*`).
3. The conversation appears in the Chatwoot **agent inbox** — this *is* the
   unified view. The agent replies there:
   - **web/WhatsApp customer:** the reply streams back to the customer via our
     SSE bridge (already wired).
   - **email customer:** the reply goes out as an email (see the email inbox
     setup, roadmap Part 1).
4. Agent clicks **Resolve** in Chatwoot → the `conversation_status_changed`
   webhook resumes the AI for that session and appends a status-transition entry
   to the audit log.

## New-inbound alerting (native)

Enable Chatwoot's built-in notifications instead of building our own:

- **Per agent:** Profile → Notifications → enable **Email** + **Push** for
  "A conversation is assigned to you" and "A new message in an assigned
  conversation". Toggle the browser **Enable push notifications** banner.
- **Desktop/sound:** Chatwoot plays an audio alert and shows a desktop
  notification for new/assigned conversations when the tab is in the background
  (Profile → Notifications → Audio).
- These cover the BRD's "pop-up notification / system alert on new inbound".

## Finish the light configuration

1. **Create a Proton team** and route escalations to it (so it's not mixed with
   the shared instance's other tenants):
   - Chatwoot → Settings → Teams → **Add team** "Proton" → add the Proton agents.
   - Note its numeric id and set `CHATWOOT_AGENT_TEAM_ID=<id>` on the backend
     (Cloud Run env). The adapter then auto-assigns every escalation to that team.
   - *Current state:* only team id 1 "wah chan" exists (the other tenant), so
     `chatwoot_agent_team_id` is left `0` (unassigned) until the Proton team is made.
2. **Agent-assist FAQ panel** (`apps/chatwoot-agent-app`): Chatwoot → Settings →
   Integrations → **Dashboard Apps** → add name "FAQ Assist" + the hosted iframe
   URL. Backend prerequisites (now wired): the Chatwoot origin is in the CORS
   allowlist (`frontend_origins`); set `QA_API_KEY` on the backend and pass the
   same value to the app via `?apiKey=...` for the thumbs-feedback endpoint. See
   `apps/chatwoot-agent-app/README.md`.

## Mapping from the old Zendesk workflow

| Old (Zendesk) | Now (Chatwoot) |
|---|---|
| Zendesk agent workspace / Support tickets | Chatwoot inbox 3 conversations |
| Sunshine live chat back to our UI | Chatwoot agent reply → SSE bridge (unchanged) |
| Zendesk triggers/notifications | Chatwoot native email/push/desktop/sound |
| Zendesk macros/ZAF FAQ sidebar | Chatwoot Dashboard App (`apps/chatwoot-agent-app`) |
| Zendesk ticket tags (category/sla) | Chatwoot conversation labels (`category_*`, `sla_*`, …) |

## Not in scope here (long-term)

Roster/presence, per-agent channel priority, and status-based polling assignment
(BRD block B) are a larger build; Chatwoot's native assignment + teams is the
interim. Revisit Chatwoot's paid routing/omnichannel add-ons vs a bespoke engine
when block B is scheduled.
