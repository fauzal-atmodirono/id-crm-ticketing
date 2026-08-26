# Bahana — portfolio analytics, investor profiling, and RM-gated stock ranking

**Date:** 2026-08-26
**Status:** design
**Branch:** dev-yuda
**Supersedes nothing.** Extends `2026-08-22-bahana-personalization-design.md`
(referred to below as **the Phase 0 spec**). Where the two disagree, this
document wins for the three features it defines and the Phase 0 spec still
governs everything else.
**Audience:** the engineer who will build this, and the agent writing the
Phase 1 commercial proposal. §9 is the section to hand Bahana.

---

## 1. What was asked for

Internal team feedback, as a diagram:

```
Customer Profile + Risk Profile + Historical Transactions
+ Watchlist / Clickstream + Stock Characteristics + Market Conditions
        ↓
Personalized Stock Ranking → "15 Stocks for You This Week"

"What's your goal?" / "When will you need the money?"
/ "How would you react to -20%?" / "Experience?"
        ↓
Investor Profile (Risk = Moderate, Horizon = Long, Experience = Beginner)
        ↓
Personalized Starter Portfolio — BBCA 25%, BBRI 20%, BMRI 15%, TLKM 10%, …

System recognizes 60% exposure to financial sector
        ↓
Recommendation: diversification, rather than another banking stock
```

That is not one feature. It is three subsystems with different data
dependencies, different build costs and very different regulatory exposure,
and they are treated separately throughout:

| | Feature | Blocked on the data feed? |
|---|---|---|
| **A** | Position-level portfolio analytics → concentration and diversification | Yes |
| **B** | Conversational investor profiling → horizon, experience, goal | **No** |
| **C** | Personalized stock ranking + starter portfolio, RM-approved | Yes |

**A** is the feature closest to what exists and it still cannot be built
today: `fact_holding` is `(customer_id, ticker)` with no quantity and no
value, and `nasabah.sectors_for` orders sectors by **ticker count**, not by
weight. "60% Keuangan" is not a number this system can currently produce for
anyone. Positions are the missing primitive.

**C** collides head-on with the Phase 0 spec. §3.2 and §7.4 there, and
`customer_context._ELIGIBLE_INSTRUCTIONS` verbatim today — *"do not recommend
buying or selling any specific security"*, *"never quote a return, yield,
price, or fee"*. A ranked list of individual equities with target weights is
investment advice and portfolio management: licensed activity, not a
prompt-tuning problem. §4 and §7 below are how it becomes buildable rather
than how the rule gets relaxed.

---

## 2. Decisions taken

These four were settled before this document was written. They are recorded
here because each one closes off a large branch of the design space, and
anyone reading this later will otherwise re-open them.

**2.1 Built against the real back-office feed, not synthetic data.**
Phase 0 ran entirely on generated nasabah. This does not. Every table in §3
is a contract with a feed Bahana has not yet agreed to supply, which means
nothing in A or C ships until open question §9.1 of the Phase 0 spec is
answered. That is the accepted cost of not designing a second system to throw
away. **B and the RM surface are deliberately exempt** — see §8.

**2.2 Every security recommendation is approved by a licensed human before
it reaches a nasabah.** The engine ranks and drafts; a relationship manager
sees the list with its reasons and approves, edits, or kills it. This is the
Phase 0 spec's Phase 2 suggestion queue, promoted from roadmap line to
load-bearing component, and it is the same `AGENT_MODE=suggest` shape already
sold as a compliance feature. The bot never emits a ticker unreviewed.

**2.3 The conversational profile is authoritative for personalization only.**
Horizon, experience and goal shape tone, depth of explanation, and which of
the *already-eligible* products surface first. Suitability gating stays keyed
on the `risk_profile` from account opening — the regulated KYC artifact. A
customer cannot talk themselves into a riskier eligible set. §5.3 makes this
a test, not a convention.

**2.4 The ranking is LLM-assisted inside a hard deterministic boundary.**
This is a deliberate departure from the Phase 0 spec §4.3, which says the
"who" and "what" are never an LLM. §4.4 defines the boundary that keeps the
departure defensible, and §7.2 states plainly what is given up.

---

