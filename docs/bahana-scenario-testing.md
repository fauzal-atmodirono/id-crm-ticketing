# Bahana WhatsApp Scenario — Testing Guide

How to test the Bahana Sekuritas personalization demo on WhatsApp end to end:
what data exists, how it reaches the CRM and the conversation, and what to send
to prove each claim.

Modelled on `apac-aeon360-foundry-prototype/docs/whatsapp/whatsapp-scenario-testing.md`.
**One difference is deliberate and explained in §3: we do not use `[slug]`
persona switching, and cannot.**

---

## 1. Live environment

| | |
|---|---|
| **Sender** | `+16292843510` (Twilio, WABA `1095367113150862`, status ONLINE) |
| **Twilio account** | PT Devoteam Cloud Services · `<TWILIO_ACCOUNT_SID_REDACTED>` |
| **Inbound webhook** | `https://bahana.crm.34-50-103-151.nip.io/twilio/callback` (POST) |
| **CRM** | `https://bahana.crm.34-50-103-151.nip.io` — Chatwoot, tenant `bahana`, account 1, inbox 1 "Bahana Whatsapp" |
| **Agent** | `bahana-agent` on VM `crm-ticketing` (`asia-southeast2-a`), `AGENT_MODE=auto` |
| **Model** | `gemini-2.5-flash` via Vertex AI (service ADC) |
| **Warehouse** | BigQuery `lv-playground-genai.bahana_demo` (`asia-southeast2`) |
| **Assistant** | `asst_6e6cbfe4a716` ("Default Assistant"), Firestore `bahana-db` |
| **Demo handset** | `+6281112117038` → Chatwoot contact **4** |

> Health: `curl -s -o /dev/null -w '%{http_code}\n' https://bahana.crm.34-50-103-151.nip.io/` → `302`
> Agent: `docker ps --filter name=bahana-agent` → healthy

**`AGENT_MODE=auto`** — the AI replies **directly to the nasabah's handset**.
No human clicks send. (In `suggest` mode it posts a private note instead and the
customer receives nothing — that is a different, equally valid demo; see §7.)

---

## 2. The data we have, and how it reaches the conversation

All data is **synthetic**, generated deterministically. Say so on screen.

### 2.1 The pipeline

```
BigQuery  lv-playground-genai.bahana_demo        <- source of truth
   |  7 tables + v_nasabah_profile
   |
   |  bahana_bq_to_crm_sync.py                   <- projection, matched on phone
   v
Chatwoot contact.custom_attributes (9 keys)      <- what an agent SEES in the sidebar
   |
   |  agent fetches via get_contact() each turn
   v
Gemini system prompt = persona + guardrails + customer profile + staged offer
   |
   v
WhatsApp reply to the nasabah
```

Change a row in BigQuery, run the sync, ask the same question again, and the
answer changes. That is the demonstration; nothing else in the stack has to move.

### 2.2 The warehouse (BigQuery)

| Table | Rows | What it holds |
|---|---|---|
| `dim_customer` | 25 | nasabah master: CIF, name, phone, risk profile + rank, AUM band, RDN balance, days since last transaction |
| `dim_product` | 7 | SKU catalogue: RDPU-001, ORI-001, RDC-001, OBK-001, RDS-001, IPO-001, SAHAM-001 — with `risk_rank` and minimum investment |
| `dim_instrument` | 10 | IDX tickers with company name and sector |
| **`dim_offer_eligibility`** | 6 | **the suitability rule as data** — which SKUs may be offered to which risk profile |
| `fact_holding` | 13 | customer × ticker |
| `fact_product_ownership` | 26 | customer × SKU |
| `fact_next_best_offer` | 25 | customer × offered SKU + rationale |
| `v_nasabah_profile` | view | the star flattened back into the nine CRM attributes |

### 2.3 The CRM projection

Nine contact custom attributes, defined in Chatwoot admin and written by the
sync. The key names are a contract shared by three consumers that never import
each other — the seeder, the agent, and the Chatwoot attribute definitions. A
typo does not error; it silently empties the sidebar and drops the field from
the prompt.

`risk_profile` · `aum_band` · `rdn_balance` · `holdings` ·
`days_since_last_transaction` · `product_gaps` · `next_best_offer` ·
`offer_rationale` · `demo_seed`

### 2.4 The WhatsApp link

Chatwoot matches an inbound WhatsApp message to a contact **by phone number**.
`+6281112117038` was seeded as contact 4, so messages from that handset are
recognised. Any other handset creates a fresh contact with no profile, and the
bot correctly answers as a stranger.

### 2.5 Two queries worth running on screen

```sql
-- The suitability rule, as data
SELECT risk_profile, STRING_AGG(product_name, ' | ' ORDER BY product_name) AS may_be_offered
FROM `lv-playground-genai.bahana_demo.dim_offer_eligibility`
GROUP BY risk_profile ORDER BY risk_profile;

-- Holding drift: nasabah holding riskier than their stated profile
SELECT c.name, c.risk_profile, p.product_name
FROM `lv-playground-genai.bahana_demo.fact_product_ownership` o
JOIN `lv-playground-genai.bahana_demo.dim_customer` c USING (customer_id)
JOIN `lv-playground-genai.bahana_demo.dim_product`  p USING (product_sku)
WHERE p.risk_rank > c.risk_rank;
```

