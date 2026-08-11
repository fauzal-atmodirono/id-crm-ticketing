# Integration Overview
<!-- TRAINING: audience=admin -->

This chapter is a quick-reference map of every outside system the CRM
connects to, what each one is for, and what you see inside the CRM as a
result. Each section below has its own full write-up; use this table to
find the right one.

| Integration | What it connects | What you see in the CRM |
|---|---|---|
| WhatsApp | Proton's WhatsApp Business number | A WhatsApp inbox in Conversations |
| Web chatbot | The website chat widget | A Web Chatbot inbox in Conversations, running the same AI assistant as WhatsApp |
| Voice bot | Proton's support phone line, answered by an AI voice assistant | A conversation with a call transcript, alongside your other channels |
| Phone | The human side of that same call | The same conversation, plus — where turned on — a transfer attempt to a live agent |
| Email (incl. escalation emails) | Proton's support email address(es) | An Email inbox in Conversations, plus automatic two-thread escalation emails when the `escalate` label is applied |
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
3. **If the case needs escalating, don't rely on the `escalate` label
   here.** Adding it to a WhatsApp conversation by hand does not send an
   email or a WhatsApp alert to anyone — the automatic escalation flow
   only fires on an Email-channel conversation (see this chapter's Email
   section, below). The only automatic PIC notification on WhatsApp is the
   AI assistant's own judgement that a message is a genuine complaint,
   which an agent can't trigger by hand. **What to actually do:** contact
   the dealer or PIC directly (phone, a direct email), and don't tell the
   customer "I've escalated this" on the strength of the label alone. A
   `dealer_<slug>` label still records the case against that dealer for
   turnaround reporting — that part works on every channel — but it's a
   reporting stamp, not a notification.
4. Voice notes and photos aren't a reliable way to get information to the
   assistant yet — this has not been tested end to end on a real WhatsApp
   number. Describe it as untested if a customer or colleague asks, not as
   working.
5. If WhatsApp messages stop arriving, or replies aren't reaching
   customers, report it to your CRM administrator or Devoteam support —
   this is usually a problem with the WhatsApp Business connection itself,
   not something fixable from inside the CRM.

[[SCREENSHOT: ch12-whatsapp | A WhatsApp inbox conversation]]

### Example scenario

A customer messages Proton's WhatsApp number asking about e.MAS 7 test
drive availability; the conversation arrives in the WhatsApp inbox, where
the AI assistant drafts a reply for an agent to review and send (see the
End-to-End Scenarios chapter's Scenario 1). Where a case instead needs a
dealer's attention, see the End-to-End Scenarios chapter's Scenario 12 for
what escalating a WhatsApp case actually involves.

### Integrations & automation

WhatsApp conversations feed the AI Assistant Behaviour chapter's decisions,
create or update the customer's record in the Contacts chapter, and are
counted in the channel breakdowns shown throughout the Reports chapter.

## Web chatbot

### What it is

The website's live chat widget, connected as a channel so a visitor's
typed questions are answered by the same AI assistant used on WhatsApp.
Two practical differences from WhatsApp: replies go out as plain text (no
chat-style formatting conversion), and a visitor isn't required to give a
name, phone number, or email unless the widget's own pre-chat form has
been set up to ask for one.

### Where to find it

Appears as a Web Chatbot inbox in the Conversations view, wherever the
widget has been embedded on the website and the AI assistant has been
assigned to that inbox — confirm both are set up with an administrator
before promising a visitor the bot will answer.

<!-- VERIFY-LIVE: confirm exact Web Chatbot inbox setup wording on the live tenant -->

### How to use it

1. A visitor opens the chat widget on the website and types a question;
   it arrives as a new (or continuing) conversation in the Web Chatbot
   inbox.
2. Handle it like any other conversation — read, reply, and use AI-assist
   features exactly as covered in the Conversations chapter. Replies sent
   from here reach the visitor as plain text.
3. Because a visitor may not have given any contact details, don't assume
   a web-chat contact and a later phone or email contact are the same
   person just because the story sounds similar — check the contact
   panel's own record before treating them as linked (see the Contacts
   chapter).
4. **The `escalate` label has the same limitation here as on WhatsApp:**
   adding it to a Web Chatbot conversation by hand doesn't send an email
   or notify anyone — only an Email-channel conversation triggers the
   automatic escalation flow. **What to actually do:** contact the dealer
   or PIC directly, outside the CRM, the same as on WhatsApp.
