---
name: technical-proposal-generator
description: Use when a Devoteam G Cloud sales or solutions engineer asks for a technical proposal, SOW, or statement of work for a Google Cloud, data, or AI/ML engagement — "write a technical proposal for [client]", "SOW for [client]", "statement of work", "proposal for the [client] data warehouse", "AI/ML proposal", "RFP response", "turn this RFP/KAK/discovery notes into a proposal", "draft the Devoteam proposal for X", "buatkan proposal teknis untuk [client]". Also trigger on bare shorthand like "proposal for [client]" and when the user links scoping docs, a pricing sheet, or discovery notes and asks for a client-ready proposal Doc. Do NOT use for Google Deal Acceleration Fund documents — that is daf-sow-generator.
---

# Technical Proposal Generator

Produces a **signature-ready Google Doc** — a Devoteam G Cloud technical proposal / SOW for a Google Cloud data or AI/ML engagement — by copying the master template and filling its 27 tokens with deal-specific content, plus a client-specific architecture diagram.

## Master template

| Field | Value |
|---|---|
| Doc ID | `1-VWnTqpeXdqF7IDFv7_R0UbXooJjWn00ULcllQLvY4E` |
| Name | `Devoteam G Cloud — Technical Proposal SOW — MASTER TEMPLATE` |

This is the default. Override only if the user explicitly points at a different template Doc. **Never edit the master** — every edit targets the copy made in Phase B.

Section-by-section map of what is tokenized vs. preserved: `references/template-structure.md`.

---

## Phase A — Intake interview (grill the user)

**This phase decides whether the proposal is credible.** The engineer asked for it explicitly: nothing about people, clients, or figures is hardcoded, so ask every time. **Never invent a client fact, name, date, volume, or figure** (`references/profile-extraction.md` §5).

**Shortcut first.** If the user links scoping material — RFP / Kerangka Acuan Kerja, discovery notes, a pricing sheet, an architecture sketch, an email thread — read it before asking anything, via the `gws-docs` and `gws-sheets` skills. Extract against the rubric in `references/profile-extraction.md` §1–§2, then **ask only about what is genuinely still missing**. Show the user what you extracted so they can correct it.

**Formal tender?** If the material includes a TOR/KAK, an RFP, or aanwijzing minutes (Berita Acara Rapat Penjelasan) — or there are bidders, merit scoring, and a submission deadline — read `references/rfp-response-mode.md` **before** the interview. Non-negotiables from it: the BA is binding and overrides TOR/RFP conflicts (surface every conflict, never silently pick); ask **develop vs integrate** per component (client-owned channels/dashboards are usually integration scope even when the TOR says "pengembangan"); walk every TOR chapter to a home in the doc before reporting done; name deliverables in evaluator vocabulary (BRD/FSD/TSD, SIT/UAT reports) and map them 1:1 to sprint outputs.

Ask in **four grouped rounds**, not 27 questions one at a time. State up front how many rounds there are.

**Round 1 — Client & deal.** Legal entity name in full (`PT …`, `PT (Persero)`, `Tbk` — this goes on the contract); short name used in prose; project title; proposal date (format `7 April 2026`); industry sub-sector (payments, multifinance, insurance, retail banking and public sector have different regulators and different pains).

**Round 2 — Problem & solution.** The business problem in the client's own vocabulary — name their actual systems and files. **Push for numbers**: rows, GB, cycle times, hours per month, error rates. A problem statement with no numbers has not been discovered properly. Then: target GCP / AI-ML services; data sources with engines and formats; target systems to integrate; BI / consumption layer; and whether data must stay in Indonesia (`asia-southeast2` is Jakarta, `asia-southeast1` is Singapore).

**Round 3 — Commercials & delivery.** Scope items phase by phase; total duration and phase breakdown; deliverables; explicit out-of-scope; whether managed services are being sold at all and for how long.

**Round 4 — Devoteam people.** SDM, Technical Lead, TAM, both L3 escalation contacts (names *and* emails), support email, support portal URL, support timezone. These land in a table the client will use to escalate a production incident — a wrong name or typo'd email means the escalation goes nowhere. Never guess one.

**Confirm, do not assume, on these three.** They are template defaults known to be stale or wrong:

