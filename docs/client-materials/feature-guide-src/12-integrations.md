# Integration Overview

This chapter is a quick-reference map of every outside system the CRM
connects to, what each one is for, and what you see inside the CRM as a
result. Each section below has its own full write-up; use this table to
find the right one.

| Integration | What it connects | What you see in the CRM |
|---|---|---|
| WhatsApp | Proton's WhatsApp Business number | A WhatsApp inbox in Conversations |
| Email (incl. escalation emails) | Proton's support email address(es) | An Email inbox in Conversations, plus automatic two-thread escalation emails when the `escalate` label is applied |
| Phone / IVR | Proton's support phone line, answered by an AI voice assistant | A conversation with a call transcript, alongside your other channels |
| Gemini AI | Google's Gemini AI model | AI-drafted replies, Ask Copilot answers, summaries, and Playground test answers |
| DMS / TSP | A dealer's Dealer Management System / Telematics Service Provider | Vehicle and service-history results inside a Customer 360 lookup |
| Knowledge base | Proton's FAQ and document corpus | The content AI-assist features and Ask Copilot draw their answers and source citations from |
| BI / reporting exports | External BI/reporting tools | Report figures made available for use outside the CRM |

## WhatsApp

### What it is

Proton's WhatsApp Business number connected to the CRM as a channel, so
customer WhatsApp messages arrive and are answered from inside the same
platform as every other conversation.

### Where to find it

