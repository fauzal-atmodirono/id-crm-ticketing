# Client Profile Extraction Rubric

Everything the proposal needs that is not Devoteam boilerplate comes from the client profile. This file is the
rubric for building that profile — from whatever scoping material exists (discovery notes, an RFP or Kerangka
Acuan Kerja, an email thread, a Google Doc, a pricing sheet, an architecture sketch), or from an interactive
interview when no document exists.

**The governing rule is at the bottom of this file: never invent a client fact.** Read it before you start
extracting, not after.

---

## 1. What to extract

Each row maps to the token it fills in the master template. Extract in this order — later fields depend on
earlier ones.

### Identity and framing

| What to extract | Token | Where it typically hides |
|---|---|---|
| Client legal entity name, including the `PT` / `PT (Persero)` / `Tbk` form | `{{CLIENT_LEGAL_NAME}}` | RFP cover page, NDA, contract header, email signature block, company website footer, the client's own letterhead |
| Client short name used in running prose | `{{CLIENT_SHORT_NAME}}` | The name people actually say in meetings — usually the legal name with `PT` and any suffix stripped |
| Project title | `{{PROJECT_TITLE}}` | RFP title, the subject line of the requesting email, the name of the budget line item |
| Proposal date | `{{PROPOSAL_DATE}}` | Not in the source material — ask, or use the intended submission date. Format it as `7 April 2026` to match the template. |
| Industry / sector | (feeds `{{PROBLEM_STATEMENT}}` and the Company Profile opening) | Obvious from the entity, but confirm the sub-sector: payments, multifinance, insurance, and retail banking have different regulators and different pains |

### The business problem

| What to extract | Token | Where it typically hides |
|---|---|---|
| Current state — what exists today, how it works | `{{PROBLEM_STATEMENT}}` | "Latar belakang" / "Background" section of an RFP; the first ten minutes of a discovery call; the "as-is" slide |
| The pain, with concrete evidence | `{{PROBLEM_STATEMENT}}` | Complaints in call notes; anything with a number attached — file sizes, row counts, hours per month, days to close, number of people involved |
| Why now — the trigger | `{{PROBLEM_STATEMENT}}` | A regulatory deadline, an audit finding, a system reaching a hard limit, an executive mandate, a budget cycle |
| Target end state and how it scales beyond the initial scope | `{{PROBLEM_STATEMENT}}` | "Tujuan" / "Objectives"; the client's own vision statement; what they say when asked "and after this?" |

### Solution shape

| What to extract | Token | Where it typically hides |
|---|---|---|
| Target Google Cloud / AI-ML services | `{{SOLUTION_COMPONENTS}}` | The pricing sheet is the most reliable source — every service with a cost line is in scope. Also the architecture sketch, and any service the client names unprompted |
| Architecture walk-through, source → destination | `{{ARCHITECTURE_NARRATIVE}}` | The architecture diagram; the ingestion discussion in discovery notes |
| Closing summary of what the architecture achieves | `{{ARCHITECTURE_SUMMARY}}` | Write this; it restates the problem statement's promise in architectural terms |
| Architecture diagram itself | `{{ARCHITECTURE_DIAGRAM}}` (anchor — never filled with text) | Generated from the deal's services with the `drawio-skill` and inserted at the anchor by `scripts/insert_diagram.py` — see SKILL.md **Phase D** for the full procedure, including deleting the legacy image and clearing the anchor |
| Data sources: systems, database engines, formats | `{{ARCHITECTURE_NARRATIVE}}`, `{{SCOPE_OF_WORKS}}` | "Sumber data" tables in an RFP; the DBA's list; the pricing sheet's Datastream or Dataflow line items |
| Data volumes and growth rate | `{{PROBLEM_STATEMENT}}`, `{{SCOPE_OF_WORKS}}` | Rarely stated precisely. Ask for: rows in the largest table, GB today, monthly growth, and daily change volume. If unavailable, `{{TBD}}` it — do not estimate |
| Target systems to integrate | (feeds `{{SCOPE_OF_WORKS}}` / `{{ARCHITECTURE_NARRATIVE}}` — not a document token) | "Integrasi" requirements; the list of downstream applications that consume the output |
| BI / consumption layer | (feeds `{{SCOPE_OF_WORKS}}` / `{{ARCHITECTURE_NARRATIVE}}` — not a document token) | Whatever the client already owns. Look for Tableau, Power BI, Looker, Qlik, or "Excel" in the current-state description |

