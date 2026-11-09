# Bahana — AI personalization on top of the CRM's automation engine

**Date:** 2026-08-24
**Status:** design + implemented rule set
**Branch:** dev-yuda
**Companion to:** `docs/superpowers/specs/2026-08-22-bahana-personalization-design.md`
(read §4.3 of that spec first — the who/what/how split is the premise this
document builds on)
**Provisioning script:** `deploy/scripts/provision-bahana-automation.py`

---

## 1. The question this answers

Bahana's incumbent, Mekari Qontak, sells workflow/automation. Ours does too —
Chatwoot v4.15.1 ships a rule engine we had never switched on for any tenant.
The question is not "do we have automation" but **"what happens when the rule
engine and the AI run on the same conversation."**

The answer is that they compose without new plumbing, because they already
share one data surface.

---

## 2. The seam: attributes in, labels out

Nothing needed to be built to connect these two. Both halves already read and
write the same two Chatwoot primitives.

```
  warehouse / nightly batch
        │  writes profile + staged offer
        ▼
  contact custom_attributes ──┬──► Automation Rules ──► labels · assignment · status
                              │       (deterministic: who / when / where)
                              └──► AI prompt context ──► the wording
                                        (the "how", and only the "how")
        ▲                                   │
        └──────── labels + outcome ◄────────┘
```

Three facts make this work, all of them already true in production:

1. **The orchestrator reads `contact.custom_attributes` on every single turn**
   (`agent/app/services/orchestrator.py:491`), formats them through
   `customer_context.py`, and folds the result into the system prompt. The
   profile is re-read per turn, not cached — change an attribute and the very
   next message is answered against the new value.
2. **Automation rules can condition on those same contact custom attributes.**
   Chatwoot validates the key against `custom_attribute_definitions` with
   `attribute_model: contact_attribute`, so the eight nasabah attributes the
   seeder writes are usable as rule conditions with no code at all.
3. **A label write fires `conversation_updated` to our webhook**, which is
   already HMAC-verified and deduped. `sync.py::maybe_escalate` and
   `maybe_stamp_dealer_escalation` have consumed exactly this signal in
   production for months — the only difference is that today a human applies
   the label and tomorrow a rule does.

Point 3 is the load-bearing one and is easy to miss. See §6.1.

---

## 3. Division of labour

**Automation decides who gets what and when. The AI decides only how it is
said.** This is the same separation the spec demands for compliance reasons
(§4.3, §7.4), except the "who" half now lives in a settings page the business
team can edit instead of in code they cannot.

| Job | Owner | Editable by | Auditable as |
|---|---|---|---|
| Eligibility / suitability | offer catalogue + `dim_offer_eligibility` | compliance | a SQL join |
| Who, when, where it routes | **Chatwoot automation rules** | business / RM lead, in the UI | a rule list |
| Whether the AI may speak at all | **Chatwoot automation rules** (via assignment) | business / RM lead, in the UI | a rule list |
| The actual wording | Gemini, via the orchestrator | persona text, in the UI | `ai_actions` rows |

Every row of that table is something a compliance reviewer can be shown
directly. None of it is "trust the prompt".

---

## 4. The four patterns

### 4.1 Pattern A — automation segments, the AI speaks

A rule reads the profile and stamps a segment label; the AI reads the same
profile and matches its register to it.

> `conversation_created` + contact `risk_profile = Konservatif`
> → `add_label segmen-konservatif`

The value here is not the label. It is that **the segmentation thresholds
become operator-editable**: the RM lead retunes who counts as a priority
nasabah in the settings page, with no deploy and no prompt edit. This is the
cheapest personalization lever we can hand Bahana, and the easiest to
demonstrate.

### 4.2 Pattern B — automation is the AI's kill switch

This is the pattern worth selling.

The orchestrator acts **only** on conversations whose status is `pending`
(`orchestrator.py:145`, `_is_eligible`). Therefore *any* rule that assigns a
conversation to a human takes the AI off the air — deterministically, before
the model is ever called, with no prompt involved.

