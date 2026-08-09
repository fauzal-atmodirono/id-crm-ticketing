# AI Assistant Behaviour

## When the AI replies vs. hands off to a human

### What it is

The AI assistant only acts on a conversation while it is genuinely waiting
for a first response — a **Pending** conversation with a new incoming
customer message. It ignores its own earlier replies and never touches a
conversation that's already **Open**, **Snoozed**, or **Resolved**; that
territory belongs to a human. When a burst of messages arrives quickly (a
customer typing several lines in a row), the assistant waits a brief moment
after the last one before deciding, so the whole burst gets one answer
instead of several.

Each time it decides, it settles on one of two outcomes from your side: it
either replies (see Suggest mode vs. Auto mode, below), or it hands the
conversation off to a human — reopening it so an agent can take over.
Internally the assistant can flag two different reasons for handing off (for
example, "this needs a specialist" versus simply "I'm not sure"), but since
this CRM keeps every escalation inside Chatwoot rather than a separate
ticketing system, both reasons produce exactly the same thing you see: a
reopened conversation, ready for an agent.

### Where to find it

Not something an agent switches on or off. Whether the assistant is active
at all on a given inbox, and in what mode, is set by an administrator under
**Knowledge → Inboxes** (see the Knowledge chapter).

### How to use it

1. Watch the conversation list: a conversation the assistant is still
   working on stays **Pending**; once it hands off, the conversation
   reopens (moves out of **Pending**) so a human can see it needs attention.
2. If the assistant hands off, it may first post a short acknowledgement
   message to the customer (an administrator-configured handoff message)
   before reopening the conversation — don't be surprised to see that
   message already sent when you open the conversation.
3. When a handoff conversation is reopened, it's often assigned to an
   agent automatically as part of the handoff, rather than staying
   unassigned.
4. Treat a conversation the assistant handed off the same way you would any
   other reopened conversation — read the thread, and reply or take
   whatever action the customer needs.

[[SCREENSHOT: ch10-ai-reply-vs-handoff | A conversation handed off to a human agent]]

### Example scenario

A customer messages the after-sales WhatsApp inbox asking for a refund on a
cancelled service package. The assistant recognizes this needs a human
decision, posts a brief "we'll connect you with an agent" message, and
reopens the conversation, which is then automatically assigned to the
on-duty agent.

### Integrations & automation

This behaviour works together with the AI auto-draft feature covered in the
Conversations chapter, and its persona, guardrails, and messages are
configured under Knowledge → Settings (see the Knowledge chapter). Which
inbox the assistant is active on, and in what mode, is set under Knowledge →
Inboxes.

## Suggest mode vs. Auto mode

### What it is

Every inbox where the assistant is active runs in one of two modes, chosen
by an administrator: **Suggest mode**, where the assistant drafts a reply as
a private note and reopens the conversation for a human to review and send;
or **Auto mode**, where the assistant sends its reply straight to the
customer and the conversation stays **Pending** while it continues handling
the conversation on its own.

### Where to find it

Set per inbox under **Knowledge → Inboxes**, with a tenant-wide default mode
under **Knowledge → Settings** that applies to any inbox without its own
override (see the Knowledge chapter for both).

### How to use it

1. An administrator decides which inboxes should run in Suggest mode
   (every reply reviewed by a human first) and which can run in Auto mode
   (the assistant replies directly, useful for high-volume, low-risk
   questions).
2. Set or change the mode for a specific inbox under **Knowledge →
   Inboxes**; leave an inbox unset to inherit the tenant-wide default mode
   from **Knowledge → Settings**.
3. As an agent, you can tell which mode an inbox is running by what you
   see: a suggested draft arriving as a private note (Suggest mode) versus
   a reply already sent to the customer with the conversation still
   Pending (Auto mode).
4. In either mode, if the assistant can't confidently answer, it hands off
   to a human instead (see the section above) rather than guessing.

[[SCREENSHOT: ch10-suggest-vs-auto | The difference between a suggested private note and an auto-sent reply]]

### Example scenario

Proton runs its general WhatsApp support inbox in Suggest mode, so every
AI-drafted reply is reviewed by an agent before it reaches a customer, but
runs a simpler pre-order inquiry inbox in Auto mode, since those questions
are lower-risk and higher-volume, and the team wants customers to get an
instant answer.

### Integrations & automation

The mode set here is what the Conversations chapter's "AI auto-draft and
suggest-vs-auto mode" section describes from an agent's point of view.
Whichever mode is active, replies are grounded in the same knowledge base
covered in the Knowledge chapter.

## Escalation labels & the escalation email

### What it is

Applying the **escalate** label to a conversation on an **Email** inbox
triggers an automatic two-part escalation email: a short acknowledgement
sent to the customer, and a separate forward containing the case details
sent to the responsible department PIC and/or dealer. This only applies to
Email-channel conversations — applying the label on a WhatsApp or phone
conversation doesn't send an email, since there's no email thread for it to
join.

Separately, the first time a dealer label is applied to any conversation
(on any channel, with or without the escalate label), the CRM stamps a
one-time timestamp used to measure how long that dealer takes to act on the
case — this is what feeds the dealer turnaround figures in the Reports
chapter.

### Where to find it