### Commercial scope

| What to extract | Token | Where it typically hides |
|---|---|---|
| Scope items, phase by phase | `{{SCOPE_OF_WORKS}}` | "Ruang lingkup pekerjaan" in an RFP — often already structured as a numbered list you can mirror |
| Timeline: total duration and phase breakdown | Section 3.1 timeline table | "Jangka waktu pelaksanaan"; the client's go-live date working backwards; the budget period |
| Deliverables | `{{DELIVERABLES}}` | "Keluaran" / "Output" in an RFP; the acceptance criteria; anything the client says they need "for audit" |
| Out-of-scope items | `{{OUT_OF_SCOPE}}` | Rarely written down. Derive from what the client asked about and was told no, plus the standard four exclusions in the template. Every ambiguity surfaced in discovery that was not resolved belongs here |
| Managed-service intro framing | `{{MANAGED_SERVICE_INTRO}}` | Whether post-implementation support is being sold at all, and for how long |

### Devoteam-side people

| What to extract | Token | Where it typically hides |
|---|---|---|
| Service Delivery Manager | `{{SDM_NAME}}`, `{{SDM_EMAIL}}` | The engagement staffing plan, or ask the sales engineer directly |
| Technical Lead | `{{TECH_LEAD_NAME}}`, `{{TECH_LEAD_EMAIL}}` | As above |
| Technical Account Manager | `{{TAM_NAME}}`, `{{TAM_EMAIL}}` | As above — usually the account's standing TAM, not deal-specific |
| Level 3 escalation contacts (two people) | `{{ESCALATION_L3_NAME_1}}` / `_EMAIL_1`, `{{ESCALATION_L3_NAME_2}}` / `_EMAIL_2` | Standing Devoteam Indonesia escalation list; usually the same two names across proposals |
| Support email, portal URL, support timezone | `{{SUPPORT_EMAIL}}`, `{{SUPPORT_PORTAL_URL}}`, `{{SUPPORT_TIMEZONE}}` | Standing Devoteam values; see `boilerplate.md` block 7c for the timezone naming trap |

**These names must never be guessed.** They appear in a table the client will use to escalate a production
incident. A wrong name or a typo'd email address means an escalation goes nowhere. If the staffing is not
confirmed, `{{TBD — Devoteam Technical Lead}}` is the correct output.

---

## 2. Where to look, by source type

**RFP or Kerangka Acuan Kerja (KAK).** The most structured source and usually the most complete. Indonesian
RFPs commonly follow a fixed skeleton: Latar Belakang (background — feeds the problem statement), Maksud dan
Tujuan (purpose and objectives — feeds the target state), Ruang Lingkup Pekerjaan (scope of works — maps almost
directly to `{{SCOPE_OF_WORKS}}`), Keluaran (deliverables), Jangka Waktu Pelaksanaan (duration), and
Persyaratan (requirements — read this carefully for certification, local-presence, and residency conditions
that constrain the solution). Mirror the RFP's own scope structure in the proposal wherever possible;
evaluators score against their own list.

**Discovery call notes.** The richest source of the evidence that makes a problem statement credible — file
sizes, hours spent, error incidents, the name of the spreadsheet everyone depends on. Weakest on volumes,
timeline, and anything the client has not yet decided. Mine the notes for direct quotes and specific numbers,
then check each one back with the client before it goes into a customer-facing document.

**Pricing sheet or cost estimate.** The authoritative list of which services are actually in scope, and often
the only place data volumes appear (a BigQuery storage line implies a TB figure; a Datastream line implies
source databases and change volume). Cross-check the service list against the architecture narrative: any
service in the architecture but not in the price is a margin problem, and any service in the price but not in
the architecture is a credibility problem.

**Email threads.** Best for the timeline, the trigger ("why now"), the decision-makers, and constraints stated
in passing. Read the whole thread — the binding constraint is frequently in a one-line reply near the end.