| Rule | Effect |
|---|---|
| `consent_marketing = false` → assign team | the AI cannot stage an offer to a nasabah who opted out |
| unverified sender asks about balances → assign team | the AI cannot disclose figures (spec §7.1) |
| top AUM band → assign team | highest-value relationships are human-first; the AI drafts as a private note under `AGENT_MODE=suggest` |

**Consent and suitability become operator-editable controls rather than prompt
instructions**, which is precisely what §7.4 says they must be. For a
state-owned securities firm this is the strongest artefact we have: the
compliance officer reads a list of rules, not a paragraph of English handed to
a language model.

### 4.3 Pattern C — automation stages and consumes the offer

The staged-offer mechanic from spec §4.4, expressed as rules:

1. A rule labels conversations whose contact carries a `next_best_offer`
   (`offer-staged`).
2. The label write fires `conversation_updated` to our agent service.
3. The service (Phase 2 — not built) resolves the offer, checks consent and
   suppression, and writes it back onto the conversation.
4. The next turn's prompt carries it; the AI answers the customer's actual
   question first and weaves the offer in.
5. On delivery the service stamps `offer_delivered_<id>`, which both prevents
   a repeat and becomes the attribution key.

Only step 1 is provisioned today. Steps 3–5 are Phase 2.

### 4.4 Pattern D — outcome closes the loop

Resolve → outcome label → back into the warehouse → the next batch scores
better. This is what makes the personalization improve over time **without
training a model**: the learning lives in a batch job that can be inspected,
diffed and explained. For a regulated buyer that is a feature, not a
limitation.

Not built. Phase 3.

---

## 5. The rule set as provisioned

Eight rules, six labels, one custom attribute, one team. Created by
`deploy/scripts/provision-bahana-automation.py` (dry-run by default).

### 5.1 Active on creation — additive only

These only add labels. **None of them can silence the AI**, which is
deliberate: the tenant is still a demo tenant and the AI holding the
conversation is the thing being demonstrated.

| Rule | Event | Condition | Action |
|---|---|---|---|
| `Segmen — Konservatif` | `conversation_created` | contact `risk_profile` = `Konservatif` | label `segmen-konservatif` |
| `Segmen — Moderat` | `conversation_created` | contact `risk_profile` = `Moderat` | label `segmen-moderat` |
| `Segmen — Agresif` | `conversation_created` | contact `risk_profile` = `Agresif` | label `segmen-agresif` |
| `Nasabah prioritas` | `conversation_created` | contact `aum_band` ∈ {`Rp 500 juta - 1 miliar`, `> Rp 1 miliar`} | label `nasabah-prioritas` |
| `Penawaran tersedia` | `conversation_created` | contact `next_best_offer` is present | label `offer-staged` |
| `Opt-out — BERHENTI/STOP` | `message_created` | content contains `BERHENTI` or `STOP` | label `opt-out` + assign team |

The opt-out rule is the one exception that *does* route to a human, and that
is correct behaviour: it fires only on an explicit keyword, and an opt-out
request is exactly the thing a bot should not be handling. It implements
spec §7.3's opt-out requirement in the CRM half; the batch half (suppressing
staged offers for opted-out nasabah) is Phase 3.

### 5.2 Created inactive — the governance demos

These **do** silence the AI, so they ship switched off and are toggled on
deliberately — in a meeting, as a live demonstration of the control.

| Rule | Event | Condition | Action |
|---|---|---|---|
| `Consent ditolak — serahkan ke manusia` | `conversation_created` | contact `consent_marketing` = `false` | assign team `RM Prioritas` |
| `Nasabah prioritas — manusia lebih dulu` | `conversation_created` | contact `aum_band` = `> Rp 1 miliar` | assign team `RM Prioritas` |

**The demo beat:** set one nasabah's `consent_marketing` to `false`, switch
the rule on, send the same message again from the same handset. The AI that
was chatty thirty seconds ago is now silent and the conversation is sitting in
an RM's queue. Nothing about the model changed. That is the point.

