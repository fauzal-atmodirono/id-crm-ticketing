# Bahana Sekuritas — brief for writing the proposal

**Date:** 2026-08-22
**Status:** handoff brief, ready to use
**Branch:** dev-yuda
**For:** the agent writing the Bahana proposal
**Source of technical truth:**
`docs/superpowers/specs/2026-08-22-bahana-personalization-design.md`
**Owner of the engagement:** Yuda (Devoteam)

---

## 0. How to use this document

This is a brief, not a draft. It tells you what the proposal must say, what it
must not claim, and what you are missing.

Read the design spec first — it is the technical source of truth, and where any
architectural statement in the proposal must trace back to. This brief adds the
commercial framing, the accuracy guardrails, and the structure.

**The most important section is §3.** Read it before you write a word.

---

## 1. The situation

Bahana Sekuritas is an Indonesian securities firm, state-owned, part of the IFG
group. They have asked for **CRM (Customer Relationship Management):
personalisasi dan peningkatan interaksi nasabah/klien**, and put two options
forward themselves:

- **Opsi 1** — they already hold nasabah contact and profile data in **Mekari
  Qontak**, unused. They want personalization: targeted offers, delivered as a
  **suggestion** to their people or a **blast** to the customer.
- **Opsi 2** — build the CRM and its AI from scratch, possibly on Proton
  assets.

Their own words matter here: **they are not yet using the CRM, so the priority
is the AI personalization.** A CRM may be proposed, but they are candid that it
likely shouldn't compete with Qontak, which is already SaaS and in place.

**Our answer is a third framing** — not Opsi 1, not Opsi 2:

> An AI personalization and engagement layer that owns its own workspace,
> reads their customer data, produces portfolio-aware conversations and
> relationship-manager suggestions, and delivers over WhatsApp. Qontak stays
> in place for service conversations.

Lead with that. It is a smaller first sale than a CRM replacement and a
considerably larger second one, and it takes the Qontak objection off the table
before they raise it.

---

## 2. What is actually being demonstrated on Monday

A live demo runs **Monday 2026-08-24** on WhatsApp number `+16292843510`, on a
dedicated `bahana` tenant, using **synthetic data we generated**.

The demo shows: a nasabah messages WhatsApp; the AI recognizes them, sees their
portfolio, answers their actual question, and introduces a relevant offer in
context; a human agent then interrupts mid-conversation and takes over with the
full profile beside them; every AI decision is logged.

**What it proves:** the mechanism works end to end.
**What it does not prove:** anything about model quality on Bahana's real book.
Nobody can prove that without their data.

Say this in the proposal. A prospect who discovers the caveat themselves trusts
everything else less.

---

## 3. Do not invent these — ask Yuda

You are missing real inputs. An agent writing a proposal will be tempted to
fill these with plausible-sounding content. **Do not.** Every item below is
either a commercial decision or a fact we do not have.

| Missing input | Why it matters | What to do |
|---|---|---|
| **Price, rate card, or effort estimate** | We have agreed none. A number in a proposal is a commitment. | Leave a clearly marked section for Yuda, or describe *what drives cost* (§7) without quoting figures |
| **Engagement timeline / start date** | Not discussed | Ask. Do not invent a Gantt chart |
| **Is this an RFP response or a follow-up?** | Changes the whole document shape — formal procurement wants compliance matrices and mandatory-response formats; a post-demo follow-up wants a short narrative deck | Ask before choosing a structure |
| **Who reads it** — business, IT, procurement, compliance? | Determines depth and where the technical annex goes | Ask. If unknown, write for a business reader with a technical annex |
| **Bahasa Indonesia or English?** | A BUMN proposal is usually Bahasa Indonesia; technical annexes are often English | Ask. Recommendation: Bahasa Indonesia for the main document, English for the technical annex, and keep Indonesian domain terms (nasabah, RDN, saham, reksa dana) in both |
| **Whether Bahana will supply back-office data** | Decides whether this is next-best-action or better-worded messaging (§6) | Present as an open question *to them*, not as an assumption |
| **Anything about their existing Qontak contract** | Term, cost, satisfaction — unknown | Never speculate about a competitor's commercials |
| **Named references or case studies** | Proton and AEON360 are real work, but naming clients requires their permission | Describe capability generically unless Yuda confirms permission |
| **Any figure for WhatsApp messaging cost** | Meta's pricing changed materially during 2025 | Verify current Meta and Twilio rate cards before quoting. If you cannot verify, describe the cost *model*, not the rate |
| **Regulatory citations beyond UU PDP 27/2022** | We are confident on that one; OJK regulation numbers we have not verified | Refer to obligations in substance ("suitability", "licensed recommendation") rather than citing regulation numbers you have not checked |

