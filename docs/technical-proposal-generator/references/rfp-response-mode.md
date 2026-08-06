# RFP / Tender Response Mode

Read this when the proposal answers a formal procurement — the user hands over a TOR/KAK, an RFP, or
aanwijzing minutes (Berita Acara Rapat Penjelasan), or the deal has bidders, merit scoring, and a
submission deadline. Everything here was learned on a live tender (Finnet KYC/B & Risk Engine, Jul 2026)
where each rule below was the fix for a real defect.

## 1. Document precedence — the BA is binding

Ingest, in this order: **TOR/KAK** (the requirements), **RFP** (procurement rules, evaluation, BoQ
format), **BA Rapat Penjelasan** (aanwijzing minutes), plus any prior RFI response for positioning.

**The BA overrides both.** RFPs state that the BA "bersifat mengikat dan merupakan bagian yang tidak
terpisahkan" — when the BA says 2 months and the RFP's scoring table says 5, the BA governs. Never
silently pick one: surface every conflict (duration, SLA numbers, concurrency) to the user with the
source of each figure, recommend the binding one, and record the decision. When two sources give
different targets for the same metric (TOR ≤1 s vs RFP ≤2 s), commit to the stricter in the proposal
and say so.

Extract from the BA specifically: who provides infrastructure, who executes the pen-test, payment
tranches, ATS start date, the Waslak / BAPP signer, NDA and SMAP obligations, and the real submission
deadlines. These routinely change what the TOR says.

## 2. Mirror the evaluation criteria

Pull the merit-system table out of the RFP (e.g. admin 5% / technical doc 15% / presentation 40% /
price 40%) and treat each scored criterion as a section the proposal must visibly answer: team
qualifications by named role, timeline fit, SLA compliance table, regulatory compliance
(APU-PPT / POJK / UU PDP / ISO 27001-SOC 2), post-implementation guarantees. A criterion with no
matching section is points left on the table. Note the deadline and submission channel in the Phase F
report — tender deadlines are hard.

## 3. Develop vs integrate — ask per component

The single most expensive framing error: writing "Development of X" when the vendor only integrates
with X. Client-owned channels, mobile apps, and internal dashboards (in the Finnet deal: DTP finpay.id,
Mitra Finpay Mobile, INTAN) are usually **integration** scope even when the TOR's own wording says
"pengembangan". Ask the user explicitly, component by component: *build, enhance, or integrate-only?*
Then keep scope, solution components, architecture narrative, AND the diagram telling the same story —
a "Developed by <vendor>" boundary tag on the diagram is the cleanest expression.

## 4. Scope completeness — every TOR chapter needs a home

Reviewer feedback that triggers rework: *"ruang lingkup belum lengkap sesuai KAK."* Before Phase F,
walk the TOR chapter by chapter and point to where each lands in the doc. The chapters that get missed
are never the module list — they are:

- the **API interface list** (TOR ~2.2): adapter APIs, decision-engine API, dashboard integration API,
  **updates to existing/legacy APIs**, per-adapter traffic monitoring, provisioning config with audit
  trail, tracing, **SSO** — put them in scope, not only in solution components;
- **infrastructure & licensing obligations** (TOR ~Bab 5): tool and vendor-data-provider licenses as
  vendor responsibility, platform-version disclosure, query list to the client's DBA before go-live,
  DB growth/retention/archiving lists, UU PDP compliance of vendor integrations;
- **documentation** (~Bab 8): beyond FSD/User Guide — the platform Technical Document and the
  **Fault Handling Document**, TK materials, periodic progress meetings;
- **governance** (~Bab 9): change requests via the Waslak, personnel qualifications with
  certifications, NDA (company + personal), SMAP ISO 37001.

## 5. Deliverables in standard names, mapped 1:1 to scope

Use the vocabulary evaluators grade against: **BRD, FSD, TSD** (architecture, flows, libraries, DB
relations, decision mechanism), API Integration Document, approved Test Plan, **SIT report, UAT
report**, QC & SQA reports, User Guide, SOP, Fault Handling Document, TK materials, source code
(ownership per BoQ), ATS. Then make the sprint plan emit them: each sprint bullet ends with
"outputs: …" so scope ↔ deliverables trace line by line. A deliverable no sprint produces, or a sprint
with no deliverable, is the gap reviewers find.

## 6. Commercial hygiene

Technical proposal and price (SPH/BoQ) are usually separate submissions — confirm, and state in the
doc that pricing is provided separately. **Never let internal pricing leak**: extend the Phase E
residual scan with the currency marker (`Rp`), `margin`, and the actual vendor unit prices from the
pricing sheet. Internal sheets carry partner-vs-list margin notes; those numbers must never appear in
any client-facing artifact, including diagram labels and image alt text (`--raw`).

## 7. Diagram altitude — ask, don't guess

One deal produced five diagram revisions because altitude was assumed. Offer the ladder explicitly and
let the user pick: (a) marketing overview grid, (b) delivery-scope focus (the modules the vendor
builds, client systems collapsed), (c) engineering topology (numbered flows, sync/async, queues,
protocols), (d) official GCP reference-architecture style (grey zones, icon + name + 2-4 word role,
minimal text). Expect to move down the ladder as reviewers engage. Rules that hold at every altitude:
strip version suffixes and internal footnotes (stencil compromises, "v3 edition") before the client
sees it; a vendor-scope boundary tag; simplifying the diagram does NOT imply simplifying the doc text —
confirm scope of any "remove service X" instruction (diagram-only vs whole proposal) before touching
prose or pricing.