### 5.3 Why the conditions avoid numbers

`days_since_last_transaction` is written by the seeder as a **string**
(`str(...)` in `client.py::build_nasabah_custom_attributes`), and
`rdn_balance` as a formatted string (`"Rp 82,500,000"`). A text-typed custom
attribute offers only `equal_to` / `not_equal_to` / `contains` /
`does_not_contain` / `is_present` / `is_not_present` — there is no
`greater_than`.

So the obvious rule — *"dormant more than 90 days"* — **cannot be written
today.** Two ways to fix it, and the second is the right one:

1. Redefine `days_since_last_transaction` as a Number attribute, which unlocks
   `greater_than`. Cheap, but it puts a threshold in the CRM that nobody
   versions.
2. **Have the batch compute a `dormancy_band`** (`Aktif` / `Pasif` /
   `Dormant`) and write it as a categorical attribute. The rule then reads a
   band, and the threshold that produced it lives in the warehouse where it is
   versioned, testable and explainable.

Option 2 is consistent with the tiering in spec §4.2: **the analytical tier
computes, the display tier renders, and the rule engine only matches.** Any
condition that wants arithmetic is a signal that the computation belongs
upstream. Tracked in §8.

---

## 6. Implementation notes that are not obvious

### 6.1 Use labels as the message bus, not `Send Webhook Event`

The rule engine has a `send_webhook_event` action, and it is the intuitive way
to call our service. **Don't.**

Have the rule write a label instead. We already receive `conversation_updated`
on every label write, on a path that is HMAC-verified
(`security.py::verify_chatwoot_signature`), deduped on `X-Chatwoot-Delivery`
(`dedupe.py::claim_delivery`), returns 200 immediately and dispatches to a
background task. That path has been carrying production traffic for months.

Going the webhook route means a second inbound surface whose signing behaviour
is unverified — it is not established that automation-rule webhooks carry the
same `X-Chatwoot-Signature` / `X-Chatwoot-Timestamp` headers as account
webhooks, and finding out costs a live experiment. The label route sidesteps
the question entirely and reuses code that already handles the hard parts.

**Rule of thumb: automation rules talk to us in labels.**

### 6.2 A custom attribute must be defined before a rule can reference it

Chatwoot's `condition_validation_service.rb` rejects a rule whose
`attribute_key` is neither a known filter key nor a row in
`custom_attribute_definitions` — matched on `attribute_model`, which defaults
to `conversation_attribute`. Two consequences:

- Contact-level conditions **must** send
  `"custom_attribute_type": "contact_attribute"`, or the lookup goes to the
  wrong model and the rule is rejected.
- `consent_marketing` did not exist as a definition, so the provisioning
  script creates it before creating the rules that reference it. Order
  matters.

### 6.3 Labels should be created, not conjured

The `add_label` action accepts an arbitrary string, and the tag will attach.
But a tag with no corresponding `Label` record has no colour and does not
appear in Settings → Labels, which makes the rule set look improvised. The
script creates the six labels first.

### 6.4 Known upstream issue

