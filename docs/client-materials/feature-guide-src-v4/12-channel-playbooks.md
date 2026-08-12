# Channel Playbooks
<!-- TRAINING: audience=agent -->

Every channel funnels into the same conversation list, and the toolbar you
work with is the same one everywhere. What differs is **what already happened
before the conversation reached you**, and **what an agent can and cannot make
happen from inside the CRM** on that channel.

This chapter is the per-channel walkthrough: what the customer did, what the
platform did on its own, and what you do next in each of the three situations
that actually occur — the AI handled it, the customer wants a person, or the
case has to reach someone outside the contact centre.

> **Read this as "the platform as configured today."** Almost everything here
> sits behind a switch an administrator controls, and the settings differ per
> tenant. Where a switch changes what an agent should *say to a customer*,
> this chapter names it in the step rather than hiding it in a footnote. If a
> step below does not happen on your tenant, the honest phrasing is "that
> isn't set up for us yet," not "it's broken."

---

## How the channels map to the CRM

| Customer touchpoint | Appears as | What happens before a human sees it |
|---|---|---|
| WhatsApp message | The WhatsApp inbox | Full AI assistant: knowledge-grounded answers, idle handling, live handoff to a person |
| Web chatbot | The website widget inbox | The same assistant and the same knowledge base; replies are plain text, and the visitor may be anonymous |
| Voice bot | The API-channel inbox, via the telephony connection | Greeting, knowledge-grounded answers, a satisfaction rating at the end, and a conversation left behind at hangup |
| Phone | The same conversation the voice bot created | Nothing — this is what is left for a person to work |
| Email | The Email inbox | An acknowledgement on arrival, and, on the `escalate` label, a two-thread escalation to the PIC and dealer group |

Social channels (Facebook, Instagram) are not connected and are not covered
here.

---

## The toolkit that is identical on every channel

| Action | Where |
|---|---|
| See whether the assistant already answered | The conversation timeline — assistant messages look like any other message |
| Ask the AI for a knowledge-grounded draft | Reply box → **Ask Copilot** |
| Get a suggested answer to the customer's last message | Reply box → **Suggest a reply** |
| Summarise a long conversation before taking it over | Reply box → **Summarize** |
| Categorise the case | Right sidebar → Conversation Actions → **Labels** |
| Reassign to another agent | Click the assignee name in the conversation header — recorded in the Audit Log |
| Resolve | The status control at the top right of the conversation |
| See a customer's history across channels | Contact panel → **Previous Conversations** |
| Look up a customer by phone or vehicle number | **Customer 360** in the left sidebar |
| Check or edit who an escalation reaches | **Escalation Routing** in the left sidebar |
| Check or edit response and resolution targets | **SLA Policies** in the left sidebar |

The four standalone pages in that list — Audit Log, Customer 360, Escalation
Routing and SLA Policies — appear only if your role carries the matching
permission. Not seeing one is a permissions question for your administrator,
not a fault.

---

## WhatsApp
<!-- TRAINING: audience=agent -->

The most complete channel: the assistant answers, hands off, and closes cases
on its own.

### What the customer does

Messages the connected WhatsApp number.

### What happens before you see it

1. The assistant replies in the customer's own language, grounded in the
   knowledge base whenever the question matches something there.
2. Outside business hours the customer receives the configured out-of-office
   reply.
3. If the assistant judges the message to be a genuine complaint, it escalates
   on its own — a PIC email plus an alert — and applies the `escalate` label.
   This is the assistant's own classification. **You cannot trigger it by
   adding the label yourself**; see Scenario C.

### Scenario A — the assistant handles it end to end

The customer asks a routine question and the assistant answers from the
knowledge base. **Your role: none.** Spot-check by opening the closed
conversation, or through the CSAT report.

### Scenario B — the customer asks for a person

1. The conversation reopens and is assigned to an available agent whose
   channel priorities include WhatsApp.
2. Work it as any other conversation — **Ask Copilot** gives you a
   knowledge-grounded draft to edit rather than a blank reply box.
3. Resolve when done.

### Scenario C — the case needs to reach a dealer or PIC

**Adding the `escalate` label to a WhatsApp conversation does not email
anyone.** The escalation email flow runs on Email-channel conversations only.
The label will stick, and nothing will be sent.

1. A `dealer_<slug>` label *is* still worth applying — it stamps the case for
   dealer turnaround reporting, and that part works on every channel. It is a
   reporting stamp, not a notification.
2. **What to actually do:** contact the dealer or PIC directly, or open an
   Email conversation with the customer and escalate from there (see the Email
   playbook). Do not tell a customer "I've escalated this" on the strength of
   the label alone.

> Your administrator can switch escalation on for every channel. Until they
> have, treat the label as inert here.