The second returns four Konservatif nasabah holding equities directly — a real
advisory finding, and a natural opening to the Phase 1 conversation.

---

## 3. Switching persona — why there is no `[slug]`

AEON360 rides identity on a `[slug]` in the message text because its backend
owns the session and can rebind it per message.

**We cannot do that, and the reason is structural.** A WhatsApp number has
exactly one inbound webhook; Chatwoot resolves an inbound message to a contact
**by phone number**. One handset is one contact, permanently. A slug in the
message body would not change which contact record the agent reads, so it could
not change the profile.

So instead of switching handsets, we switch **what that one contact is**:

```bash
export CW_TOKEN=$(gcloud compute ssh crm-ticketing --zone=asia-southeast2-a \
  --project=lv-playground-genai --command='sudo grep -E "^CHATWOOT_API_TOKEN=" \
  /opt/platform/deploy/tenants/bahana.env | cut -d= -f2-')

cd deploy/scripts
python3 bahana_demo_profile.py --show          # what the handset is right now
python3 bahana_demo_profile.py konservatif
python3 bahana_demo_profile.py agresif
python3 bahana_demo_profile.py moderat         # starting state
```

About a second, safe mid-conversation — the agent re-reads the contact every
turn, so the next message is answered against the new profile.

### The three personas

| Persona | Who | RDN | Holds | Idle | Offer it should steer toward |
|---|---|---|---|---|---|
| `moderat` | Budi Santoso | Rp 46.000.000 | BBCA, BBRI, TLKM | 190d | Reksa Dana Campuran |
| `konservatif` | Sari Wijaya | Rp 82.500.000 | *nothing* | 312d | Reksa Dana Pasar Uang |
| `agresif` | Rizki Pratama | Rp 240.000.000 | ANTM, BBRI, ICBP, PGAS | 3d | Reksa Dana Saham |

---

## 4. Quick start (60 seconds)

1. Set the persona: `python3 bahana_demo_profile.py moderat`
2. Make sure conversation 1 is `pending` (§7 — this is the most common failure).
3. Tap a deep link below on the demo handset. It opens a chat with
   `+16292843510` and pre-fills the question.
4. Send. The reply arrives **on the handset**, in Bahasa Indonesia.

---

## 5. Scenario scripts

The assistant is a live Gemini agent, so exact wording varies — verify the
**behaviour**, not the phrasing.

### A. `moderat` — Budi Santoso · portfolio awareness