- `{{SUPPORT_TIMEZONE}}` — the source proposal says `WIT (Western Indonesia Time)`, which is wrong: **WIB** is Jakarta/Western. Ask, and default to `WIB (Western Indonesia Time)`.
- `{{INDUSTRY_CREDENTIAL}}` — one sentence naming this deal's industry and its regulator (e.g. financial services / OJK). Must be tailored per deal. Never cite a regulation number you have not verified.
- **The §1 corporate figures** (talent counts, certifications, Partner-of-the-Year count) — carried over from an older proposal and unverified. Tell the user plainly to confirm them before sending.

**Anything the user cannot answer becomes a literal `{{TBD — what is missing and who can supply it}}` in the document**, and every one of them is listed back in the Phase F report. Say this to the user so they know the interview has an exit.

---

## Phase B — Copy the template

```bash
gws drive files copy \
  --params '{"fileId":"1-VWnTqpeXdqF7IDFv7_R0UbXooJjWn00ULcllQLvY4E","supportsAllDrives":true,"fields":"id,name,webViewLink,parents"}' \
  --json '{"name":"<CLIENT SHORT NAME> — Technical Proposal — <PROJECT TITLE> (Draft)"}'
```

Capture the new Doc ID and `webViewLink`. All later phases target the copy.

---

## Phase C — Fill the tokens

Two classes, handled differently. **This distinction is the core of the skill.**