## 3. The data contract

Everything here is BigQuery, in the tenant's `bahana_*` dataset, and every
table is a landing zone for a feed. The existing star
(`deploy/scripts/bahana_bq_warehouse.py`) is extended, not replaced: same
`customer_id` CIF grain, same `dim_product` / `dim_offer_eligibility`
vocabulary, same `v_nasabah_profile` flattening contract that
`bahana_bq_to_crm_sync.py` projects onto Chatwoot contacts.

The warehouse's existing docstring already promises this seam — *"swap the
view's source from our synthetic table to Bahana's real back-office feed and
this script does not change at all."* This section is that promise made
specific.

### 3.1 New facts

**`fact_position`** — the missing primitive. Replaces `fact_holding` as the
source of truth for what a nasabah owns; `fact_holding` is retained as a
view over it so nothing that reads it today breaks.

| column | type | note |
|---|---|---|
| `customer_id` | STRING | CIF |
| `ticker` | STRING | → `dim_instrument` |
| `quantity` | INT64 | lot-adjusted units |
| `avg_cost_idr` | NUMERIC | |
| `market_value_idr` | NUMERIC | quantity × last close; the feed supplies it |
| `as_of_date` | DATE | partition key |

`market_value_idr` arrives from the feed rather than being computed here, on
purpose: the back office and our warehouse must not disagree about a
customer's portfolio value, and the only way to guarantee that is to not
compute it twice.

**`fact_transaction`** — "Historical Transactions". Grain: one settled order
or fund subscription/redemption.

`customer_id`, `transaction_id`, `instrument_ref` (ticker **or**
`product_sku`), `ref_kind` (`instrument` | `product`), `side`
(`buy`|`sell`|`subscribe`|`redeem`), `quantity`, `amount_idr`,
`transacted_at` TIMESTAMP, partitioned on date.

This retires `dim_customer.days_since_last_transaction` as a stored scalar —
it becomes derived, which removes a field that can go stale against the facts
beside it. Dormancy, trading frequency and realized behaviour all come from
here.

**`fact_engagement_event`** — "Watchlist / Clickstream", the only genuinely
new *kind* of data in the diagram.

`customer_id`, `event_at` TIMESTAMP, `event_type` (`watchlist_add`,
`watchlist_remove`, `quote_view`, `research_view`, `screener_run`,
`order_abandoned`), `ticker` NULLABLE, `source` (`app`|`web`), partitioned on
date.

Batch-loaded nightly. There is no hot-path need for this — nothing in a chat
turn reads raw events, only the nightly features derived from them — and
resisting a streaming ingest here saves an entire subsystem.

**Consent is not optional for this table.** Contact data and behavioural data
are different lawful bases under UU PDP 27/2022. Using what someone browsed
to target what they are offered is the textbook case a regulator asks about.
See §7.4.

### 3.2 Extended dimensions

**`dim_instrument`** gains the "Stock Characteristics" the diagram asks for:
`market_cap_idr`, `free_float_pct`, `avg_daily_value_idr` (liquidity),
`beta_1y`, `dividend_yield_pct`, `per`, `pbv`, `index_membership`
(`LQ45`|`IDX30`|`ISSI`|`none`), `is_sharia` BOOL, `as_of_date`. Sector stays
where it is — `nasabah.TICKER_SECTORS` is currently the single source of
truth for it and the warehouse builds `dim_instrument` from it; under a real
feed that inverts, and the feed becomes the source. Note the inversion
explicitly when it happens, because the seeder's docstring currently claims
the opposite.

**`fact_market_snapshot`** — "Market Conditions". `as_of_date`, `ihsg_close`,
`ihsg_return_20d`, `volatility_regime` (`low`|`normal`|`elevated`), plus a
per-sector companion `fact_sector_performance` (`as_of_date`, `sector`,
`return_1m`, `return_3m`).

Deliberately thin. Market conditions in this design tune *framing and
timing* — whether the weekly list leads with defensives, whether it goes out
at all in an elevated-volatility week — not stock selection. A market-timing
engine is a different product and a much larger regulatory claim.