### What is not usable yet on WhatsApp

- **Voice notes and photos.** The assistant can be given them, but this has
  never been tried end to end against a real WhatsApp number. Describe it as
  untested if a colleague asks, not as working.
- **Manual escalation by label**, as above.

[[SCREENSHOT: ch12-whatsapp-conversation | A WhatsApp conversation with an assistant reply and the agent's reply box]]

---

## Web chatbot

The same assistant and the same knowledge base as WhatsApp, so the shape of
the work is identical. Two differences matter in practice.

**Replies go out as plain text.** No WhatsApp-style formatting or message
splitting.

**The visitor may be anonymous.** Nothing forces a name, email or phone
number. A visitor who gives none gets a contact record with nothing
identifying in it — which means **Previous Conversations** cannot connect them
to a later phone call or email. Do not assume a web-chat contact and a later
caller are the same person because the story matches. Check the contact panel.

### What happens before you see it

The assistant answers from the same knowledge base, and the same automatic
complaint escalation applies. There is no web-only knowledge source.

### Scenario A — the assistant handles it end to end

As WhatsApp. Your role: none.

### Scenario B — the visitor wants a person

Reply directly, or use **Ask Copilot**. The reply goes out as plain text.
Resolve when done.

### Scenario C — the case needs to reach a dealer or PIC

Identical to WhatsApp: the `escalate` label sends nothing on this channel.
Escalate outside the CRM, or move the case to email.

### What is not usable yet on Web chatbot

- Manual escalation by label, as above.
- An anonymous visitor may have no phone number to look up in Customer 360.
- No voice or image understanding — this channel is text only by design.

---

## Voice bot — the AI-answered part of a call

> **Say "should work, not yet confirmed" about anything in this section beyond
> the basics.** The greeting, knowledge-grounded answers, the satisfaction
> rating and the conversation created at hangup were demonstrated on a real
> call. Everything beyond that — live transcript, automatic classification,
> call recording, a real transfer to a person — is built and switched off, and
> has never been exercised against a real call. Do not present any of it as
> demonstrated.

### What the customer does

Dials the number connected to the platform. There is no "click to talk"
button on any website today.

### What happens before you see anything in the CRM

1. The assistant greets the caller and answers vehicle and product questions
   from the same knowledge base every other channel uses.
2. It is instructed to handle English, Bahasa Melayu and Chinese, and to
   switch mid-call to match the caller. **Bahasa reliability on calls is an
   open issue** — do not promise it.
3. The conversation appears in the CRM **at hangup**, built from the complete
   transcript. Watching a call arrive line by line is a separate setting that
   is switched off.
4. A call that ends before anyone speaks cleans itself up rather than sitting
   in your queue as an empty conversation.

### Scenario A — routine question, fully AI-handled

The assistant answers, asks whether there is anything else, then asks for a
1–5 rating before the call ends. **Your role: none in real time.** Review the
conversation afterwards and check the CSAT report.

### Scenario B — the caller wants a person

Today the assistant says a specialist will follow up, and the call continues.
**No transfer is attempted.** Someone has to pick the conversation up
afterwards and call back. Live transfer is built but switched off; see the
Phone playbook for what it would involve.

### Scenario C — the case needs to reach a dealer or PIC

A call-originated conversation is not on the Email inbox, so the `escalate`
label notifies nobody. If the assistant's own complaint detection fires, a PIC
is notified automatically. Otherwise, escalate outside the CRM.

### What is not usable yet on Voice bot

- **Reliable Bahasa Melayu on calls** — an open issue.
- **No keypad menu.** Do not train agents on "press 1 for sales"; it does not
  exist and no decision has been taken to build it.
- **No customer-facing call widget** on any website.
- Live transcript, classification, recording and real transfer are all built
  and switched off.

---

## Phone — the human side of the same call

This continues the call the voice bot answered. There is no separate "what the
customer does": this is the conversation the CRM is left holding, and what you
do with it.

### What happens before you see it

At hangup, the conversation carries the call transcript. Automatic
classification of the call into a case category, and call recording, are both
built and switched off on this tenant — so do not expect a category label or
a recording attribute to appear on a call.

### Scenario A — fully AI-handled call, reviewed afterwards

Open the conversation, read the transcript, and check the CSAT report for the
rating. Nothing to act on unless the transcript says otherwise.

### Scenario B — the call was transferred to a person

Transfer is switched off today, so this does not currently happen. If your
administrator enables it, three things are worth knowing before you rely on
it:

1. **Business hours gate every transfer, with no exception.** Outside the
   configured hours the platform refuses the transfer rather than routing it
   somewhere else. There is no 24/7 bypass for roadside or accident calls —
   if that is wanted, it has to be built.
2. If a person answers, the conversation flips to open with a handoff note the
   moment the transfer is dialled — look for it while the transfer is still
   ringing.
3. If nobody answers, the caller hears a bilingual apology and the call ends.
   It does not return to the assistant. The conversation is tagged
   `unanswered_handoff`. **The apology promises a callback that nothing
   schedules** — only a person working that tag makes it true.

### Scenario C — the case needs to reach a dealer or PIC

As with the voice bot: the label sends nothing. Escalate outside the CRM, or
move the case to email.

### What is not usable yet on Phone

- **No automatic callback** behind the unanswered-transfer apology.
- **No roadside-specific after-hours routing.** The Roadside Assistance
  incident log is a separate record-keeping page and has nothing to do with
  call routing.
- **An agent on a call is not automatically marked busy** for routing on other
  channels.
- Everything past the transcript is unconfirmed against a real call.

---

## Email
<!-- TRAINING: audience=agent -->

The channel with the most automation behind it, and the only one where the
`escalate` label actually sends something.

### What the customer does

Emails the support address.

### What happens before you see it

1. **Inbound mail is polled, not pushed.** A new email typically takes one to
   two minutes to appear as a conversation. A test email that does not show up
   instantly is not a failure.
2. **A new subject line produces one acknowledgement to the customer.** A
   reply on an existing thread does not produce another.
3. **The first inbound message may attract a suggestion note.** If no
   department label is on the conversation yet, the assistant posts a private
   note naming the department it thinks fits — for example *AI-suggested
   escalation department: **sales*** — and reminds you to apply that label
   before `escalate`. It is a nudge and nothing more: **no label is ever
   applied automatically**, because an AI decision should not silently trigger
   a real escalation email. Use your own judgement and apply the label
   yourself. Departments with no PIC configured are never suggested.

### Scenario A — routine email, no escalation

Allow the poll interval, then work it like any conversation: reply from the
CRM, resolve when done. Your reply suppresses any further automatic
acknowledgement on that thread.

### Scenario B — the customer needs a substantive reply

**Ask Copilot** for a knowledge-grounded draft, edit it, send, resolve.

### Scenario C — escalation to a PIC and a dealer

**Label order matters, and it is the single most important operational fact in
this chapter.** The platform reads the labels present at the moment `escalate`
is applied. Apply `escalate` first and the PIC leg does not fire for that
trigger — only the customer acknowledgement goes out.

1. **Apply the `dept_<slug>` label first** — for example `dept_sales`. Ask an
   administrator which departments have a PIC configured; escalating to one
   that does not is silent, and nobody is emailed.
2. **If a dealer is involved, apply the `dealer_<slug>` label next.** The label
   has to already exist and match the dealer's routing slug exactly — a
   mismatched or space-containing label silently matches nothing. Every member
   email on that dealer's group receives the forward.
3. **Apply `escalate` last.**
4. Up to three emails go out:
   - **To the customer** — subject *Update on your case: …*. It never carries
     a case tag; the customer's thread stays clean.
   - **To the department PIC** — subject *[Escalation] …*, copying the
     department's listed personnel.
   - **To the dealer group** — subject *[Escalation - Dealer Forward] …*, to
     every member on the list.
5. **Escalation fires once.** Further changes to the conversation while
   `escalate` is still applied send nothing more. Removing and re-adding the
   label *does* send a second real round — so do not toggle the label to
   demonstrate the feature in front of a client.
6. A customer with no email address on file gets no acknowledgement; the PIC
   email still goes out.

[[SCREENSHOT: ch12-escalation-labels | A department label and escalate applied in order on an email conversation]]

### Scenario C continued — the dealer's reply comes back

1. The dealer replies to the escalation email without editing the subject.
   Their reply arrives as a separate, throwaway conversation — allow the same
   one-to-two-minute poll.
2. **The original case gains a private note** beginning *Reply from &lt;name&gt;
   &lt;email&gt;:* with the quoted trail and signature stripped out.
3. **A second private note follows** titled *Suggested customer reply (draft —
   review before sending):* with an AI-drafted reply beneath it. **You review
   and send it yourself.** Nothing reaches the customer without a person
   clicking send.
4. The case is labelled `escalation_replied` and stamped with the time. **A
   second reply from the same dealer after that is not linked** — if a dealer
   says they replied twice, check the mailbox rather than the case.
5. **The throwaway conversation resolves itself** and needs nothing from you.
   The dealer receives nothing back from the CRM — no acknowledgement, no
   satisfaction survey. Neither belongs in an external mailbox.
6. **None of this re-escalates the case.** The customer is not emailed again
   and the PIC is not emailed again.
7. **If the customer replies to their own acknowledgement**, their words land
   on the case as a private note prefixed *Customer's own reply (from
   &lt;email&gt;…)* and **the case is reopened** so it returns to the queue. It
   does not appear in the main thread as a customer message — that is a
   platform limitation, not a fault, and the note wording makes the source
   unmistakable.

### SLA breach alerts

1. On breach, the department's PIC — resolved from the conversation's
   `dept_<slug>` label, using the same routing table as escalation — receives
   an email subject *[SLA] SLA_BREACH_NO_RESPONSE on case &lt;N&gt;*.
2. The conversation gains a private note naming the breach.
3. A later scan with no activity in between does **not** re-send.

### What is not usable yet on Email

- **A department with no PIC escalates to nobody, silently.** Check the
  Escalation Routing page before promising a department will be reached.
- Department slugs used for escalation are a **separate list from the case
  category labels** used for reporting. Do not assume every case category has
  a matching PIC.

---

## One customer, four touchpoints

> A customer messages WhatsApp about a delivery delay and gets a routine
> answer. A week later they open the website chat with a follow-up the
> assistant cannot resolve, and an agent takes over. Two weeks after that they
> call in for a status update. The agent who picks up the transcript decides
> the dealer needs to see it.

1. **WhatsApp** — the assistant answers and the case closes.
2. **Web chat** — the assistant here has no awareness the WhatsApp
   conversation ever happened. Each session is grounded only in what is said
   in that conversation. It hands off; an agent resolves it.
3. **The call** — same again: the assistant treats it as a fresh enquiry.
4. **As the agent working the transcript** — open the contact record and use
   **Previous Conversations** to see the WhatsApp and web chat history before
   replying. **Customer 360** will pull the same history from a phone number
   in one search.
5. **Escalating to the dealer** — the call's conversation is not on the Email
   inbox, so it cannot escalate. Open an Email conversation with the customer
   and run the Email playbook's Scenario C there.

**The biggest cross-channel gap:** there is no automatic link between a
customer and all their prior cases. **Previous Conversations** and Customer
360's search are the whole of what exists, and both depend on the customer
being recognisable — the same email, the same phone number — each time.

---

## Quick reference

| Task | Where to click |
|---|---|
| Draft a reply with AI help | Reply box → **Ask Copilot** |
| Answer the customer's last message | Reply box → **Suggest a reply** |
| Catch up on a long conversation | Reply box → **Summarize** |
| Categorise a case | Right sidebar → Conversation Actions → **Labels** |
| Escalate an **email** case | Labels: `dept_<slug>` → `dealer_<slug>` → `escalate`, **in that order** |
| Escalate a WhatsApp, web or phone case | No automatic email — contact the PIC or dealer directly, or move the case to email |
| See a dealer's reply | Private note on the **original** case, beginning *Reply from …* |
| See who reassigned a case | **Audit Log** in the left sidebar |
| Check who an escalation reaches | **Escalation Routing** in the left sidebar |
| Look up a customer by phone or vehicle | **Customer 360** in the left sidebar |
| Check response and resolution targets | **SLA Policies** in the left sidebar |
| See which agent owns which case at a glance | The **Agent** column on the Cases list |
| See a customer's history across channels | Contact panel → **Previous Conversations** |

---

## Known limitations, and what to tell the customer

| Channel | Limitation | What to say |
|---|---|---|
| WhatsApp, Web, Phone | The `escalate` label alone notifies nobody | "I'm looping in the right team directly — I'll follow up once I hear back," then actually contact them |
| All AI channels | The assistant has no memory across channels | "Let me pull up your earlier conversation so I have the full picture" — then actually open Previous Conversations |
| Email | Label order matters | Internal only — just follow it |
| Email | A second dealer reply is not linked onto the case | If a dealer says they replied twice, check the mailbox directly |
| Email | A department with no PIC escalates to nobody | Check Escalation Routing before promising a department will be reached |
| Voice bot, Phone | Nothing past the greeting, answers and rating has been run against a real call | Do not promise transcript, recording, classification or transfer works — say "should work, not yet confirmed" |
| Phone | No roadside-specific after-hours transfer | Never tell a caller reporting an accident after hours that they will be transferred automatically |
| Phone | Bahasa Melayu on calls is unreliable | If a caller gets stuck in English, apologise and handle it manually — do not call it fixed |
| Phone | A transfer that goes unanswered promises a callback nothing schedules | "Let me personally make sure someone follows up," then work the `unanswered_handoff` tag |
| Cases list | The Agent column is on the Cases list only | The assignee's name is not on the conversation card in the normal inbox — open the case, or use the Cases list |
| Customer 360 | No dealer management system is connected | Never read a vehicle field to a customer as their real vehicle record |
| Social | Facebook and Instagram cannot be connected | "We don't support Facebook or Instagram messages yet" |
