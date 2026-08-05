# End-to-End Scenarios

## Scenario 1: WhatsApp inquiry to resolution

1. A customer messages Proton's WhatsApp number asking about the price and
   availability of a test drive for the e.MAS 7. Chatwoot creates a new
   conversation on the WhatsApp inbox, which is running in Suggest mode
   (see the AI Assistant Behaviour chapter's Suggest mode vs. Auto mode
   section).
2. The AI assistant drafts an answer grounded in the knowledge base and
   posts it as a private note, then reopens the conversation for a human
   (see the Conversations chapter's AI auto-draft section and the AI
   Assistant Behaviour chapter).
3. The on-duty agent opens the conversation, reads the suggested draft and
   its source citations, tweaks the wording slightly, and sends it as
   their own reply (see the Conversations chapter's Private notes and
   Suggest-a-reply sections).
4. The customer confirms they'd like to book the test drive; the agent
   arranges it and, once everything is confirmed, marks the conversation
   **Resolved** (see the Conversations chapter's Resolving, snoozing &
   transcripts section).
5. The customer receives the standard resolution prompt and satisfaction
   survey, and their 1–5 rating shows up later in the CSAT report (see the
   AI Assistant Behaviour chapter's Lifecycle messages section and the
   Reports chapter).

[[SCREENSHOT: ch11-scenario1-whatsapp | A WhatsApp inquiry resolved with an AI-suggested reply]]

## Scenario 2: Complaint escalation to turnaround report

1. A customer emails in about a recurring charging fault that wasn't fixed
   at their last service visit. The conversation lands on the Email inbox
   (see the Conversations chapter's Conversation inbox & views section).
2. The agent handling it decides the case needs the dealer's attention and
   applies the **escalate** label along with the relevant dealer's label
   (see the Conversations chapter's Labels section).
3. The CRM automatically sends the customer a short acknowledgement email
   and forwards the case details to the dealer's PIC by email, using the
   contact set up in Escalation Routing; the dealer's turnaround clock
   starts at the same moment (see the AI Assistant Behaviour chapter's
   Escalation labels & the escalation email section and the Administration
   chapter's Escalation Routing section).
4. The dealer investigates and confirms the fault has been repaired; the
   agent updates the conversation and marks it **Resolved**.
5. During the weekly ops review, a supervisor opens the Weekly Report page
   and checks the Dealer Escalation Turnaround table to see how long that
   dealer took to close the case, alongside every other escalation from
   the same week (see the Reports chapter's Weekly Report and Dealer
   escalation turnaround sections).

[[SCREENSHOT: ch11-scenario2-escalation | A complaint tracked from escalation label to the turnaround report]]

## Scenario 3: RSA call to Customer 360 follow-up

1. A customer calls Proton's support line reporting a breakdown on the
   toll road. The call is answered by the AI assistant, and the
   conversation appears in the Conversations view with the call transcript
   (see the AI Assistant Behaviour chapter's Phone/IVR touchpoint section).
2. Because it's a roadside situation, an administrator with access to the
   RSA Incident Log page logs a new entry with the vehicle number, cause,
   and breakdown location (see the RSA Incident Log chapter's Logging an
   RSA incident section).
3. As towing is arranged and the vehicle reaches the dealer's outlet, the
   same administrator updates the incident record with the relevant
   timestamps (see the RSA Incident Log chapter's Incident statuses &
   updates section).
4. A few weeks later, the same dealer calls asking about that customer's
   vehicle history. An administrator with Customer 360 access opens
   **Customer 360**, searches by the vehicle number, and sees the
   completed RSA incident together with the customer's conversations in
   one place (see the Contacts chapter's Customer 360 section and the RSA
   Incident Log chapter's RSA in Customer 360 & reports section).
5. Where the DMS/TSP connection is configured, the same Customer 360
   result also shows that vehicle's service history alongside the RSA
   incident (see the Contacts chapter's Customer 360 section and the
   Administration chapter's Integrations section).

[[SCREENSHOT: ch11-scenario3-rsa | An RSA incident followed up through Customer 360]]

## Scenario 4: FAQ batch import to live bot answer

1. Ahead of an e.MAS 7 launch event, Proton's service team compiles a
   spreadsheet of 40 frequently asked warranty questions.
2. An administrator exports the spreadsheet as a CSV file and uses
   **Bulk upload (CSV)** under **Knowledge → FAQs** to import all 40
   entries in one go (see the Knowledge chapter's FAQs section).
3. Before the launch, the administrator opens **Knowledge → Playground**
   and asks a few of the same questions a customer might ask, to confirm
   the assistant answers correctly using the newly imported entries (see
   the Knowledge chapter's Playground section).
4. Satisfied with the answers, the administrator leaves the entries active.
   When the launch event starts and a real customer asks one of those
   questions on WhatsApp, the AI assistant answers it live, grounded in
   the newly imported FAQ (see the Conversations chapter's Suggest-a-reply
   and AI auto-draft sections, and the AI Assistant Behaviour chapter).
5. Any question the imported FAQs don't cover still falls back to a
   normal handoff to a human agent, the same as any other unanswerable
   question (see the AI Assistant Behaviour chapter's When the AI replies
   vs. hands off to a human section).

[[SCREENSHOT: ch11-scenario4-faq-csv | A newly imported FAQ answered live by the bot after Playground testing]]

## Scenario 5: Weekly reporting routine

1. Every Friday afternoon, Proton's operations lead opens **Reports →
   Weekly Report** and sets the week picker to the period just finishing
   (see the Reports chapter's Weekly Report section).
2. They read **Case Volume** for the week's total and its channel/division
   breakdown, then **Case Status Trend** for how cases split across
   statuses during the window.
3. They scroll through **Inquiry / Complaint / Feedback Detail —
   Departments & PIC**, **Call Centre & SLA Performance**, and
   **Work-in-Progress / Case Aging** to note anything that needs follow-up
   before the client call.
4. They check **Dealer Escalation Turnaround** for any dealer whose
   average turnaround has crept up, ready to raise it on the call (see the
   AI Assistant Behaviour chapter's Escalation labels & the escalation
   email section for how that clock starts).
5. Figures from the page are copied into the weekly client deck; if the
   client asks for a deeper cut of the data than the page shows, the
   operations lead asks their CRM administrator to arrange a bulk export
   (see the Integrations Overview chapter's BI/reporting exports section).

[[SCREENSHOT: ch11-scenario5-weekly-report | Using the Weekly Report page for the weekly client meeting]]

## Scenario 6: New agent onboarding

1. Proton hires a new customer-service agent, Dian, to cover the
   After-Sales WhatsApp and email inboxes.
2. An administrator opens **Settings → Agents**, adds Dian with the
   **Agent** role, and an invitation is sent to her email address (see the
   Administration chapter's Agents section).
3. The administrator assigns her to the **After-Sales** team under
   **Settings → Teams**, so conversations can be routed to the team as a
   whole rather than to her individually at first (see the Administration
   chapter's Teams section).
4. The administrator makes sure she has access to the relevant WhatsApp
   and email inboxes under **Settings → Inboxes** (see the Administration
   chapter's Inboxes section).
5. Dian signs in for the first time using the credentials from her
   invitation, lands on the Conversations view, and — since her role is
   Agent — sees Conversations, Contacts, and Knowledge, but none of the
   administrator-only pages (see the Introduction chapter's Logging in
   and Roles: agent vs administrator sections).

[[SCREENSHOT: ch11-scenario6-onboarding | Onboarding a new agent with a role, team, and inbox assignment]]