**`dim_suitability_rule`** — the instrument-level twin of
`dim_offer_eligibility`. Enumerating every permitted ticker per risk profile
does not scale past a demo, so this is bounds rather than a list:
`risk_profile`, `max_beta`, `min_avg_daily_value_idr`, `min_market_cap_idr`,
`allow_non_index` BOOL, `require_sharia` BOOL.

It stays a **table**, not code, for the same reason `dim_offer_eligibility`
is a table: when Bahana's compliance officer asks how the AI is stopped from
putting a speculative small-cap in front of a conservative investor, the
answer should be a row they can read and a join, not a paragraph of Python.

### 3.3 New views

**`v_portfolio_exposure`** — `customer_id`, `sector`, `market_value_idr`,
`weight_pct`. This view is the entire content of feature A's arithmetic. It
is where "60% Keuangan" comes from, and because it is value-weighted it will
routinely disagree with today's count-weighted `holdings_sectors` string.
That is the point, and it is a behaviour change to a field already on screen:
`v_nasabah_profile.holdings_sectors` is re-pointed at this view so the string
the CRM carries is weight-ordered, and its docstring updated to say weight
rather than count. The Python twin, `nasabah.sectors_for`, serves the
synthetic population only and has no positions to weight by; it stays
count-based, and its docstring must say so rather than continuing to claim
the two are byte-identical, because under a real feed they no longer are.

**`v_candidate_universe`** — `customer_id` × `ticker`, one row per instrument
that survives every deterministic filter, carrying its feature columns and a
`base_score`. §4.2.

**`v_nasabah_profile`** gains `concentration_summary` (feature A),
`investor_horizon` / `investor_experience` (feature B). It keeps its
contract: whatever it selects is what `bahana_bq_to_crm_sync.py` writes onto
the Chatwoot contact and therefore what `customer_context._PROFILE_FIELDS`
can render. Those three files are a single contract in three languages and
the sync script's docstring already says so — change them together.

### 3.4 Serving tier

The Phase 0 spec §4.2 defines three tiers and warns that BigQuery must never
sit in the chat hot path. That constraint binds hardest here: a ranking join
across positions, engagement and instruments is seconds and costs money per
query, and a chat turn has milliseconds.

In the backend's per-tenant Postgres:

- **`nasabah_profile`** — keyed by phone, the materialized profile including
  concentration and investor preferences. Read on every chat turn.
- **`ranking_candidate`** — `customer_id`, `ticker`, `features` JSONB,
  `base_score`, `rank_batch_id`. Written by the nightly batch.
- **`suggestion`** — `id`, `customer_id`, `kind`
  (`stock_list`|`starter_portfolio`|`product_offer`), `payload` JSONB,
  `status` (`draft`|`approved`|`edited`|`rejected`|`sent`|`expired`),
  `rm_user_id`, `created_at`, `decided_at`, `decision_note`, `model_version`,
  `prompt_hash`, `rank_batch_id`.

`suggestion` is the regulated artifact. Everything a licensed human approved,
what they changed, and what the machine originally proposed, all retained.

---

## 4. Feature C — ranking and the starter portfolio

### 4.1 C splits in two, and the split matters

**C1, ranking** — which instruments, in what order. LLM-assisted (§4.3).

**C2, allocation** — BBCA 25%, BBRI 20%, BMRI 15%, TLKM 10%. **Deterministic,
never the LLM.** A percentage allocation is constrained arithmetic: weights
sum to 100, per-sector caps hold, per-position caps hold, the minimum
investment per `dim_product.min_investment_idr` is satisfiable at the
customer's RDN balance. A language model asked to produce this will
occasionally return weights that sum to 97, or breach the very concentration
cap the feature exists to enforce — and it will do so fluently.

The allocator takes the ranked list and the risk profile's target sector caps
and produces the weights. The LLM writes the *explanation* of the resulting
portfolio, from the numbers the allocator computed. That is the same division
the Phase 0 spec §4.3 already draws between choosing and phrasing; it is only
C1 that departs from it.

### 4.2 The candidate universe is deterministic

Before any model sees anything, `v_candidate_universe` has already applied:

1. **Suitability** — join `dim_suitability_rule` on the customer's
   **back-office** `risk_profile`. Beta, liquidity, market cap, index
   membership, sharia flag.