Applied like any other label, from an open conversation's label control
(see the Conversations chapter's Labels section).

### How to use it

1. Open the Email conversation you need to escalate.
2. Apply the **escalate** label.
3. Optionally also apply a department label and/or the relevant dealer
   label — see **Escalation Routing** in the Administration chapter for
   how these map to a specific PIC or dealer contact.
4. The acknowledgement email to the customer and the internal forward to
   the PIC/dealer are sent automatically; there's nothing further to click.
5. If you only need the case attributed to a dealer for turnaround
   reporting (no escalation email needed, or the conversation isn't on an
   Email inbox), apply just the dealer label — the turnaround clock still
   starts.

[[SCREENSHOT: ch10-escalation-label | Applying the escalate label to a conversation]]

### Example scenario

A customer emails about a recurring charging fault that the support team
can't resolve directly. The agent applies the **escalate** label along with
the relevant dealer's label; the customer receives an acknowledgement email
while the dealer's PIC receives the case details by email, and the dealer's
turnaround clock starts running from that moment.

### Integrations & automation

This ties directly into the Escalation Routing directory (Administration
chapter), which determines who actually receives the PIC/dealer email, and
into the Dealer Escalation Turnaround figures shown in the Reports chapter.
See also the Integration Overview chapter's Email section.

## Lifecycle messages

### What it is

The assistant sends a set sequence of automatic, customer-facing messages
across a conversation's life, separate from anything it says while
answering a question: an opening welcome/disclaimer message when a
conversation starts (or, on an Email inbox, a simple acknowledgement of
receipt instead), an idle-warning message if the customer goes quiet, a
closing message if the conversation is then auto-closed for inactivity, a
prompt asking whether the case is resolved, a satisfaction survey (worded
differently depending on whether the AI or a human agent handled the
conversation), a thank-you after the customer rates it, and a message
letting the customer know a human agent is being assigned if the case isn't
resolved yet.

### Where to find it

The wording of each message is set per assistant under **Knowledge →
Settings** (see the Knowledge chapter's Messages section), and the timing —
how long to wait before warning or closing an idle conversation — is set
per inbox under **Administration → Inboxes** (see the Administration
chapter), which can also override the wording for that inbox specifically.

### How to use it

1. Expect a new conversation to open with a welcome/disclaimer message
   (or an acknowledgement, on email) — this happens automatically, before
   an agent even looks at it.
2. If a customer stops replying mid-conversation, expect an idle-warning
   message once the configured wait time passes, followed by an automatic
   close if they still don't respond within the grace period.
3. When the assistant or an agent resolves the customer's issue, expect a
   "is your case resolved?" prompt; a "yes" answer leads to a satisfaction
   rating request, then a thank-you; a "no" (or an unclear) answer reopens
   the conversation for a human instead.
4. When a human agent resolves a conversation directly (without going
   through that prompt), the customer instead receives the agent-specific
   satisfaction survey afterward.
5. Treat all of these messages as normal, expected parts of the flow —
   they don't need an agent to trigger them.

[[SCREENSHOT: ch10-lifecycle-messages | The set of customer lifecycle messages in the assistant settings]]

### Example scenario

A customer messages the WhatsApp support line late at night. The assistant
answers the initial question, then the customer goes quiet; after the
configured idle period they receive a warning message, and since they never
reply again, the conversation auto-closes without ever needing an agent to
step in.

### Integrations & automation

These messages work together with the inactivity/auto-close timers covered
in the Administration chapter's Inboxes section, and their wording is
configured alongside the rest of an assistant's persona in the Knowledge
chapter's Settings section. A conversation resolved through this flow shows
up the same way as any other resolved conversation in the Conversations
chapter.

## Phone / IVR touchpoint

### What it is

Inbound phone calls to Proton's support line are answered by the same AI
assistant, using a voice conversation rather than text: it can hold a
natural back-and-forth, answer vehicle questions from the same knowledge
base used on WhatsApp, and ask the caller to rate the call 1–5 at the end.
There is no traditional press-1-for-sales phone menu — callers speak
naturally and the assistant works out what they need.

### Where to find it

There is no separate phone/IVR configuration screen inside the CRM. What
you see is the result each call leaves behind: a conversation in the
Conversations view with the call's transcript as its messages, updating
close to real time during the call.

<!-- VERIFY-LIVE: confirm current phone/IVR operator-visible surface on the live tenant -->

### How to use it

1. When a customer calls in, the assistant answers and the call appears
   as a new conversation in the Conversations view, with the spoken
   exchange logged as a transcript.
2. Read that transcript conversation the same way you would a WhatsApp or
   web conversation, including after the call has ended.
3. At the end of the call, the caller is asked to rate the interaction
   1–5; that rating feeds the CSAT report (see the Reports chapter) the
   same way a text-channel rating does.
4. If a caller reports an accident or breakdown outside business hours,
   the assistant is designed to route them straight to the 24/7 roadside
   assistance line rather than the normal queue — log the incident in the
   RSA Incident Log chapter once it reaches an agent.
5. If a caller asks to speak to a person, confirm with your administrator
   what currently happens on your tenant — live transfer to an agent may
   not be fully connected yet on every deployment.

<!-- VERIFY-LIVE: confirm current phone/IVR operator-visible surface on the live tenant -->

[[SCREENSHOT: ch10-phone-ivr | An inbound phone/IVR conversation in the inbox]]

### Example scenario

A customer calls outside business hours asking about the battery warranty
on an e.MAS X70; the assistant answers from the knowledge base, and the
call's transcript appears in the Conversations view for an agent to review
the next morning. Had the same caller instead reported an accident, the
assistant would route the call directly to the 24/7 roadside-assistance
line.

### Integrations & automation

Phone/IVR conversations join the same single inbox front door described in
the Introduction chapter, are answered from the same knowledge base
described in the Knowledge chapter, and feed the CSAT report and, for
roadside-assistance calls, the RSA Incident Log chapter — the same way any
other channel does. See also the Integration Overview chapter's Phone/IVR
section.