Chatwoot [#10377](https://github.com/chatwoot/chatwoot/issues/10377) reports
automation misbehaving with custom attributes in conditions. Treat every rule
in §5.1 as **unverified until observed firing on this tenant** — the
verification steps in §7 are not optional ceremony.

---

## 7. Provisioning and verification

```bash
# Token — never echo it; this is the runbook's own pattern
export CW_TOKEN=$(gcloud compute ssh crm-ticketing --zone=asia-southeast2-a \
  --project=lv-playground-genai --command='sudo grep -E "^CHATWOOT_API_TOKEN=" \
  /opt/platform/deploy/tenants/bahana.env | cut -d= -f2-')

export CHATWOOT_URL=https://bahana.crm.34-50-103-151.nip.io
export CHATWOOT_ACCOUNT_ID=1
export CHATWOOT_API_TOKEN="$CW_TOKEN"

python3 deploy/scripts/provision-bahana-automation.py            # dry run (default)
python3 deploy/scripts/provision-bahana-automation.py --apply
python3 deploy/scripts/provision-bahana-automation.py --remove   # clean undo
```

The script is idempotent — it matches existing rules by name and reports them
as `unchanged` rather than creating duplicates, so it is safe to re-run.

**Verification, in order:**

1. Settings → Automation lists eight rules; the two in §5.2 show as inactive.
2. Settings → Labels shows the six labels with colours.
3. Send a message from the demo handset. The conversation acquires
   `segmen-*`, `offer-staged`, and `nasabah-prioritas` where applicable.
4. The AI still replies. (If it does not, a rule assigned the conversation —
   check §5.2 is still inactive.)
5. Switch `Consent ditolak` on, set that contact's `consent_marketing` to
   `false`, send again → routed to a human, AI silent.
6. Switch it back off.

---

## 8. What the rule engine cannot do

Worth knowing before Bahana pushes on it, and worth saying out loud rather
than discovering in a POC:

- **No "ask the AI" action.** `send_webhook_event` is the only outward call;
  everything AI-shaped goes through our service.
- **Conditions are flat attribute matches.** No arithmetic, no scoring, no
  cross-conversation state. Scoring stays in the batch — see §5.3.
- **No time-based trigger.** Events only; there is no "three days after X".
  Snooze-and-reopen is the nearest native trick. Real time logic belongs in
  the batch job.
- **It cannot buy an outbound message.** The 24-hour window is Meta's
  constraint, not Chatwoot's. Automation can only act on conversations the
  nasabah has already opened — which is the same constraint the no-templates
  decision (spec §2.1) was built around.
- **No visual journey builder.** Qontak will likely demo a drag-and-drop
  canvas with drip sequences. Ours is a rule list, and side by side we lose on
  looks. The counter is substantive: their canvas fires fixed templates on
  fixed branches, ours makes a model decision per turn against live profile
  data — and we own the code, so a workflow they need that Qontak does not
  ship is a change we can make.
- **Past roughly twenty rules the list becomes unmanageable.** That is the
  signal to move logic into the agent service, not to keep adding rules.

---

## 9. Next

1. **`dormancy_band` in the batch** (§5.3) — unlocks the dormant-nasabah rules
   that are the most commercially obvious of the lot.
2. **Pattern C steps 3–5** — staged-offer resolution and the
   `offer_delivered_<id>` stamp, in `agent/app/services/`. Phase 2.
3. **Suppression in the batch** — the `opt-out` label must be read by whatever
   stages offers, or the CRM half of §7.3 is decorative. Phase 3.
4. **Outcome labels → warehouse** (Pattern D). Phase 3.
5. **Roll the pattern to other tenants.** Nothing here is Bahana-specific
   except the attribute vocabulary; proton and aeon360 have the same engine
   switched off.

---

## 10. 2026-08-25 — the offer stopped being a dead end

Added after the first live WhatsApp run of the demo. Recorded here rather than
in a new document because it changes something §3 and spec §4.3 assert.

### 10.1 What the live run showed

The bot answered every question correctly and then quit the moment the nasabah
declined the offer:

> **nasabah:** tapi saya ingin fokusnya ke saham ajaa, gimana yaa?
> **bot:** ...Ada hal lain yang bisa saya bantu?

Replaying the same four turns offline against all three personas
(`deploy/scripts/bahana_replay.py`) showed it was not a one-off: **all three
handed off on that same turn.** The Konservatif persona's handoff reason
quoted the cause almost verbatim — *"tidak dapat merekomendasikan produk di
luar penawaran hubungan yang sudah ditentukan"*.

That was `customer_context._OFFER_INSTRUCTIONS` doing exactly what it said:
*"You may only mention the offer named above."* One product, and the customer
had just said no to it, so the model had no legal move left.

### 10.2 The rule that changed

**From** "the model may name exactly one product" **to** "the model may name
any product from this customer's suitability-checked set".

The set is `product_gaps` — which both writers already compute as *eligible
for this risk profile, and not owned* (`nasabah._gaps_for`; the
`dim_offer_eligibility` join in `v_nasabah_profile`) — minus the staged offer.
No new data, no second suitability rule.

**The compliance guarantee is unchanged in substance.** Code still decides
which products are legal for this nasabah; the model still cannot reach one
that is not. A reviewer is still shown a suitability table, and it is still
the same table. What moved is only *how many* of the already-approved rows the
model may choose between — and the old answer of "one" was not a safety
property, it was a bug wearing a compliance costume. Pinned by
`test_forbids_reaching_outside_the_eligible_set`.

Behaviour when the nasabah wants something genuinely outside the set is now
specified rather than left to the model: say plainly that it does not match
the recorded risk profile, and offer an RM review of that profile. The
Konservatif persona asking for equities does exactly this — it does *not*
start pitching stocks.

### 10.3 Two smaller fixes in the same block

- **The profile is no longer recited.** Handed a labelled field list and no
  instruction, the model read it back as a labelled field list — which is why
  a freshly generated reply read as a template. It is now told to quote only
  the one or two details the question calls for.
- **`holdings_sectors`** now reaches the prompt. Sector lived only in
  `dim_instrument` and never left BigQuery, so the AI could say "concentrated
  in stocks" but never "two of your three holdings are banks". Derived, not
  stored (`nasabah.sectors_for`), because `PINNED_OVERRIDE` replaces holdings
  after generation and a stored field would go stale on the one record that
  is on screen. It has a SQL twin in `v_nasabah_profile`; the ordering rules
  match so the seeder and the sync job write byte-identical values.

### 10.4 The harness is the durable part

`deploy/scripts/bahana_replay.py` builds the **real** system prompt and calls
the **real** Gemini, faking only the transport. Before it, the only way to see
what the model said was to WhatsApp the Twilio number from a handset — one
persona, one turn at a time, a live 24-hour window per iteration. Every test
in `agent/tests` stubs the model, so the suite pinned plumbing and said
nothing about whether the conversation was any good.

    agent/.venv/bin/python deploy/scripts/bahana_replay.py --all

Every prompt change from here should be replayed across the population before
it goes near a handset — every §9 item touches this prompt.

Running full hello-to-escalation scripts (rather than single turns) then caught
three more defects in one pass: a bare "halo" answered with a product pitch,
`offer_rationale` — the CRM's internal note on why an offer was picked — recited
at the customer as though it were copy, and a request to place an IPO order
declined into a cul-de-sac instead of routed to a human. The fix for that last
one went in too broad on the first attempt and made the Moderat persona hand off
on "am I diversified?", which is the question the product exists to answer; it
is now scoped to carrying out a transaction. All four are pinned by tests.

### 10.5 The provisioner could never find its own team

Re-running the provisioner against bahana on 2026-08-26 to create the
`holdings_sectors` definition surfaced an unrelated idempotency bug worth
recording, because it had been silently latent since the team was first
created.

`ensure_team` matched on `team["name"] == "RM Prioritas"`. **Chatwoot lowercases
a team name on create**, so the account holds `rm prioritas` and the exact match
never hits — every re-run reported `CREATE team` and, under `--apply`, would
have tried to create a duplicate while all 8 rules were already correctly
assigned to team 1. The `--verify` output and the dry run disagreed with each
other, which is what made it visible: 8/8 rules present, yet the team they
assign to "did not exist".

The re-read fallback added in `6075460` carried the same `==`, so it could not
have repaired it either. Both now compare case-insensitively via `_find_team`.

Worth generalising: this class of bug only shows up on the *second* run, and a
provisioner is usually only run once per tenant. `--verify` disagreeing with
`--dry-run` is the cheapest signal there is.

Those scripts live in `deploy/scripts/demo-scripts/` and are the source of the
transcripts quoted in `docs/bahana-demo-guide-customer-v3.md` §5 — kept in the
repo so that document's claim to quote real output stays checkable. Each ends on
a different escalation trigger (customer asks / operational request / compliance
boundary), which is what v3 §6 tabulates.