2. **Concentration constraint** — feature A as a filter. Any instrument whose
   sector already exceeds the configured weight ceiling for that customer
   (default 40%) is excluded outright. This is the diagram's *"diversification
   rather than another banking stock"*, expressed as a `WHERE` clause. A
   nasabah at 60% Keuangan cannot be shown BMRI, whatever any model thinks.
3. **Already-held** — existing positions excluded from a "new ideas" list.
4. **Feature computation** — sector-underweight gap, engagement affinity
   (watchlist and view counts from `fact_engagement_event`), transaction
   affinity, liquidity fit against the customer's typical ticket size, plus a
   `base_score` combining them with fixed published weights.

`base_score` is not decoration. It is the fallback order in §4.4 and the
thing an RM compares the model's ordering against when deciding whether to
trust it.

### 4.3 The model's job

Input: the profile, the concentration summary, the market snapshot in one
line, and the candidate set — capped at 40 instruments, each with its named
feature contributions.

Output: a **forced function call**, following `app/ai/tools.py`'s existing
pattern (`function_calling_config` mode `ANY`), returning an ordered list of
`{ticker, reason}`. Not free text. `app/ai/gemini.py` already falls back to
`handoff_to_human` when the model returns anything else; the ranking path
falls back to `base_score` order instead, since there is no conversation to
hand off.

The model contributes what the score cannot: reading the interaction between
a customer's stated goal, what they have actually been looking at, and what
they already own, and expressing the reason in language an RM can sanity-check
in five seconds.

### 4.4 The boundary, and what enforces it

| Guard | Enforced by |
|---|---|
| Cannot admit an instrument to the set | The set is the prompt; §4.5 validates the output back against it |
| Cannot breach suitability | Set is pre-filtered on the KYC risk profile (§4.2, filter 1) |
| Cannot worsen concentration | Set is pre-filtered on exposure (§4.2, filter 2) |
| Cannot compute the allocation | C2 is deterministic (§4.1) |
| Cannot reach a customer unreviewed | RM approval (§2.2, §6) |
| Cannot quote returns or prices | Output schema has no such field; validation drops unknown keys |

### 4.5 Validation

Every ticker the model returns is checked against the candidate set for that
customer and that `rank_batch_id`. Anything not in it is **dropped and the
drop logged** — the same containment `customer_context._eligible_alternatives`
gives the chat path today, applied to instruments.

If fewer than ten valid instruments survive, the batch falls back to
`base_score` order and marks the suggestion `model_fallback`. An RM seeing
that flag knows they are looking at arithmetic, not judgement.

`temperature=0`, model id pinned in config, and the full prompt, candidate
set, raw response and validation outcome recorded in `ai_actions` against the
`suggestion` row.

"15 Stocks for You This Week" is the top 15 after validation. If validation
leaves 12, the customer gets 12 — the count is a headline, not a contract,
and padding it from outside the candidate set is exactly the failure this
whole section exists to prevent.

---

## 5. Feature B — conversational investor profiling

The one part of this document that needs nothing from Bahana.

### 5.1 Shape

Four questions — goal, horizon, reaction to a 20% drawdown, experience —
asked by the agent in the existing WhatsApp conversation, one at a time,
in natural language rather than as a form read aloud. A new tool in
`app/ai/tools.py`, `record_investor_preference(goal, horizon,
drawdown_reaction, experience)`, is the only way the answers are captured;
the model does not summarise them into prose and hope something parses it.

The tool writes to the serving tier and to Chatwoot contact custom
attributes (`investor_goal`, `investor_horizon`, `investor_experience`,
`preference_captured_at`), which means the RM sees them in the sidebar with
no fork patch — the same free surface the Phase 0 spec §5.1 exploited.

### 5.2 What it changes downstream

`customer_context._PROFILE_FIELDS` gains horizon and experience, and
`_PROFILE_INSTRUCTIONS` gains a sentence tying explanation depth to the
recorded experience level: a Beginner gets what a reksa dana is, a
sophisticated investor does not get told.

Horizon enters C's `base_score` as a feature — a ten-year horizon tolerates
lower-liquidity, longer-thesis names within the same risk profile — and it
enters C2's allocator as a target-duration input for fixed-income weight.