5. If the widget stops loading, or messages aren't arriving, report it to
   your CRM administrator or Devoteam support.

[[SCREENSHOT: ch12-web-chatbot | A Web Chatbot inbox conversation]]

### Example scenario

A website visitor asks about e.MAS 7 financing options through the chat
widget; the assistant answers from the same knowledge base it would use on
WhatsApp, and when the visitor asks a follow-up the bot can't resolve, an
agent takes over and replies in plain text (see the End-to-End Scenarios
chapter's Scenario 13 for what happens if that same case needs escalating).

### Integrations & automation

Web Chatbot conversations feed the AI Assistant Behaviour chapter's
decisions and create or update the customer's record in the Contacts
chapter the same way WhatsApp does, using the same knowledge base — there's
no separate web-only content to maintain.

## Voice bot

### What it is

The AI-answered part of an inbound call to Proton's support line: a live
voice conversation (not a keypad menu) that greets the caller, answers
questions from the same knowledge base as other channels, and asks for a
1–5 rating before the call ends.

### Where to find it

No settings screen inside the CRM. What you see is what each call leaves
behind: a conversation in the Conversations view carrying the call's
transcript, appearing either live during the call or all at once at
hangup, depending on how your tenant is configured.

<!-- VERIFY-LIVE: confirm current phone/IVR operator-visible surface on the live tenant -->

### How to use it

1. A customer calls Proton's support line; the assistant answers, and the
   call appears as a conversation with the spoken exchange logged as a
   transcript.
2. The assistant is meant to answer in English, Bahasa Melayu, or Chinese
   and switch mid-call to match the caller — but **reliable Bahasa Melayu
   on a live call is a known, unresolved issue.** Don't promise a
   Bahasa-speaking caller the assistant will stay in Bahasa for the whole
   call; if they get stuck in English, apologize and hand the call to a
   human rather than calling it fixed.
3. At the end of the call, the caller is asked to rate the interaction
   1–5; that rating feeds the CSAT report the same way a text-channel
   rating does.
4. If the caller asks for a person, whether a transfer is even attempted
   depends on your tenant's configuration — see the Phone section below
   for what happens next once one is.
5. **Read this before promising anything else about this channel:** the
   greeting, KB answers, and rating survey were confirmed on a real,
   live call. Live transcript streaming, call classification, recording,
   and a real agent transfer are built and code-reviewed but, as of this
   writing, have never been run against a real phone call — describe them
   as "should work, unconfirmed" if a client asks, not as proven.

[[SCREENSHOT: ch12-voice-bot | A voice bot call transcript in the Conversations view]]

### Example scenario

A customer calls asking about the e.MAS X70's battery warranty; the
assistant answers from the same knowledge base used on WhatsApp, and the
call's transcript appears in Conversations for an agent to review, exactly
like a text conversation would (see the End-to-End Scenarios chapter's
Scenario 3).

### Integrations & automation

Voice bot conversations join the same single-inbox front door described in
the Introduction chapter and are answered from the same knowledge base as
every other channel (see the Knowledge chapter); the rating feeds the CSAT
report, and roadside-assistance calls feed the RSA Incident Log chapter
once a human picks them up.

## Phone

### What it is

The human side of the same call the Voice bot section covers: what's left
behind in Chatwoot once a call ends or is handed to a person, and — on
tenants where it's turned on — an attempted live transfer to an agent
mid-call.

### Where to find it

The same conversation the Voice bot section describes — there's no
separate "Phone" inbox or settings screen; it's what an agent does with
that conversation.

### How to use it

1. Open the resulting conversation like any other and read the transcript.
2. **If a transfer to a human is attempted:** the assistant tells the
   caller it's trying to connect them, then dials a single support number
   — the same number for every reason a call gets transferred; there's no
   separate always-on line for any particular kind of call.
3. **A real correction worth knowing:** every transfer attempt is gated by
   the support inbox's normal business hours, with no exception for
   accident or roadside calls. **Don't tell a caller reporting an accident
   after hours that they'll be automatically connected to a 24/7 line —
   that isn't built.** Log the incident in the RSA Incident Log chapter and
   handle the follow-up manually instead.
4. If someone answers, the conversation reopens with a note recording the
   handoff — check it while the transfer is still ringing, not after
   everyone's hung up.
5. **If nobody answers,** the caller hears a short apology and the call
   simply ends — it does not return to the assistant. The conversation is
   tagged so it's easy to find in your queue. **The apology promises a
   callback, but nothing sends that callback automatically** — only a
   human working that tagged conversation makes it happen, so treat an
   unanswered transfer as an open action item, not a closed loop.
6. **The `escalate` label has the same limitation here as on WhatsApp and
   the web chatbot:** a phone-originated conversation isn't on the Email
   inbox, so applying `escalate` by hand doesn't send anyone an email.
   Escalate to a dealer/PIC manually, the same as the other two channels.
7. If a caller reports call quality issues, dropped transfers, or a
   transfer that should have connected but didn't, report it to your CRM
   administrator or Devoteam support — most of this behaviour is still
   being confirmed against real calls.

[[SCREENSHOT: ch12-phone | A call transferred to a human agent, and the note it leaves behind]]

### Example scenario

A caller reporting a breakdown after business hours asks to speak to
someone; because there's no after-hours exception for roadside calls, the
transfer isn't attempted, so the agent who picks up the transcript the
next morning logs the incident in the RSA Incident Log chapter and follows
up directly instead (see the End-to-End Scenarios chapter's Scenario 14).

### Integrations & automation

Phone conversations feed the CSAT report and, for roadside-assistance
calls, the RSA Incident Log chapter, the same way the Voice bot section
describes. An unanswered transfer's tag is what an agent should look for
to know a callback is still owed.

## Email (incl. escalation emails)

### What it is

Proton's support email address(es) connected as an Email channel, plus the
two-thread escalation email flow that fires when an agent applies the
`escalate` label to an Email-channel conversation, and the reply loop that
links a dealer's, PIC's, or customer's reply back onto the case that sent
it. This is the one channel where escalation labels actually send email —
see the WhatsApp, Web chatbot, and Phone sections above for what to do on
the others.

### Where to find it

Appears as an Email inbox in the Conversations view. The escalation email
flow itself has no separate settings screen — it's triggered by applying a
label (see the AI Assistant Behaviour chapter's Escalation labels & the
escalation email section).

### How to use it

1. A customer emails Proton's support address; a new conversation is
   created on the Email inbox. If your tenant has the inbound
   auto-acknowledgement turned on, the customer also receives an automatic
   acknowledgement of receipt — check with your administrator whether it's
   on for you, since it's an editable, opt-in setting rather than something
   every tenant has running (see the Knowledge chapter's Settings section).
2. Handle the conversation like any other email conversation.
3. If the case needs escalating, apply a department label, then a dealer
   label if one applies, then the `escalate` label, in that order — the
   customer acknowledgement and the internal PIC/dealer-group forward
   emails are sent automatically (see the AI Assistant Behaviour chapter).
4. Watch the conversation for the dealer or PIC's reply — it's linked back
   onto this same conversation as a private note, with an AI-drafted
   customer reply beside it, rather than arriving as a separate email you
   have to go find (see the Conversations chapter's Escalation replies
   section). A customer who replies to their own acknowledgement rejoins
   the conversation the same way, as a normal incoming message.
5. If an expected escalation email, acknowledgement, or reply doesn't
   arrive, first check the recipients set up in Escalation Routing (see
   the Administration chapter); if it's still not working, report it to
   your CRM administrator or Devoteam support.

[[SCREENSHOT: ch12-email | An escalation email thread sent from an Email-channel conversation]]

### Example scenario

A customer emails about a recurring charging fault; the agent escalates it
with a department label, a dealer label, and `escalate`, the customer
receives an acknowledgement email, and every member of the dealer group
receives the case details by email at the same time (see the End-to-End
Scenarios chapter's Scenario 2). A few days later the dealer's reply shows
up as a private note on the same conversation, with a draft reply ready
for the agent to send on to the customer.

### Integrations & automation

Escalation emails rely on the Escalation Routing directory (Administration
chapter) to know who to notify — and to check a reply against before
linking it back onto a case — and feed the Dealer Escalation Turnaround
figures in the Reports chapter.

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

1. Use it indirectly through the Conversations chapter's Suggest a reply,
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

An agent handling a charging-error question clicks **Suggest a reply**;
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