If you cannot get an answer and must proceed, mark the gap visibly in the draft
rather than papering over it.

---

## 4. The core message

Everything in the proposal should ladder up to this. If a reader remembers one
paragraph, make it this one:

> Bahana already has the conversations and already has the data. What is
> missing is the layer that connects them — one that knows who the nasabah is
> when they message, and turns each conversation into a relevant, compliant,
> auditable interaction. We are proposing that layer, delivered on WhatsApp,
> without displacing what Bahana already runs.

Three supporting pillars, in this order:

1. **Personalization that respects the channel.** No mass blasting. Every
   interaction happens inside a conversation the nasabah started, which is
   both cheaper and more welcome. (§5)
2. **Compliance is designed in, not bolted on.** Human-in-the-loop by default,
   suitability enforced in code, every AI decision auditable. (§8)
3. **Proven machinery, new application.** The conversational half already runs
   in production; what we are building is the personalization on top. Lower
   delivery risk than "from scratch". (§9)

---

## 5. The template decision — explain it as strategy, not constraint

This will be the most-questioned part of the proposal, so handle it carefully.

**The fact:** WhatsApp only permits free-form messaging inside a **24-hour
window** that opens when the *customer* messages you. Outside it, every message
must be a pre-approved template, priced per message, and marketing templates
require opt-in.

**Our decision:** we use no templates at all.

**How to frame it** — as a deliberate product choice with three benefits, not
as something we couldn't afford:

- **Regulatory.** Customer-initiated conversations are far easier to defend
  than pushing investment offers at retail investors unprompted.
- **Commercial.** No marketing-category messaging fees, no Meta template review
  in the delivery path.
- **Effectiveness.** An offer that arrives inside a conversation the nasabah
  started, attached to a question they actually asked, will outperform an
  interruption.

**What replaces "blast"** — they asked for it, so answer it directly. The
mechanism is **staged offers**: a batch computes a next-best-offer per nasabah
and parks it on their profile; when that nasabah next messages, for any reason,
the AI answers their real question and then introduces the offer in context.
Campaign semantics, conversational delivery, zero messaging cost.

**Be honest about the consequence.** Because we never message first, something
must bring nasabah into WhatsApp: click-to-WhatsApp entry points in their app
and site, QR codes on e-statements, WhatsApp CTAs in existing emails, and
click-to-WhatsApp ads. This is a real dependency on Bahana's side and belongs
in the proposal as a joint workstream, not buried. Email and in-app remain
available for genuine bulk reach.

---

## 6. The data conversation — the most important commercial point

Qontak holds conversation contacts: name, phone, maybe tags. Genuine
personalization for a securities firm needs AUM, risk profile, holdings,
transaction recency and product gaps — which live in the back office, not in
Qontak.

**Personalization quality is capped by the data feed.** Whether Bahana provides
one decides whether they are buying a next-best-action product or segment-by-tag
messaging with better copy.

Handle this as a **phased commitment**, which lets them start without the feed
and makes the feed's value obvious:

- **Phase 1 works with what exists** — Qontak contacts, conversation history,
  and whatever attributes they can export today. Real, useful, limited.
- **Phase 2 unlocks with the back-office feed** — the point at which
  personalization becomes genuinely predictive.

Do not present the feed as a precondition. Present it as the difference between
two levels of outcome, and make the second one attractive.

---

## 7. Commercial shape

Phasing follows the design spec §6 — use these names for consistency:

| Phase | What it delivers | What it needs from Bahana |
|---|---|---|
| **Phase 0** | The demo. Mechanism proven on synthetic data. | Nothing — already done |
| **Phase 1** | Real data: back-office feed, profile store, identity resolution. Personalization stops being a demo. | Data feed, identity mapping decisions |
| **Phase 2** | RM surface: offer catalog, eligibility rules, scoring, staged offers, suggestion queue with approve-to-send. Their "suggestion" ask, delivered. | Offer catalog owner, compliance sign-off on suitability rules |
| **Phase 3** | Governance and scale: authentication tiers, consent ledger, opt-out, frequency caps, outcome attribution, operator segment builder. | Compliance and security review |

**On cost, without quoting numbers.** If you must address budget before Yuda
supplies figures, describe the drivers rather than the price: number of tenants
and inboxes; whether hosting is ours or theirs (a materially different
engagement — see §8.6 of the design spec); complexity and frequency of the data
feed; message volume, which under this design is dominated by inbound and
therefore by their entry points, not by campaign size; and the depth of
compliance review a state-owned firm will require.