### 5.3 What it must never change

`risk_profile`. The tool has no parameter for it and the serving-tier writer
does not accept it. A test asserts that no path from a chat message can alter
the field that gates eligibility, in the same spirit as
`test_forbids_reaching_outside_the_eligible_set`.

When the answers imply a risk tier above the one on file — someone who says
they would buy more at −20% but is recorded Konservatif — the batch applies a
`profile_review` label. The automation engine already routes on labels
(`docs/bahana-automation-personalization.md` §2), so this needs no new
plumbing. It notifies a human. It changes nothing.

---

## 6. The RM surface

A new Chatwoot fork patch, following exactly the shape of patches 0039
(escalation routing), 0040 (FAQ bulk upload) and 0041 (Customer 360): a Vue
admin page against a new backend router, RBAC-gated by `require_permission`
with a new `suggestion.review` permission.

The queue shows, per nasabah: the ranked list or the proposed portfolio,
each item's reason, the concentration picture that constrained it, whether
the model or the fallback produced it, and the customer's profile. The RM
approves, edits, or rejects with a note.

Approval stages the result onto the contact, at which point the existing
conversational machinery takes over unchanged: the staged suggestion becomes
context the orchestrator weaves into the next inbound message, exactly as
`next_best_offer` does today. Rejection is recorded with its reason, which
over time is the only honest signal available about whether the ranking is
any good.

**This is the expensive deploy.** A fork patch means Cloud Build for `amd64`
to Artifact Registry, then pull and `--force-recreate` on the VM — the one
path in this repo with real failure modes, and the path Phase 0 explicitly
cut to make its deadline. Budget it as such. It cannot be avoided by using
labels instead: approving a fifteen-item ranked list by applying a label is
not a workflow, and the suggestion queue *is* the direct answer to Bahana's
original "suggestion" ask, so under-building it undersells the product.

---

## 7. Compliance and security

The Phase 0 spec §7 still applies in full. These are the deltas this design
introduces.

### 7.1 Authentication stops being deferrable

Phase 0 knowingly disclosed an AUM band to an unverified WhatsApp sender and
narrated that as a gap. Position values, transaction history and a
personalized ranking are a different order of sensitivity. The two-tier model
in the Phase 0 spec §7.1 — unverified gets education and generic content,
verified gets figures — becomes a **blocking prerequisite** for anything in A
or C reaching a customer, not a Phase 3 item.

### 7.2 What LLM-assisted ranking gives up

Reproducibility becomes **replayability**, and that is weaker. With a
deterministic scorer, "the same inputs produce the same ranking" is a
property. With a model in the loop, even at temperature 0, it is a strong
expectation that a model version change can break. Mitigations: the model id
is pinned in config and changing it is a deliberate act; every ranking's full
inputs and raw output are retained; and `deploy/scripts/bahana_replay.py`,
which already replays conversations against the real prompt offline, is
extended to replay ranking batches so a model change is evaluated before it
ships rather than after.

State this honestly in the proposal. A securities firm's model-risk reviewer
will ask, and "we log everything and replay before upgrading" is a real
answer where "it's deterministic" would have been a false one.

### 7.3 The advisory boundary in the chat path

`customer_context._ELIGIBLE_INSTRUCTIONS` currently draws a line that this
design keeps and sharpens:

- Describing the customer's own record — what they hold, **how concentrated
  it is**, which sectors, how long since they traded — is reporting their
  data back to them. The bot does this itself. Feature A lives entirely on
  this side of the line.
- Naming a specific security to buy is advice. The bot never does this
  unprompted; it routes to the RM queue.

The instruction block needs the concentration case added explicitly, because
an unqualified "do not recommend buying or selling any specific security"
currently reads as forbidding the bot from telling a customer they are 60%
in one sector — which is the opposite of what feature A is for.

### 7.4 Consent for behavioural data

`fact_engagement_event` needs its own lawful basis and its own opt-out,
separate from contact-data consent. The batch checks it before an engagement
feature enters any score. A customer who declines behavioural targeting still
gets ranked — on positions and stated preferences alone, with the engagement
features zeroed.