**An existing architecture diagram.** Gives the data flow directly. Walk it left to right and turn it into the
`{{ARCHITECTURE_NARRATIVE}}` bullets in the order the source proposal uses: Data Sources → Data Ingestion and
Processing → Data Lake → Data Warehouse → Integration → Data Visualization → Data Governance → Other Services.
Keeping that order makes the narrative match the diagram the reader is looking at.

**No document at all — interview mode.** Ask in this sequence, because each answer constrains the next:
1. What does the business do, and which division is this for?
2. What happens today, step by step, and who does it?
3. What breaks, and how do you know — what does it cost you in time, money, or risk?
4. Why now? What changed, or what deadline exists?
5. What data, from which systems, and roughly how much?
6. Who consumes the output, and in what tool?
7. What must be true for this to be considered done?
8. What is explicitly not part of this?
9. When does it need to be live, and what drives that date?
10. Any regulatory, residency, or security constraints?

Stop and confirm the answers to 3 and 5 before writing. Those are the two that carry numbers into the
document.

---

## 3. Quality bar for the problem statement

`{{PROBLEM_STATEMENT}}` is the section that decides whether the proposal reads as bespoke or as a template
with a name changed. Use section 2 of the accepted Finnet proposal as the model. It runs five paragraphs with
a specific arc:

1. **Market context.** One paragraph locating the client in its industry and naming the pressure the industry
   is under — growth, competition, regulation. It ends by narrowing to the specific division and the specific
   challenge. *"…operates in a fast-evolving digital financial ecosystem where transaction volumes, merchant
   onboarding, and product variations continue to grow. Within this landscape, the Finance Division faces a
   specific and increasingly urgent challenge…"*

2. **The current state, in the client's own vocabulary.** Name the actual system, file, or process — the
   sample names the "Master Revenue (MR)" spreadsheet, its contents, its sources, and who maintains it. Using
   the client's internal names is the single strongest signal that the proposal came from a real conversation.

3. **The pain, with concrete evidence.** This is where the numbers go: file sizes, row counts, cycle times,
   manual effort, error rates. The sample cites an Excel file that has grown to roughly 770 MB and hundreds of
   thousands of rows, and connects that directly to slow, error-prone, manual reporting workflows that
   bottleneck the closing cycle. **A problem statement with no numbers in it has not been discovered
   properly** — go back and get them rather than writing around the gap.

4. **The proposed direction.** What is being built and what it changes, stated as an outcome rather than as a
   technology list: consolidate and structure the key datasets, standardize definitions and transformations,
   enable faster reporting through optimized queries and curated models, reduce manual dependency, provide a
   single trusted platform.

5. **How it scales beyond the initial scope.** Acknowledge the boundary of this engagement while showing the
   architecture is designed to extend past it. The sample scopes the initial build to one division while
   stating that the architecture and governance model will support a broader enterprise foundation later.
   This paragraph does two jobs: it protects scope, and it plants the follow-on deal.

**Length:** three to five paragraphs. Shorter reads thin; longer stops being read.

**Register:** formal, third person, benefit-led. No marketing superlatives, no exclamation, no "cutting-edge".
Assert what is true and let the numbers carry the weight.

**Test before accepting it:** if you replaced the client name with a competitor's, would the paragraphs still
be true? If yes, it is too generic — the specifics are missing.

---

## 4. Indonesia-market specifics

Capture these when relevant; they materially affect whether the proposal is credible to an Indonesian
enterprise buyer.

**Regulatory constraints.** For banks, payments companies, multifinance, insurers, and fintech, Otoritas Jasa
Keuangan (OJK) requirements shape the solution — and OJK-regulated buyers expect a partner who says so
unprompted. Bank Indonesia (BI) regulations apply to payment system operators. Personal data is governed by
Undang-Undang Perlindungan Data Pribadi (UU PDP), Indonesia's personal data protection law. Public sector and
state-owned enterprise buyers may carry additional Kominfo/Komdigi electronic system requirements.
<!-- verify: specific regulation numbers and current agency naming change; confirm the exact instrument before citing a regulation number in a client document -->
**Never cite a specific regulation number you have not verified.** Naming the regulator and the obligation in
general terms is credible; citing the wrong POJK number is worse than citing none.