**Scalar tokens (18 fillable)** — single values. One `batchUpdate` of `replaceAllText` via `scripts/apply_replacements.py`. Sort requests by **descending search-string length** first — not because one token name is a substring of another (every token is `{{…}}`-delimited, so none is), but so that a later, broader replacement cannot chew into a value already inserted (`known-gotchas.md` #11).

> **Never put `{{ARCHITECTURE_DIAGRAM}}` in the fill map.** It is an *anchor*, not a value — Phase D locates it to position the diagram. Replacing it with text destroys the insertion point. It is the one `{{…}}` that legitimately survives the scalar pass, and **Phase D step 3b** is what removes it — nothing in Phase E does, Phase E only verifies. If step 3b is skipped, `Full image: {{ARCHITECTURE_DIAGRAM}}` ships to the client and Phase E's zero-`{{` assertion can never pass.

**Narrative block tokens (8)** — `{{PROBLEM_STATEMENT}}`, `{{ARCHITECTURE_NARRATIVE}}`, `{{ARCHITECTURE_SUMMARY}}`, `{{SOLUTION_COMPONENTS}}`, `{{SCOPE_OF_WORKS}}`, `{{DELIVERABLES}}`, `{{OUT_OF_SCOPE}}`, `{{MANAGED_SERVICE_INTRO}}`. Each occupies **one plain paragraph** in the template but must expand into many paragraphs with headings and bullets. `replaceAllText` cannot do this — it inserts plain text only and interprets no Markdown.

**Fill the narrative blocks before the scalar pass**, so scalars nested inside inserted prose (e.g. `{{CLIENT_SHORT_NAME}}`) still resolve.

**Default path is programmatic styling** — do not dump plain text and leave the engineer to restyle:

1. `insertText` the full multi-paragraph block at the token's location, then delete the token text.
2. **Re-fetch the doc** — pre-insert indices are now stale.
3. Locate the inserted paragraphs by their text; apply `updateParagraphStyle` (`HEADING_3` / `HEADING_4`) to component headings and `createParagraphBullets` (`BULLET_DISC_CIRCLE_SQUARE`) to list runs, **back-to-front**.

This is a known-fiddly step whose failure mode is a visibly malformed customer-facing doc. Read `references/known-gotchas.md` **#12** before starting; it also documents the legitimate fallback (clean plain text + an explicit hand-off list of sections to restyle by hand). What is never acceptable is literal `##` or `**` reaching the client.

**Content sourcing:**
- `{{SOLUTION_COMPONENTS}}` — assemble from `references/solution-components.md`, selecting **only** the components in this deal's scope, in the data-flow order defined at the top of that file (ingest → store → transform → govern → model → serve → consume), which is not the file's physical order. Do not paste the library.
- `{{PROBLEM_STATEMENT}}` — 3–5 paragraphs following the five-part arc in `references/profile-extraction.md` §3.
- The rest — write in house style from the intake answers.

**`CLIENT_TARGET_SYSTEMS` and `BI_TOOL` are generation inputs, not document tokens.** They feed the prose of `{{SCOPE_OF_WORKS}}` and `{{ARCHITECTURE_NARRATIVE}}`. Putting them in the fill map produces spurious unmatched-token errors.

---

## Phase D — Architecture diagram

The template still carries the **legacy inline image** from the source proposal, sitting directly above the `Full image: {{ARCHITECTURE_DIAGRAM}}` anchor in §2.2. It is a Finnet-era diagram and **must not reach a customer**.

1. **Generate** a client-specific GCP diagram with the `drawio-skill` (`drawio` CLI is installed, v30.0.4). Components from the deal's actual services; official Google Cloud icons; tier layout Sources → Ingestion → Storage/Lake → Warehouse/Processing → AI/ML → Consumption, with governance / security / observability as a cross-cutting band. Soft cap: 2 self-review rounds. **Ask the user which altitude they want first** (`references/rfp-response-mode.md` §7 — marketing grid / delivery-scope focus / engineering topology / GCP reference style); assumed altitude is the top cause of diagram rework. Export with the long-form `--width 1600` flag — the short `-w` makes the drawio CLI fail with "input file/directory not found" (and exit 0). Before the PNG touches the Doc, strip anything internal from the canvas: version suffixes in the title, stencil-compromise footnotes, TODO notes — those belong in the report, never in a client artifact.
2. **Delete the legacy image.** Image *content* cannot be replaced (`known-gotchas.md` #9), but an inline image occupies one index in the body, so a `deleteContentRange` over that index removes it.
3. **Insert the new PNG** at the anchor with `scripts/insert_diagram.py` (uploads to Drive, makes it link-readable, inserts inline). If the API rejects the image ("problem retrieving the image" / "access forbidden"), fall back to the `lh3.googleusercontent.com/d/<ID>=w1600` URL form with anyone-with-link sharing, and add a `"\n"` after the inserted image so it is not glued to the next paragraph — full recipe in `known-gotchas.md` #14 (which also covers cheap image *swaps* on later revisions).

   **3b. Clear the anchor text — mandatory.** `insertInlineImage` places the image next to the anchor; it does **not** consume it, and nothing else in this skill deletes it. Once step 3 has succeeded, issue one more `replaceAllText` with `containsText: "{{ARCHITECTURE_DIAGRAM}}"` and `replaceText: ""`, so the `Full image:` line no longer carries a raw token into a customer document.

   - **If no diagram was inserted** (step 1 or step 3 failed), replace it with `{{TBD — architecture diagram}}` instead of the empty string, so the gap is visible in the Phase E scan and the Phase F report rather than silent.

4. **Graceful degradation.** If generation or insertion fails: still upload the `.drawio` source to Drive next to the Doc, leave a `{{TBD — architecture diagram}}` in the anchor's place (step 3b), and flag it prominently in the Phase F report with the Drive URL. If step 2 succeeded but step 3 failed, **say so** — the doc now has no diagram at all. Never leave the legacy image silently in place, and never claim a diagram was inserted when it was not.

---

## Phase E — Verify

Run `scripts/verify_residuals.py` against the finished Doc. Assert:

- **Zero** remaining `{{` other than deliberate `{{TBD — …}}` placeholders (and scan independently for a dangling `}}`). Pass `--allow-prefix '{{TBD'` so the deliberate placeholders are subtracted from the residual count and reported separately as open items instead of failing the run;
- zero `Finnet`, `Fincx`, `Tableau`, and the old PIC surnames — add `--raw` so hyperlink URLs, smart-chip metadata and image alt-text in the raw Docs JSON are scanned too, not just the flattened body text;
- present: client legal name, client short name, project title, and each in-scope GCP service.

```bash
python scripts/verify_residuals.py <DOC_ID> --raw \
  --residuals Finnet Fincx Tableau '{{' '}}' \
  --required '<CLIENT_SHORT_NAME>' BigQuery \
  --allow-prefix '{{TBD'
```

A leftover `{{TOKEN}}` in a customer-facing document is a **hard failure** — fix and re-scan. `occurrencesChanged` from the batch reply is not evidence; the re-fetched document is.

Two situational adjustments:

- **When the client IS the template's origin client** (a new Finnet deal), the `Finnet` residual check inverts: move it to `--required` and scan instead for the *other* legacy strings (`Fincx`, `Tableau`, old PIC surnames) plus anything stale for this deal (e.g. the old deal's module names, `Data Lake & Data Warehouse` Gantt labels).
- **On tenders with an internal pricing sheet**, extend `--residuals` with `Rp`, `margin`, and the actual vendor unit prices — internal partner-vs-list margins must never reach a client artifact (`references/rfp-response-mode.md` §6). A raw-JSON hit on `margin` is usually the `marginLeft` style property and a raw `Rp` can sit inside an image-URL token: grep the raw JSON for context before declaring a leak.

---

## Phase F — Report

- Doc URL.
- Sections filled, and which narrative blocks were styled programmatically vs. left for manual restyling.
- **Diagram status** — inserted / degraded / legacy image deleted but not replaced.
- **Every `{{TBD}}` left**, quoted with its section.
- **Template defects to eyeball before sending:** the §6.1 heading says "Enhanced Support" while the body describes "Premium Support" (different Google Customer Care tiers, different commitments and prices); and the unverified §1 Devoteam corporate figures.
- Residual-scan output. Never report the proposal finished without it.

---

## Quick reference — the 27 tokens

**Scalar (19 = 18 fillable + 1 anchor that is never filled)** — one `replaceAllText` pass:

| Token | × | Value |
|---|---|---|
| `{{PROJECT_TITLE}}` | 1 | Short project name, e.g. `Finance Data Warehouse` |
| `{{CLIENT_LEGAL_NAME}}` | 2 | Full legal entity, `PT …` / `Tbk` |
| `{{CLIENT_SHORT_NAME}}` | 3 | Name used in running prose |
| `{{PROPOSAL_DATE}}` | 1 | `7 April 2026` format |
| `{{INDUSTRY_CREDENTIAL}}` | 1 | One sentence: industry + regulator familiarity |
| `{{ARCHITECTURE_DIAGRAM}}` | 1 | **DO NOT FILL** — anchor; Phase D step 3b deletes it |
| `{{SUPPORT_TIMEZONE}}` | 2 | Default `WIB (Western Indonesia Time)` |
| `{{SUPPORT_PORTAL_URL}}` | 1 | Devoteam support portal |
| `{{SUPPORT_EMAIL}}` | 2 | Devoteam support inbox |
| `{{SDM_NAME}}` / `{{SDM_EMAIL}}` | 2 / 2 | Service Delivery Manager |
| `{{TECH_LEAD_NAME}}` / `{{TECH_LEAD_EMAIL}}` | 1 / 1 | Technical Lead |
| `{{TAM_NAME}}` / `{{TAM_EMAIL}}` | 1 / 1 | Technical Account Manager |
| `{{ESCALATION_L3_NAME_1}}` / `{{ESCALATION_L3_EMAIL_1}}` | 1 / 1 | First L3 escalation contact |
| `{{ESCALATION_L3_NAME_2}}` / `{{ESCALATION_L3_EMAIL_2}}` | 1 / 1 | Second L3 escalation contact |

**Narrative blocks (8)** — insert-then-style:

| Token | Section | Content |
|---|---|---|
| `{{PROBLEM_STATEMENT}}` | §2 | 3–5 paragraphs: market context → current state → pain with numbers → direction → how it scales |
| `{{ARCHITECTURE_NARRATIVE}}` | §2.2 | Source → destination walk-through of the diagram, bulleted by tier |
| `{{ARCHITECTURE_SUMMARY}}` | §2.2 | Closing paragraph: what the architecture achieves |
| `{{SOLUTION_COMPONENTS}}` | §2.3 | Per-component write-ups with `HEADING_3`/`HEADING_4`, in-scope components only |
| `{{SCOPE_OF_WORKS}}` | §2.4 | Scope items, phase by phase |
| `{{DELIVERABLES}}` | §3.2 | Intro sentence + deliverable bullets |
| `{{OUT_OF_SCOPE}}` | §4 | Explicit exclusions |
| `{{MANAGED_SERVICE_INTRO}}` | §6.3 | One-paragraph framing above the preserved maintenance bullets |

---

## Common mistakes

- **Editing the master template instead of the copy.** Every batchUpdate after Phase B targets the *new* Doc ID.
- **Leaving the legacy Finnet-era diagram image in §2.2.** Delete it in Phase D even when the replacement fails.
- **Pasting the whole component library** into `{{SOLUTION_COMPONENTS}}`. Select only in-scope components.
- **Inventing PIC names, client figures, dates, or regulation numbers.** `{{TBD — …}}` always beats a confident guess; a wrong escalation contact survives into the signed scope.
- **Trusting `occurrencesChanged`** instead of re-fetching and running the residual scan. Several tokens legitimately match more than once — check against the expected count in the table above, not against 1.
- **Filling scalars before narrative blocks**, which leaves live tokens inside prose inserted afterwards.
- **Adding `CLIENT_TARGET_SYSTEMS` / `BI_TOOL` to the fill map.** They are not in the Doc.
- **Leaving literal Markdown (`##`, `**`) in the Doc** because programmatic styling was skipped. Strip it and hand off the restyling explicitly.
- **`gws … | tail -n +2`.** The `Using keyring backend` line goes to **stderr**, so stdout is already clean JSON and this idiom eats the first line of it. A plain `> doc.json` redirect is all that is needed (`known-gotchas.md` #6).
- **Writing "Development of X" for a client-owned system.** Channels, mobile apps and internal dashboards are usually *integration* scope even when the TOR says "pengembangan" — ask develop-vs-integrate per component (`references/rfp-response-mode.md` §3) and keep scope, components, narrative and diagram consistent once answered.
- **Scope and deliverables that don't reconcile.** Every deliverable must be emitted by a named sprint/phase, and every TOR chapter must land somewhere in the doc — "ruang lingkup belum lengkap sesuai KAK" is the reviewer comment you get otherwise (`references/rfp-response-mode.md` §4–§5).
- **Trusting one source when TOR, RFP and BA disagree.** The BA (aanwijzing minutes) is binding; surface the conflict and the chosen figure instead of silently picking (`references/rfp-response-mode.md` §1).
- **Shipping a diagram with internal notes on the canvas** — version-suffixed titles, stencil-compromise footnotes. Strip before insert; notes go in the report.

---

## Bundled resources

**Scripts**
- `scripts/inspect_doc.py` — dump paragraph + table structure with indices; use it to locate token paragraphs and the legacy image index.
- `scripts/apply_replacements.py` — POST a `{"requests":[…]}` JSON file to `documents.batchUpdate`, reporting per-request `occurrencesChanged` and flagging zero-matches.
- `scripts/insert_diagram.py` — upload a PNG to Drive, make it link-readable, insert it inline at an anchor.
- `scripts/verify_residuals.py` — Doc ID + residual terms + required terms → pass/fail report; exits 1 on any failure.

**References**
- `references/template-structure.md` — annotated section-by-section map of the template: what is preserved, what is tokenized, which tables and paragraphs must survive intact.
- `references/profile-extraction.md` — extraction rubric, the problem-statement quality bar, Indonesia-market specifics, the `{{TBD}}` rule.
- `references/solution-components.md` — 22 proposal-ready GCP / AI-ML component write-ups, each with a "when to include" test.
- `references/boilerplate.md` — the eight reusable Devoteam prose blocks already in the template; for review, rebuild, and exact token spellings.
- `references/known-gotchas.md` — Docs API and `gws` failure modes. #9 images, #11 token order, #12 multi-paragraph expansion are the ones this workflow depends on; #13 table rebuild/fill order, #14 image URL + swap recipe, #15 bullet-inheriting appends, #16 reading un-downloadable .docx.
- `references/rfp-response-mode.md` — formal tender mode: document precedence (BA binding), evaluation-criteria mirroring, develop-vs-integrate, TOR completeness checklist, deliverable naming (BRD/FSD/TSD/SIT/UAT), pricing-leak hygiene, diagram altitude ladder.
