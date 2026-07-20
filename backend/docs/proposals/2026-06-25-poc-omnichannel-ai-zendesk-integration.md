# Proton Conversational AI — Omnichannel POC: WhatsApp, Email & Phone → AI → Zendesk

**Proof-of-Concept Integration Proposal**

- **Date:** 2026-06-25
- **Prepared for:** Customer / business stakeholders
- **Status:** For review
- **Technical companion:** the full engineering design lives in
  [`docs/superpowers/specs/2026-06-24-whatsapp-twilio-zendesk-integration-design.md`](../superpowers/specs/2026-06-24-whatsapp-twilio-zendesk-integration-design.md)

---

## 1. Overview

Following the successful demo of the Proton conversational AI assistant, the next
step is to connect that same AI to the channels customers actually use —
**WhatsApp, Email, and Phone** — and to make sure every conversation is captured
in **Zendesk**, where the support team already works.

The AI acts as the **first responder** on all three channels. It answers common
questions instantly, and when a conversation needs a human, it hands over cleanly
to a Zendesk agent with the full context already attached. The result: faster
responses, 24/7 coverage, and a complete record in one place — without flooding
agents with low-value tickets.

This POC reuses the AI "brain" already built for the demo. We are **adding
channels around it**, not rebuilding it.

## 2. Objectives — what this POC proves

1. Customers can reach the Proton AI on **WhatsApp, Email, and Phone**.
2. The AI **resolves routine enquiries automatically** on every channel.
3. **Every conversation is recorded in Zendesk**, with smart filtering so only
   genuinely actionable conversations become open tickets.
4. **Smooth human handover** — agents pick up with full conversation history and
   an AI-written summary.
5. A **clear, validated cost model** for running the channels at scale.

## 3. Scope — the three channels

| Channel | How customers reach us | What the AI does |
|---|---|---|
| **WhatsApp Business** | Message the business WhatsApp number | Replies in real time; answers product, sales & support questions; escalates to a human when needed |
| **Email** | Email the support address | Auto-replies to straightforward emails and resolves them; drafts a reply for agent approval on sensitive or complex ones |
| **Phone** | Call the business number | Holds a natural, spoken conversation; answers questions; routes to a human when needed |

**One number** serves both WhatsApp and Phone. Email uses the existing Zendesk
support address.

## 4. How it works (plain language)

```
   WhatsApp ─┐
   Email ────┼──▶  Proton AI assistant  ──▶  Reply to the customer
   Phone ────┘        (answers, or                  │
                       hands over)                   ▼
                                          Recorded in Zendesk
                                    ┌───────────────┴───────────────┐
                          Needs a human?                    Routine / resolved?
                         OPEN ticket with full          CLOSED log record
                         history + AI summary,           (kept for the record,
                         assigned to an agent             not in the active queue)
```

1. A customer reaches out on any channel.
2. The AI understands the request and replies — using Proton's knowledge base for
   product and after-sales information.
3. **Behind the scenes, the AI decides whether this conversation needs a human.**
   - **Yes** (a complaint, a sales lead, a service request, an unhappy customer,
     or an explicit ask for a person) → it creates an **open Zendesk ticket**,
     attaches the **full conversation and a summary**, and routes it to an agent.
   - **No** (a simple question it fully answered) → the conversation is still
     **saved in Zendesk as a closed record** for the audit trail, but it does not
     clutter the agents' active queue.
4. When a human takes over, the AI steps aside; the agent's replies go back to the
   customer on the same channel.

### Why this matters
- **No ticket spam.** Agents only see conversations that actually need them.
- **Full history, always.** Every interaction is in Zendesk, ticket or not.
- **Email is handled carefully.** Because email is permanent and public-facing,
  the AI only auto-sends replies it's confident about; anything sensitive becomes
  a **draft for an agent to approve** rather than an automatic send.

## 5. Phased delivery

Delivered in three phases, simplest first, so value lands early and each channel
is validated before the next begins.

| Phase | Channel | Why this order | Indicative effort* |
|---|---|---|---|
| **A** | **WhatsApp** | Highest-impact channel; straightforward text | ~1–2 weeks |
| **B** | **Email** | Lowest complexity — Zendesk already handles email | ~0.5–1 week |
| **C** | **Phone** | Twilio **ConversationRelay** manages the speech — reuses the AI directly | ~1 week |

\* *Indicative engineering effort only, assuming prerequisites (§6) are ready.
The **WhatsApp Business verification** (§6) runs in parallel and can be the
longest single dependency — see Risks.*

> **🎯 Demo milestone — present to Proton by 2026-07-07.** To meet this date the
> phone channel uses Twilio **ConversationRelay** (Twilio handles the speech, so
> we build less). Because **WhatsApp Business verification has a multi-week lead
> time**, the 7 July demo will most realistically run WhatsApp on the **sandbox**,
> with **Email** and a **ConversationRelay phone** proof — while production
> WhatsApp verification continues in parallel for a follow-up go-live.

## 6. Prerequisites & responsibilities