Appears as a WhatsApp inbox in the Conversations view (see the
Conversations chapter's Conversation inbox & views section).

<!-- VERIFY-LIVE: confirm exact WhatsApp inbox setup wording on the live tenant -->

### How to use it

1. A customer messages Proton's WhatsApp number; the message arrives as a
   new (or continuing) conversation in the WhatsApp inbox.
2. Handle it like any other conversation — read, reply, and use AI-assist
   features exactly as covered in the Conversations chapter.
3. If WhatsApp messages stop arriving, or replies aren't reaching
   customers, report it to your CRM administrator or Devoteam support —
   this is usually a problem with the WhatsApp Business connection itself,
   not something fixable from inside the CRM.

[[SCREENSHOT: ch12-whatsapp | A WhatsApp inbox conversation]]

### Example scenario

A customer messages Proton's WhatsApp number asking about e.MAS 7 test
drive availability; the conversation arrives in the WhatsApp inbox, where
the AI assistant drafts a reply for an agent to review and send (see the
End-to-End Scenarios chapter's Scenario 1).

### Integrations & automation

WhatsApp conversations feed the AI Assistant Behaviour chapter's decisions,
create or update the customer's record in the Contacts chapter, and are
counted in the channel breakdowns shown throughout the Reports chapter.

## Email (incl. escalation emails)

### What it is

Proton's support email address(es) connected as an Email channel, plus the
two-thread escalation email flow that fires when an agent applies the
`escalate` label to an Email-channel conversation.

### Where to find it

Appears as an Email inbox in the Conversations view. The escalation email
flow itself has no separate settings screen — it's triggered by applying a
label (see the AI Assistant Behaviour chapter's Escalation labels & the
escalation email section).

### How to use it

1. A customer emails Proton's support address; a new conversation is
   created on the Email inbox, and the customer receives an automatic
   acknowledgement of receipt.
2. Handle the conversation like any other email conversation.
3. If the case needs escalating, apply the `escalate` label (and,
   optionally, a department or dealer label) — the customer acknowledgement
   and the internal PIC/dealer forward emails are sent automatically (see
   the AI Assistant Behaviour chapter).
4. If an expected escalation email or acknowledgement doesn't arrive,
   first check the recipient set up in Escalation Routing (see the
   Administration chapter); if it's still not working, report it to your
   CRM administrator or Devoteam support.

[[SCREENSHOT: ch12-email | An escalation email thread sent from an Email-channel conversation]]

### Example scenario

A customer emails about a recurring charging fault; the agent escalates it
with the `escalate` label and a dealer label, the customer receives an
acknowledgement email, and the dealer's PIC receives the case details by
email at the same time (see the End-to-End Scenarios chapter's Scenario 2).

### Integrations & automation

Escalation emails rely on the Escalation Routing directory (Administration
chapter) to know who to notify, and feed the Dealer Escalation Turnaround
figures in the Reports chapter.

## Phone / IVR

### What it is

Proton's support phone line, answered by the same AI assistant used on
other channels rather than a traditional keypad menu, using a live voice
conversation.

### Where to find it

There is no phone/IVR settings screen inside the CRM. What you see is each
call's artifact: a conversation with the call transcript, appearing
alongside your other channels in the Conversations view.

<!-- VERIFY-LIVE: confirm current phone/IVR operator-visible surface on the live tenant -->

### How to use it

1. A customer calls Proton's support line; the AI assistant answers and
   the call appears as a conversation with a live-updating transcript.
2. Review the transcript conversation the same way you would any other,
   once the call has ended.
3. A 1–5 rating captured at the end of the call feeds the CSAT report (see
   the Reports chapter).
4. Since there's no configuration screen for this integration inside the
   CRM, report any issue with call handling (calls not answering, no
   transcript appearing, or similar) to your CRM administrator or Devoteam
   support.

<!-- VERIFY-LIVE: confirm current phone/IVR operator-visible surface on the live tenant -->

[[SCREENSHOT: ch12-phone-ivr | An inbound phone/IVR call reaching the inbox]]

### Example scenario

A customer calls reporting a breakdown; the call is answered by the AI
assistant and logged as a conversation, and staff follow up by logging an
RSA incident once they pick up the case (see the End-to-End Scenarios
chapter's Scenario 3).

### Integrations & automation

Phone/IVR conversations join the same single-inbox front door described in
the Introduction chapter, are answered from the same knowledge base as
other channels (see the Knowledge chapter), and feed the CSAT report and,
for roadside-assistance calls, the RSA Incident Log chapter.

## Gemini AI

### What it is

Google's Gemini AI model, which powers every AI-assist feature in the CRM:
AI auto-drafted or auto-sent replies, Suggest-a-reply, the Ask Copilot
panel, Summarize, and Playground testing.

### Where to find it

Not a page of its own — it's the engine behind the AI-assist buttons and
automatic behaviour covered in the Conversations, Knowledge, and AI
Assistant Behaviour chapters.

### How to use it

1. Use it indirectly through the Conversations chapter's Suggest reply,
   Ask Copilot, and Summarize actions, or let it act automatically through
   AI auto-draft (see the AI Assistant Behaviour chapter).
2. Administrators tune how it behaves — its persona, guardrails, and
   response style — under **Knowledge → Settings** (see the Knowledge
   chapter).
3. Test how it will answer before customers see it, using **Knowledge →
   Playground**.
4. If AI-assist features stop responding, answer in the wrong language, or
   seem to ignore a guardrail, first check the assistant's configuration
   under Knowledge → Settings; if the problem continues, report it to your
   CRM administrator or Devoteam support.

[[SCREENSHOT: ch12-gemini-ai | The Gemini AI assistant drafting a reply]]

### Example scenario

An agent handling a charging-error question clicks **Suggest reply**;
Gemini AI drafts an answer grounded in the knowledge base with a Sources
line underneath, which the agent reviews before sending (see the
Conversations chapter's Suggest-a-reply section).

### Integrations & automation

Gemini AI is the shared engine behind every AI-assist feature described in
the Conversations, Knowledge, and AI Assistant Behaviour chapters — nothing
in this section works independently of those.

## DMS / TSP

### What it is

An optional connection to a dealer's own Dealer Management System or
Telematics Service Provider, which lets the Customer 360 lookup show a
customer's vehicle and service history alongside their CRM conversations.

### Where to find it

Configured under **Administration → Integrations**, on the **DMS / TSP**
card (visible only to administrators with the matching permission — see
the Administration chapter's Integrations and Roles & Permissions
sections). Its results appear inside a **Customer 360** lookup (see the
Contacts chapter).

### How to use it

1. An administrator with the right permission configures the connection
   under **Administration → Integrations → DMS / TSP** — provider label,
   authentication, base URL, and credential — and clicks **Test
   connection** to confirm it's reachable (see the Administration
   chapter).
2. Once enabled, any Customer 360 search that matches a vehicle shows a
   **DMS / TSP** section with that vehicle's service history.
3. A **Not connected** notice means the connection isn't configured or
   isn't reachable; a **Mock data** notice means the results shown are
   demo data rather than a live system. Either way, Customer 360 still
   shows the CRM's own contact, conversation, and RSA data.
4. If the connection reports an error or shows unexpected data, first
   check its status and **Test connection** result under Administration →
   Integrations; if it still doesn't work, report it to your CRM
   administrator or Devoteam support, since the fault may be on the
   dealer's own system.

[[SCREENSHOT: ch12-dms | The DMS / TSP connection status in Integrations]]

### Example scenario

A dealer calls asking about a customer's vehicle history; because the
dealership's DMS/TSP connection is configured and reachable, the operator's
Customer 360 search shows that vehicle's last two service visits alongside
its CRM conversations (see the Contacts chapter's Customer 360 section).

### Integrations & automation

This connection only affects the Customer 360 lookup covered in the
Contacts chapter — it has no effect anywhere else in the CRM until it is
enabled and reachable.

## Knowledge base (Vertex corpus)

### What it is

The combined FAQ and document corpus the AI assistant is grounded on:
editable FAQ entries, an indexed document corpus, and operator-uploaded
material, all covered in full in the Knowledge chapter.

### Where to find it

**Knowledge → FAQs**, **Knowledge → Documents**, and **Knowledge →
Uploads** (see the Knowledge chapter).

### How to use it

1. Maintain question-and-answer entries under **Knowledge → FAQs**,
   including bulk CSV import for adding many at once (see the Knowledge
   chapter's FAQs section).
2. Browse the larger indexed document corpus under **Knowledge →
   Documents**, and add operator-authored material under **Knowledge →
   Uploads** (see the Knowledge chapter's Documents section).
3. Test how the assistant answers using this material before customers see
   it, in **Knowledge → Playground**.
4. If an AI answer looks wrong, outdated, or missing a source, check
   whether the relevant FAQ entry or document is active/indexed first; if
   material that should be indexed is stuck as **failed** or missing, report
   it to your CRM administrator or Devoteam support.

[[SCREENSHOT: ch12-knowledge-base | The knowledge base sources cited in a suggested reply]]

### Example scenario

Ahead of a launch event, an administrator bulk-uploads 40 warranty FAQ
entries and spot-checks a few in Playground; once confirmed, real customer
questions on WhatsApp are answered live using those same entries (see the
End-to-End Scenarios chapter's Scenario 4).

### Integrations & automation

The knowledge base is what grounds AI auto-drafted replies, Suggest-a-reply,
and the Ask Copilot panel described in the Conversations and AI Assistant
Behaviour chapters — a change here takes effect the next time the assistant
answers, with no separate publishing step for FAQs.

## BI / reporting exports

### What it is

A way to get reporting figures out of the CRM for use in external
BI/analysis tools, beyond what a given report page shows on screen.

### Where to find it

Some report pages offer their own download/export option directly on the
page (see the Reports chapter); a larger, tenant-wide export is arranged
through your CRM administrator rather than a self-service button on every
report.

<!-- VERIFY-LIVE: confirm which report pages currently expose a self-service export button on the live tenant, versus which require an administrator-arranged bulk export -->

### How to use it

1. First check whether the report you need already offers a download or
   export option on its own page (see the Reports chapter).
2. If you need more data than that page shows — for example, a full
   dataset behind the Weekly Report for a client business review — ask
   your CRM administrator to arrange a bulk export.
3. Use the exported figures in your external BI/reporting tool as needed.

[[SCREENSHOT: ch12-bi-reporting | Exporting report data for BI use]]

### Example scenario

Ahead of a quarterly business review, Proton's operations lead needs
dealer-level detail beyond what the Weekly Report page shows on screen, so
they ask their CRM administrator to arrange a bulk export of the underlying
reporting data (see the Reports chapter's Weekly Report section).

### Integrations & automation

Exports draw on the same reporting warehouse behind every page in the
Reports chapter, so exported figures should match what those pages show
for the same period.