**Data residency.** Ask explicitly whether data must remain in Indonesia. If it must, the architecture is
constrained to the Jakarta region (`asia-southeast1` is Singapore; `asia-southeast2` is Jakarta — confirming
which one the client means is worth doing early). Not every Google Cloud service is available in every region,
so a residency requirement can rule out a proposed component. Check regional availability for the specific
services in the architecture before committing to them — this is a common late-stage surprise on AI/ML
engagements in particular, where model and AI-service availability varies by region.

**Legal entity naming.** Use the full form on the cover page and in contractual sections: `PT` for a limited
liability company, `PT (Persero)` for a state-owned enterprise, `Tbk` appended for a publicly listed company.
Get this exactly right — it is the name that will appear on the contract. In running prose, the short name is
normal and preferable.

**Timeline table labels.** The section 3.1 timeline table in the master template uses Bahasa Indonesia phase
labels (Assessment Kondisi Eksisting, GAP Analisis, Analisis Kebutuhan Teknis, Requirement Dokumentasi, Desain
Arsitektur, Arsitektur Konseptual, Infrastruktur, Keamanan & Kepatuhan, Konfigurasi Infrastruktur, Data
Ingestion, Proses ETL, Optimasi Data Warehouse, Unit Testing, Integration Testing, Source Code Review, Security
Acceptance Test, User Acceptance Test, Dokumentasi, User Manual Pengguna, User Manual Teknikal, Soft Launch,
Transfer Knowledge). This mixed Indonesian/English register is normal and expected in Indonesian enterprise
documents — do not "correct" it to full English. When adding phases for an AI/ML engagement, follow the same
convention: keep established English technical terms in English (Data Ingestion, Feature Engineering, Model
Training, Model Evaluation, MLOps Pipeline, Model Deployment) and put the surrounding phase language in
Indonesian (Persiapan Data, Pengembangan Model, Pengujian Model, Serah Terima).

**Support hours and timezone.** Devoteam Indonesia's 8x5 support window is Jakarta business hours, so
`{{SUPPORT_TIMEZONE}}` is normally `WIB (Western Indonesia Time)` — Waktu Indonesia Barat, UTC+7. Use `WITA`
(Central, UTC+8) or `WIT` (Eastern, UTC+9) only if the client's operations genuinely sit in those zones. The
accepted source proposal contains an error here — it writes `WIT (Western Indonesia Time)` — so do not copy the
combination from the sample. See `boilerplate.md` block 7c.

**Language of the document.** Devoteam technical proposals are written in English, with Indonesian used for
timeline phase labels and for terms the client uses internally. If the RFP is in Bahasa Indonesia and requires
a Bahasa Indonesia response, that is a scope decision to raise before drafting, not something to resolve
mid-document.

---

## 5. The `{{TBD}}` rule

**Never invent a client fact.** Not a name, not a date, not a volume, not a system name, not a figure, not a
regulation number. If the scoping material does not contain it and the engineer has not supplied it, the
output is an explicit placeholder:

```
{{TBD — description of exactly what is missing and who can supply it}}
```

For example:

- `{{TBD — total size of the source Oracle database, from the client DBA}}`
- `{{TBD — Devoteam Technical Lead assigned to this engagement}}`
- `{{TBD — target go-live date, from the client project sponsor}}`
- `{{TBD — confirm whether data must remain in the Jakarta region}}`

Every `{{TBD}}` produced must be collected and reported back to the engineer as an explicit list, with the
section it appears in. The engineer resolves them before the document goes out; the list is the checklist for
doing that.

**Why the rule is absolute.** This is a customer-facing commercial document that becomes the basis of a
contract. A visible `{{TBD — target go-live date}}` costs the engineer thirty seconds and looks like a draft.
A confidently stated wrong go-live date, wrong data volume, or wrong escalation contact survives into the
signed scope, and is discovered when someone tries to rely on it. **A wrong figure is far more damaging than a
visible TODO.**

The same applies to plausible-sounding inference. "The client is a payments company, so they probably process
around ten million transactions a month" is invention, not extraction. If a number was not stated, it is
`{{TBD}}`.

The one legitimate form of derivation is arithmetic on stated facts — deriving a duration from a stated start
and end date, or an annual figure from a stated monthly one. Show the derivation to the engineer when
reporting, so it can be checked.