Splitting clearly who provides what. Several items have **external approval lead
times** and should be started immediately, in parallel with Phase A.

### Customer / business to provide
- **Twilio account** upgraded to a paid plan (a Twilio trial number is already
  available for development).
- **WhatsApp Business Account + Meta business verification** — *the longest
  lead-time item (days to weeks); start first.*
- **Business phone number** (Twilio, voice-capable, Malaysia +60).
- **Zendesk admin access** — support address, API access, and enough agent seats
  for handover.
- Approval of the **knowledge content** the AI may use to answer customers.
- Confirmation of **data/privacy expectations** (conversation records, phone, and
  email will be stored in Zendesk).

### Delivery team to provide
- All integration code (channel connectors, AI wiring, Zendesk capture & handover).
- Zendesk configuration for the email automation.
- A **provisioning runbook** (step-by-step setup of accounts, numbers, and
  verification).
- Testing and a guided go-live for each phase.

## 7. Cost estimate

> **Region: Malaysia (+60).** Figures use current public rate cards (see the
> dedicated [cost breakdown](2026-06-25-omnichannel-poc-cost-breakdown.md) for the
> full sources and per-unit rates). The phone number rental and exact Google
> speech rates are the only items still to confirm.

**What drives cost**

| Cost driver | Charged as | Notes |
|---|---|---|
| Phone number | per number / month | One shared number for WhatsApp + Phone — MY **$4 local** / $25 toll-free |
| WhatsApp replies | per message ($0.005 Twilio) | Meta charge is **$0** — the AI's replies are the free "service" category |
| Phone calls | per minute | Voice $0.0085 **+ ConversationRelay $0.07** (Twilio handles the speech) |
| Email | — | **No extra channel fee** — uses existing Zendesk |

**Illustrative monthly example** — sample volume of 500 WhatsApp conversations
(~8 messages each) and 200 phone calls (~4 min each), phone via ConversationRelay:

| Item | Monthly |
|---|---|
| Phone number rental (MY local) | $4 |
| WhatsApp — Twilio fees ($0.005 × ~4,000 msgs) | ~$20 |
| WhatsApp — Meta (service category) | $0 |
| Phone — voice + ConversationRelay (800 min × ~$0.078) | ~$63 |
| Email | $0 |
| **Indicative total** | **~$87 / month** |

**Key cost insight:** because the AI only ever *replies* to customers who message
first, WhatsApp usage is Meta's **free "service" category** — only Twilio's small
per-message fee applies. **The main running cost is phone-call minutes.**
ConversationRelay (chosen for demo speed) is ~$0.078/min; if call volume grows, a
later switch to self-hosted speech (Media Streams + Google) roughly halves that to
~$0.03–0.05/min. WhatsApp + Email are very inexpensive to run.

> A separate detailed cost worksheet (with the calculation formula) is in the
> technical companion spec, §10.2.

## 8. Success criteria

The POC is successful when:
- A customer can hold a useful conversation with the AI on **each** of the three
  channels.
- The AI **resolves routine enquiries** without a human.
- **Actionable conversations create an open Zendesk ticket** with full history;
  routine ones are **logged but kept out of the active queue**.
- An agent can **take over** any conversation and reply to the customer on the
  same channel.
- We have a **confirmed cost model** for production volumes.

## 9. Assumptions, dependencies & risks

| Item | Type | Notes / mitigation |
|---|---|---|
| WhatsApp Business / Meta verification | Dependency | **Longest lead time.** Start immediately; use the WhatsApp sandbox for development meanwhile so engineering is not blocked. |
| Twilio trial limitations | Assumption | Trial reaches only verified numbers and adds sandbox text; production needs a paid upgrade. |
| Knowledge base coverage | Assumption | AI answer quality depends on the approved content; gaps are escalated to a human rather than guessed. |
| Phone real-time quality | Risk | Spoken conversation has latency/accuracy considerations; Phase C includes tuning and testing. |
| Data privacy / PII in Zendesk | Dependency | Conversation records, phone, and email are stored in Zendesk — confirm retention & residency expectations. |
| Final pricing | Dependency | All cost figures are illustrative until confirmed against current rate cards. |

## 10. Out of scope (this POC)

- Outbound marketing / promotional messaging (e.g. WhatsApp campaigns).
- Rich interactive WhatsApp elements (buttons, lists, catalogues).
- AI interpretation of email attachments (attachments remain on the ticket).
- Changes to the existing web demo interface.

## 11. Next steps

1. **Approve this POC scope.**
2. **Kick off WhatsApp Business / Meta verification immediately** (long lead time).
3. Confirm Twilio and Zendesk access (§6).
4. Begin **Phase A (WhatsApp)** while verification is in progress.
5. Confirm final Twilio/Meta/Google rates to convert §7 into a firm cost estimate.

---

*This proposal summarises the integration for stakeholders. The complete technical
design, security considerations, testing strategy, and detailed cost worksheet are
in the companion engineering spec linked at the top of this document.*