### 7.5 Audit

`ai_actions` gains `suggestion_id` and `rank_batch_id`. The chain from a
sentence a nasabah received, back through the RM who approved it, to the
model call, to the candidate set, to the rows that produced it, has to be
walkable in one query. For this buyer that is a selling point, not plumbing.

---

## 8. Sequencing

Ordered so that the two stages needing nothing from Bahana come first — which
means work starts now rather than after a data negotiation concludes.

| Stage | Content | Feed? | Deploy cost |
|---|---|---|---|
| **1** | **B** — profiling tool, serving write, contact attributes, `customer_context` fields, the never-writes-`risk_profile` test | No | agent + backend, built on VM |
| **2** | **RM surface** — backend router, `suggestion` table, fork patch queue, built against fixtures | No | **Cloud Build** |
| **3** | Feed contract and ingestion — §3 tables landed, `v_portfolio_exposure` | **Yes** | batch only |
| **4** | **A** — concentration into `v_nasabah_profile`, sync, `customer_context`, §7.3 instruction change | Yes | agent, light |
| **5** | **C1/C2 deterministic** — candidate universe, `base_score`, allocator, suggestions into the queue from stage 2 | Yes | batch only |
| **6** | **C1 LLM ranker** — forced tool call, validation, fallback, `ai_actions`, replay-harness extension | Yes | batch + agent |

Stage 5 is a shippable product on its own. If the LLM ranker in stage 6 never
justifies itself, stages 1–5 are still the whole diagram minus the ordering
nuance, and the fallback path in §4.5 is already that system.

---

## 9. What Bahana must provide

The blocking list, and the section to put in front of them. Nothing in
stages 3–6 starts without these.

1. **Positions**, daily, with quantity and market value per instrument.
   Without this there is no concentration, no allocation, and no ranking —
   feature A alone is worth the feed.
2. **Transaction history**, ideally 24 months, settled orders and fund
   subscriptions/redemptions.
3. **Instrument reference data** with the §3.2 characteristics, or approval to
   source them ourselves and accept the resulting discrepancies with their
   own systems.
4. **Engagement events** from their app and website — the diagram's watchlist
   and clickstream. The most valuable and the least likely to exist in usable
   form. Design assumes it may never arrive: every engagement feature is
   nullable and the scorer runs without them.
5. **Identity resolution** — the mapping from WhatsApp phone number to CIF.
   Phase 0 spec §7.2. Without it, none of this reaches a conversation.
6. **A named owner for `dim_suitability_rule`** on their side. Somebody
   licensed signs off on the bounds. Unowned, §4.2's filter is our opinion
   wearing a compliance costume.
7. **Confirmation that RMs are licensed for the approval in §2.2**, and who
   they are.
8. **Where the data may live.** Phase 0 spec §7.6, now with real position
   data at stake.

---

## 10. Open questions

1. **Does the weekly list go out at all, or only on inbound?** The Phase 0
   spec's no-template rule (§2.1 there) means we cannot push "15 Stocks for
   You This Week" to WhatsApp. It is staged and consumed on the customer's
   next inbound message, which makes "this week" a claim we cannot keep for
   a customer who does not write in. Options: rename it to drop the cadence
   promise, deliver the weekly cadence over email or in-app where bulk is
   legitimate and use WhatsApp only for the conversation, or accept staleness
   with a visible as-of date. **Recommendation: email or in-app for cadence,
   WhatsApp for conversation** — which is the Phase 0 spec §2.3 position, and
   is worth deciding before the name "This Week" reaches a customer.
2. **Ranking cadence and cost.** Nightly for the whole book, or on-demand per
   RM request? A model call per nasabah per night across a real book is a
   real number; the deterministic `base_score` is nearly free. A plausible
   answer is score everything nightly and invoke the model only for the
   nasabah an RM opens.
3. **Does the starter portfolio imply discretionary management?** A proposed
   allocation a customer accepts and we then execute is a materially larger
   licensing claim than a suggestion. Needs Bahana's compliance answer before
   §6 gains an execute button.
4. **What happens to a suggestion nobody approves?** Expiry window, and
   whether an unreviewed list is silently dropped or escalated.