---

## 8. Compliance — this is a differentiator, write it as one

Selling AI to a securities firm, the compliance section is not boilerplate. It
is the section that decides whether they take us seriously. Design spec §7 has
the detail; the proposal needs the substance of each:

- **Recommendations stay with licensed humans.** An AI that autonomously tells
  a nasabah to buy a security is a regulatory problem. Our default mode drafts
  and a human sends. Sell this as designed restraint.
- **Authentication before disclosure.** Phone possession is weak
  authentication — SIM swap, shared handsets, recycled numbers. Production runs
  two tiers: unverified gets education and generic offers with no figures;
  verified gets specifics. **Say plainly that the Monday demo does not
  implement this**, and that the production design does. Naming it first is
  worth more than hoping nobody asks.
- **Suitability enforced in code.** Risk-profile rules filter the offer catalog
  *before* the AI sees candidates. The AI phrases offers; it never selects
  them. This is the difference between a rule and a hope.
- **Consent and opt-out** under UU PDP No. 27/2022, honored by both the
  conversation and the batch.
- **Full auditability.** Every AI decision is logged with the inputs that
  produced it, before execution. Reproducible after the fact.
- **Data residency** is an open question we raise ourselves: their environment
  or ours, and it changes the deployment model. Raising it first reads as
  competence; having it raised at us reads as an oversight.

---

## 9. Positioning against Qontak

- **Do not** pitch CRM feature parity, and do not disparage Qontak.
- **Do** pitch the layer they don't have: portfolio-aware conversation,
  next-best-action, RM suggestions, auditable AI.
- Qontak stays for service conversations through Phase 1. That is a feature of
  the proposal, not a concession — it lowers their switching risk to near zero.
- One technical constraint worth stating plainly, because it will come up: a
  WhatsApp number has **exactly one inbound webhook**, so whoever holds it owns
  the conversation. If Bahana wants us on their existing Qontak-hosted number,
  that is a decision with consequences; running our own number avoids it. Present
  both, recommend neither until Yuda decides.
- Long term, if the layer proves out, absorbing the conversation surface is a
  natural expansion rather than a displacement fight. Hint at it; don't lead
  with it.

---

## 10. Suggested structure

Adapt once you know whether this is an RFP response or a post-demo follow-up
(§3). For a post-demo document:

1. **Executive summary** — one page. The core message from §4, the three
   pillars, and what we are asking them to decide.
2. **Understanding of the requirement** — play back their Opsi 1 / Opsi 2 in
   their own framing, then introduce the third framing (§1). This is where you
   earn the right to propose something they didn't ask for.
3. **What we demonstrated** — the demo, honestly bounded (§2).
4. **The proposed solution** — the personalization layer. Lead with outcomes;
   the architecture goes in the annex.
5. **Why no blasting** — §5. Anticipate the question rather than waiting for it.
6. **Data and what it unlocks** — §6, as two levels of outcome.
7. **Compliance and security** — §8. Give it real weight.
8. **Delivery phases** — §7.
9. **What we need from Bahana** — data feed, entry points, offer catalog owner,
   compliance sign-off, residency decision. A short, specific list.
10. **Open questions** — design spec §9, phrased as questions to them.
11. **Technical annex** — architecture, drawn from the design spec.

---

## 11. Tone

- Written for a business reader who will forward it to a technical one.
- Confident about what is built and running; precise about what is not.
- No hype vocabulary — no "revolutionary", "cutting-edge", "game-changing". A
  BUMN audience discounts it, and we don't need it: the demo is real.
- Every capability claim must be traceable to the design spec or to something
  demonstrably running. If you cannot trace it, cut it.
- Prefer concrete nouns over abstractions. "The AI sees the nasabah's risk
  profile and holdings" beats "leverages advanced customer intelligence".
- Where we are uncertain, say so in one clause and move on. Hedging everything
  reads as evasive; hedging the two genuinely uncertain things reads as honest.

---

## 12. Accuracy checklist before delivery

Run this against your draft:

- [ ] No price, rate, effort estimate or date that Yuda did not supply
- [ ] No WhatsApp or Meta pricing figure that was not verified against a
      current rate card
- [ ] No regulation number cited that was not verified
- [ ] No client named without confirmed permission
- [ ] No capability claimed that isn't in the design spec or demonstrably
      running
- [ ] The synthetic-data caveat appears where the demo is described (§2)
- [ ] The absence of authentication in the demo is stated, not omitted (§8)
- [ ] The inbound-traffic dependency is stated, not buried (§5)
- [ ] Qontak is never disparaged
- [ ] Every open question from design spec §9 appears somewhere
