# Logisly UC3: PM agent on Jira with Logisly's PRD format (design)

Date: 2026-11-12. Branch: `logisly-demo`. Builds on the existing UC3 agent in
`apps/logisly-agents/logisly_agents/uc3/` (ADK, Cloud Run, Gemini Enterprise
front end; plan `docs/superpowers/plans/2026-11-06-logisly-uc2-uc3-adk-agents.md`).

## Why

Logisly sent their own PRD (RFQ dispatch, Jira stories LOG-3284..3339) and said
they track work in Jira. The UC3 demo still writes our invented PRD template and
proposes issues on a mock GitHub, and its sample topic (driver-app AI assistant)
is unrelated to what they saw in UC1. The deck already promises Jira and their
PRD format. The demo must match the deck.

## Decisions (user, 2026-11-12)

- **Mock Jira**, not a real Jira site. It lives in ADK session state like the
  mock GitHub did.
- **The PRD is published as a Google Doc.**
- **The PRD topic is UC1**: milestone-based driver follow-up checklists, the
  feature built on this branch (`docs/superpowers/specs/2026-11-11-logisly-uc1-milestone-checklists-design.md`).

## What changes

1. **PRD format = Logisly's.** Section order, copied from their example:
   - Header table: Product Manager, User, UAT, Target Release.
   - Table of Content.
   - 1. Background (numbered problems).
   - 2. Impact:
     - Numbered goals with % targets, each with its formula ("# X / # Y").
     - A "Group | Current | Proposed" table, one row per team (Allocation, CT, Sales, MM, Warehouse, Driver).
     - A Metric list per team.
   - 3. Requirements & Timeline: a "Category | Details | PIC | Timeline" table with rows Release, Sosialisasi, Data Prep and Product.
   - 4. Flow: numbered steps (no image).
   - 5. Timeline: a "Category | Items | PIC | Timeline | Status" table.
   - 6. Mock Up: links or "TBD".
   - 7. Stories: a "Jira # | Description | Acceptance" table. Descriptions read "As <role>, I want <x> so that <y>", with an optional prefix `[Driver App]`, `[BE]`, `[FE]` or `[Ops Console]`. Acceptance is a numbered list.
   - Then "Pertanyaan terbuka", our addition, kept because the agent must not invent dates.

   Mixed English and Bahasa Indonesia, as in their example.
2. **Mock Jira replaces mock GitHub.**
   - One project, `LOG`; keys `LOG-<n>`.
   - Issue types: Epic, Story, Task, Bug.
   - Fields: summary, description, acceptance (list), status (To Do / In Progress / Done / Won't Do), resolution, components, labels, story points (a number field, not a label), sprint, assignee, epic (parent key), links (`duplicates`, `blocks`, `relates to`), comments.
   - Closing a duplicate means transitioning it to Won't Do with resolution Duplicate and a `duplicates` link.
   - The propose → approve → apply flow stays exactly as it is.
   - Components replace repos; capacity maps components to teams.
3. **Google Doc publishing.**
   - A `publish_prd` tool converts the PRD markdown to HTML. It uploads the HTML through the Drive API with conversion to a Google Doc in the configured folder (`LOGISLY_PRD_FOLDER_ID`) and returns the link.
   - Credentials come from ADC (the Cloud Run service account → the folder must be in a Shared Drive where that account is a member; a local run can use user ADC with the Drive scope).
   - Calling it again with the same name updates the same file. After the stories are applied, the agent republishes the PRD with real LOG keys in the Stories table.
   - With no folder configured, or on any Drive error, it returns `{"status": "unavailable", ...}` and the PRD stays in chat and in `save_document`.
4. **Sample data on the UC1 topic.** The four sources are rewritten around milestone follow-up:
   - Ops request (docx).
   - Product sync notes.
   - A CS email: a shipper complains about late POD / unloading updates.
   - An Ops/CT WhatsApp export: the bot polls all night, drivers' photos are ignored, WhatsApp cost.

   The Jira snapshot plants the cases the agent must catch: an existing duplicate pair, a superseded current-bot reminder story, a blocking in-progress dependency (GPS geofence), and date conflicts between sources and the sprint calendar or code freeze. The answer keys (expected PRD, backlog, consistency report) are rewritten to match.

## Non-goals

- A real Jira, or any Jira UI.
- PRD images or flow diagrams inside the Doc.
- Changing UC2.
- Redeploying Cloud Run (done only on the user's request).
- Gemini Enterprise re-registration.

## Anti-goals

- Fictional people only. No real driver names or phone numbers from the call transcripts or chat screenshots.
- The PRD must not promise anything UC1 does not do (no item list beyond the UC1 spec's checklist table).

## Left to the builder

The exact snapshot issues and wording, the HTML styling of the Doc, and the markdown-to-HTML library (`markdown` with the tables extension is fine).
