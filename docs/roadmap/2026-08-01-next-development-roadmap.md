# Next Development Roadmap

**Created:** 2026-08-01
**Source:** Full re-read of `docs/client-materials/Proton x Devoteam _ CRM System Update – 2026_07_28 09_58 WIB – Notes by Gemini.pdf`, cross-checked against the current codebase.
**Status:** Living tracker — update task checkboxes as work lands; add new sections rather than a new file when scope changes.

This is the backlog of work confirmed still open after the 2026-07-28 Proton x Devoteam demo call, sequenced by dependency and blocker status. Items already fully built this session (email escalation + PIC notification, Agent Channel Priorities) are not repeated here — see `docs/archive/` for their specs.

## Suggested sequence

1. Case categories & subcategories
2. RBAC / SLA / audit logs
3. Reporting & metrics extensions (depends on #2 for role-scoping)
4. Voice-note + image channel support
5. Knowledge base gaps + Copilot grounding bug
6. Dealer-forward explicit agent action
7. Voice/IVR (partly blocked on a client decision)

Not in this roadmap — external blocker, not our build queue: **Customer 360** (needs Proton's DNS/customer-360 system + vehicle data).

---

## 1. Case categories & subcategories

Client-requested, no blockers. Replace the current free-multi-label picker for "case type" with an enforced single main category + dependent subcategory.

- [ ] Design a fixed category taxonomy: one enforced main category + dependent subcategory list
- [ ] Add a `case_category`/`case_subcategory` custom-attribute pair to Chatwoot conversations, mutually exclusive at the main-category level
- [ ] Update the AI auto-classify tool (`classify_ticket_tool` in `backend/apps/backend/src/chatbot/features/chat/agents.py`) to populate main+sub instead of a flat label
- [ ] Agent-facing UI: single-select main category, subcategory list filtered by the chosen main category
- [ ] Migrate/backfill existing label-based categorization (decide: leave old labels as-is, or one-time migrate)

## 2. RBAC / SLA / audit logs

Spec already written 2026-07-27, never implemented. Highest-leverage item — unlocks role-scoped reporting in #3.

- [ ] Revisit `docs/superpowers/specs/2026-07-27-own-sla-audit-rbac-design.md` — the plan's premise (`provision_features.py` enabling Chatwoot's enterprise-locked `sla`/`audit_logs`/`custom_roles`) was never implemented
- [ ] Decide: build our own SLA/audit/roles system (as originally spec'd) vs. re-attempt enabling Chatwoot's native enterprise features now that the fork is more mature
- [ ] Land role-scoped visibility as a reusable primitive (needed again by #3)

## 3. Reporting & metrics extensions

Partly blocked — needs PRO-NET's example report visualizations before customization work can start.

- [ ] Add "cases fully AI-closed vs. escalated to human" as a first-class report metric (tracking already exists per-conversation; needs aggregation + a report view)
- [ ] Category-breakdown report (case volume by main category / department) — depends on #1
- [ ] Role-scoped report visibility — depends on #2
- ⏸ Native report customization / PowerBI export — **hold** until PRO-NET sends example report visualizations (explicit blocker from the meeting)

## 4. Voice-note + image channel support

One of PRO-NET's 3 explicitly named remaining gaps (with email, which is now done).

- [ ] WhatsApp inbound voice-note: transcribe (Gemini audio) before handing to the chat agent, same pattern as the existing voice/phone Gemini pipeline
- [ ] WhatsApp/web inbound image: wire Gemini's existing multimodal capability into the turn pipeline (currently unused product-side)
- [ ] Confirm Meta WhatsApp Business verification status (external dependency, same blocker noted for other channels)

## 5. Knowledge base gaps + Copilot grounding bug

- [ ] **Bug, not backlog** — fix Ask Copilot's grounding lag (it couldn't retrieve info the main WhatsApp agent could, per the demo); triage before adding new KB features
- [ ] Bulk FAQ upload (CSV)
- [ ] Auto-classify an uploaded document into individual FAQ entries (currently manual one-by-one)
- [ ] Document upload button was reported non-functional at demo time — verify/fix

## 6. Dealer-forward explicit agent action

Carried over from earlier session work (email escalation analysis).

- [ ] Human-agent-triggered "forward this case to a specific dealer" action, distinct from the automatic PIC-notify-on-escalation already built (`escalation_notifier.py`)

## 7. Voice/IVR

Mostly blocked on a client architecture decision.

- ⏸ DTMF-IVR vs. conversational-LLM — **client hasn't chosen**; not much to build until they do
- [ ] Agent auto-busy while on a call (skip them for new WhatsApp assignment) — small, independent, buildable now
- [ ] Wire AI-to-human call handoff to a real agent (currently demo-only)
- [ ] Call recording (flagged as a production-readiness gap, not a missing feature)

---

## Reference: full meeting action-item extract

Confirmed-working items (no action needed): Suggest-reply from KB, conversation summarization to private note, contact panel/merge, assistant persona settings, business-hours/inactivity/auto-close/resolution-grace settings, collaborators (multi-agent per inbox), channel/inbox setup, channel-source filtering in the inbox view, language auto-detect (one known flaky glitch, unresolved).

Business-track next steps (not engineering): Devoteam to consolidate all requirements into one proposal (target ~2026-08-04); Caroline pursuing Google program funding in parallel; PRO-NET owes example reports + case-category hierarchy examples.