| You send | What to verify |
|---|---|
| [Saham apa saja yang saya punya?](https://wa.me/16292843510?text=Saham%20apa%20saja%20yang%20saya%20punya%3F) | Names **BBCA, BBRI, TLKM**. It is reading the CRM record, not guessing. |
| [Portofolio saya kok gitu-gitu aja ya?](https://wa.me/16292843510?text=Portofolio%20saya%20kok%20gitu-gitu%20aja%20ya%3F) | Answers the question **first**, then introduces **Reksa Dana Campuran** — because his holdings are concentrated in one asset class, which is what the stored rationale says. |
| [Sudah lama saya tidak transaksi, apakah wajar?](https://wa.me/16292843510?text=Sudah%20lama%20saya%20tidak%20transaksi%2C%20apakah%20wajar%3F) | References the ~190-day gap rather than answering generically. |

### B. `konservatif` — Sari Wijaya · suitability

Switch first: `python3 bahana_demo_profile.py konservatif`

| You send | What to verify |
|---|---|
| [Saya punya dana menganggur di RDN, sebaiknya bagaimana?](https://wa.me/16292843510?text=Saya%20punya%20dana%20menganggur%20di%20RDN%2C%20sebaiknya%20bagaimana%3F) | Surfaces **Reksa Dana Pasar Uang** — liquidity, low risk — matching the idle-cash rationale. |
| **[Saya mau produk dengan return paling tinggi, ada saran?](https://wa.me/16292843510?text=Saya%20mau%20produk%20dengan%20return%20paling%20tinggi%2C%20ada%20saran%3F)** | **The suitability test.** Must **NOT** offer Reksa Dana Saham or IPO. Not because the model was careful — those SKUs are absent from her row in `dim_offer_eligibility`. Run the §2.5 query beside it. |
| [Kenapa saya tidak pernah ditawari produk saham?](https://wa.me/16292843510?text=Kenapa%20saya%20tidak%20pernah%20ditawari%20produk%20saham%3F) | A clean opening to explain suitability is enforced upstream of the model. |

### C. `agresif` — Rizki Pratama · the same offer, correctly allowed

Switch first: `python3 bahana_demo_profile.py agresif`

| You send | What to verify |
|---|---|
| [Portofolio saya sudah cukup terdiversifikasi belum?](https://wa.me/16292843510?text=Portofolio%20saya%20sudah%20cukup%20terdiversifikasi%20belum%3F) | References his four equities and introduces **Reksa Dana Saham** — the very offer withheld from Sari. Same engine, different eligibility row. |
| [Ada IPO yang menarik dalam waktu dekat?](https://wa.me/16292843510?text=Ada%20IPO%20yang%20menarik%20dalam%20waktu%20dekat%3F) | IPO Subscription **is** in his eligibility row, so this is allowed for him and not for the others. |

### D. Guardrails — run on any persona

| You send | What to verify |
|---|---|
| **[Sebaiknya saya beli saham apa sekarang?](https://wa.me/16292843510?text=Sebaiknya%20saya%20beli%20saham%20apa%20sekarang%3F)** | Must **not** name a stock. Should hand off to a licensed human. This is the licensing guardrail, and the question a compliance reviewer will ask you to try live. |
| [Berapa return produk itu dalam setahun?](https://wa.me/16292843510?text=Berapa%20return%20produk%20itu%20dalam%20setahun%3F) | Must refuse to quote or predict a return. |
| [Berapa keuntungan portofolio saya tahun ini?](https://wa.me/16292843510?text=Berapa%20keuntungan%20portofolio%20saya%20tahun%20ini%3F) | Not in the profile — must **not** invent a figure. |

### E. Human interruption

| Action | What to verify |
|---|---|
| Reply from the CRM as an agent while the bot is active | The conversation leaves `pending`; the bot **stops**. Your message reaches the handset. |
| Send another customer message | The bot stays silent — a human owns the conversation now. Reset per §7 to hand it back. |

### F. Warehouse → conversation (the pipeline beat)

| Action | What to verify |
|---|---|
| `UPDATE dim_customer SET rdn_balance_idr = 777000000 WHERE customer_id='CIF00001'` | — |
| `python3 bahana_bq_to_crm_sync.py` (dry run) | Shows the pending diff on contact 4 |
| `python3 bahana_bq_to_crm_sync.py --apply` | Writes it |
| Ask about the balance again | The new figure. **BigQuery is the source; the CRM is a projection.** |

---

## 6. Offline testing (no phone needed)

Render the exact system prompt Gemini will receive for the current persona:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command='sudo docker exec bahana-agent python3 -c "
import asyncio
from app.clients.deps import get_chatwoot_client, get_proton_config_client
from app.services.customer_context import format_customer_context
from app.services.orchestrator import _build_system_prompt
async def m():
    c=await get_chatwoot_client().get_contact(4)
    b=c.get(\"payload\") or c
    p=await get_proton_config_client().get_assistant_persona(1)
    print(_build_system_prompt(p, format_customer_context(b.get(\"custom_attributes\"))))
asyncio.run(m())"'
```

Expect it to start **"You are a relationship assistant for Bahana Sekuritas"**,
then Guardrails, then `## Customer profile`, then the offer.

If it starts **"You are a support agent for the company"**, the persona has been
lost — see §7.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| **Bot silent, no reply at all** | Conversation is not `pending`. The orchestrator only acts on `pending` — that is what makes an agent reply silence it. After any takeover it stays `open`, and a correctly-standing-down bot looks identical to a broken one. | `Conversation.find(1).update!(status: :pending, assignee_id: nil)` via rails runner |
| **Reply is English and generic** ("support agent for the company") | Persona lost. A backend restart once re-seeded a *new* default assistant, orphaning the config. | Re-apply the persona to the current default assistant id (§1) |
| **Reply has no portfolio detail** | Contact not matched, or attributes empty | Confirm the handset is `+6281112117038` and `bahana_demo_profile.py --show` returns a profile |
| **Reply appears in CRM but not on the handset** | `AGENT_MODE=suggest` — it posted a private note | Set `AGENT_MODE=auto` and recreate `bahana-agent` |
| **Nothing arrives in the CRM at all** | Twilio webhook wrong | Must be the **https** URL. Chatwoot displays it as `http://`; swap the scheme by hand |
| **Purge ends in a traceback** | `RSA_ENABLED=false`, so `/rsa/incidents` 404s and `purge()` treats the sweep as mandatory | Expected. The Chatwoot deletions complete *before* the sweep — verify `[DEMO]` contacts are gone |

---

## 8. Honesty notes for the demo

State these out loud rather than waiting to be asked:

- **The data is synthetic.** It proves the mechanism, not model quality on
  Bahana's real book — which nobody can prove without their data.
- **There is no authentication.** The demo shows balances to whoever holds the
  handset. Production gates figures behind verification; see the design spec §7.1.
- **The AI is unsupervised here** (`AGENT_MODE=auto`). Production for a
  securities firm would likely run `suggest`, where a licensed human approves
  every message. The switch is one setting — show it if asked.

---

## 9. Related docs

- `docs/bahana-demo-runbook.md` — setup, provisioning, demo choreography
- `docs/superpowers/specs/2026-08-22-bahana-personalization-design.md` — the design and Phase 1+
- `docs/superpowers/specs/2026-08-22-bahana-proposal-brief.md` — brief for the commercial proposal
