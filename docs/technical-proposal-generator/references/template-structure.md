# Master Template — Annotated Structure

Map of the master template Google Doc so a future agent can navigate it without re-deriving it from the
document JSON.

| Field | Value |
|---|---|
| Doc ID | `1-VWnTqpeXdqF7IDFv7_R0UbXooJjWn00ULcllQLvY4E` |
| Name | `Devoteam G Cloud — Technical Proposal SOW — MASTER TEMPLATE` |
| Body length | ~17,048 chars (the source proposal it was cut from was 48,905) |
| Top-level body elements | 151 |
| Distinct tokens | 27, across 34 occurrences |

The template was produced by genericizing an accepted Devoteam proposal: every client-specific block was
collapsed into a single token paragraph, and every client string, personal name, email address, portal URL,
and Drive smart chip was removed. Headers and footers contain only a page number. All counts below are
**empirically verified against the live Doc**, not inferred from the source proposal.

**Working rule:** anything not listed as tokenized in the table below is preserved boilerplate. Leave it
alone unless the deal genuinely requires a variation, and if it does, check the variation against
`boilerplate.md` so it is deliberate rather than accidental.

---

## 1. Section-by-section map

| Section | Contains | Status | Tokens |
|---|---|---|---|
| **Front matter (cover)** | `For {{PROJECT_TITLE}} in Google Cloud Platform`, `Prepared for:`, `Prepared by: Devoteam G Cloud`, `Date:` | Tokenized (scalars inline) | `{{PROJECT_TITLE}}`, `{{CLIENT_LEGAL_NAME}}`, `{{PROPOSAL_DATE}}` |
| **§1 Company Profile** | Devoteam corporate narrative, talent/certification/Partner-of-the-Year figures, Devoteam and Google partner logos | **Preserved** except one sentence | `{{INDUSTRY_CREDENTIAL}}` (last sentence of the first paragraph) |
| **§2 Proposed Solution** | The business problem and the case for the engagement. In the template this is one empty paragraph where five source paragraphs used to be. | Tokenized (block) | `{{PROBLEM_STATEMENT}}` |
| **§2.2 Proposed Architecture** | Legacy inline architecture image, the `Full image:` anchor line, the tier-by-tier walk-through (was 24 bulleted paragraphs), and a closing summary paragraph | Tokenized (blocks) + legacy image to delete | `{{ARCHITECTURE_DIAGRAM}}`, `{{ARCHITECTURE_NARRATIVE}}`, `{{ARCHITECTURE_SUMMARY}}` |
| **§2.3 Solution Components** | Four generic Google Data Cloud intro paragraphs (BigQuery / self-service analytics / separation of compute and storage / meeting users at their level), then the per-component write-ups | Intro **preserved**; components tokenized | `{{SOLUTION_COMPONENTS}}` sits *after* the four intro paragraphs and replaces all of §2.3.1–§2.3.5 (86 paragraphs incl. sub-headings) |
| **§2.4 Scope of Works** | Preserved lead-in `The following Project has been identified to address the following scope:`, then the scope list | Lead-in preserved; list tokenized | `{{SCOPE_OF_WORKS}}` |
| **§3 Project Timeline & Deliverables** | Section heading only | Preserved | — |
| **§3.1 Timeline** | Bahasa Indonesia Gantt table, **33 rows × 15 cols** | **Preserved intact** — no token, no client data ever in it | — |
| **§3.2 Deliverables** | Intro sentence and deliverable bullets, collapsed to one paragraph | Tokenized (block) | `{{DELIVERABLES}}` |
| **§4 Out of Scope** | Preserved lead-in `Following activities and tasks are considered out of scope`, then the exclusion list | Lead-in preserved; list tokenized | `{{OUT_OF_SCOPE}}` |
| **§5 Project Implementation** | Section heading only | Preserved | — |
| **§5.1 Agile Methodology** | Agile narrative + agile graphic image | **Preserved verbatim** | — |
| **§5.2 Change Management & Project Communication** | Change-control prose naming the client twice | **Preserved** prose, client name tokenized | `{{CLIENT_LEGAL_NAME}}` (×1 here), `{{CLIENT_SHORT_NAME}}` (×1 here) |
| **§5.3 Channels** | Communication-channel prose | **Preserved verbatim** | — |
| **§5.4 GCP Service Level Agreement** | SLA table, **5 × 3** | **Preserved intact** — contractual | — |
| **§6 Post Implementation (Optional)** | Section heading only | Preserved | — |
| **§6.1 GCP Enhanced Support** | Google Customer Care feature prose + priority/response table **5 × 2** | **Preserved intact** — contractual. See defect note below. | — |
| **§6.2 Devoteam Support and Managed Services** | Opening statement, Incident Management RACI **8 × 3**, Devoteam SLA **5 × 4** with two footnotes, support contact channels, Service Level Infrastructure Uptime disclaimer, Escalation Flow **4 × 2**, Hierarchical Escalation bullets + SLA-consumption matrix **6 × 5**, Roles **4 × 2**, both `Notes :` disclaimers | Prose, tables and disclaimers **preserved**; only the contact values are tokenized | `{{SUPPORT_TIMEZONE}}` ×2, `{{SUPPORT_PORTAL_URL}}`, `{{SUPPORT_EMAIL}}` ×2, `{{SDM_NAME}}` ×2, `{{SDM_EMAIL}}` ×2, `{{ESCALATION_L3_NAME_1}}`/`_EMAIL_1`, `{{ESCALATION_L3_NAME_2}}`/`_EMAIL_2`, `{{TECH_LEAD_NAME}}`/`_EMAIL`, `{{TAM_NAME}}`/`_EMAIL` |
| **§6.3 Scope of Works for Maintenance and Managed Service** | Intro sentence, then Preventive / Corrective / Managed Services bullet tree | Intro tokenized; **bullets preserved verbatim** | `{{MANAGED_SERVICE_INTRO}}`, `{{CLIENT_SHORT_NAME}}` ×2 (inside the Managed Services bullets) |

---

## 2. Full token inventory (27 tokens, 34 occurrences)

Ordered by first appearance.

| # | Token | Count | Section(s) | What to supply |
|---|---|---|---|---|
| 1 | `{{PROJECT_TITLE}}` | 1 | Front matter | Short scalar, e.g. `Finance Data Warehouse` |
| 2 | `{{CLIENT_LEGAL_NAME}}` | 2 | Front matter; §5.2 | Full legal entity — `PT …`, `PT (Persero)`, `Tbk` |
| 3 | `{{PROPOSAL_DATE}}` | 1 | Front matter | Human-readable, `7 April 2026` format |
| 4 | `{{INDUSTRY_CREDENTIAL}}` | 1 | §1 | One sentence naming the industry and its regulator |
| 5 | `{{PROBLEM_STATEMENT}}` | 1 | §2 | **Block.** 3–5 paragraphs; replaces 5 source paragraphs |
| 6 | `{{ARCHITECTURE_DIAGRAM}}` | 1 | §2.2 | Inline in `Full image: {{ARCHITECTURE_DIAGRAM}}` — a text anchor, not prose |
| 7 | `{{ARCHITECTURE_NARRATIVE}}` | 1 | §2.2 | **Block.** Replaces 24 bulleted paragraphs |
| 8 | `{{ARCHITECTURE_SUMMARY}}` | 1 | §2.2 | **Block.** Replaces the closing "This architecture provides an end-to-end foundation…" paragraph |
| 9 | `{{SOLUTION_COMPONENTS}}` | 1 | §2.3 | **Block.** Replaces §2.3.1–§2.3.5, 86 paragraphs |
| 10 | `{{SCOPE_OF_WORKS}}` | 1 | §2.4 | **Block.** After the preserved lead-in |
| 11 | `{{DELIVERABLES}}` | 1 | §3.2 | **Block.** Replaces the intro sentence *and* all deliverable bullets |
| 12 | `{{OUT_OF_SCOPE}}` | 1 | §4 | **Block.** After the preserved lead-in |
| 13 | `{{CLIENT_SHORT_NAME}}` | 3 | §5.2 (×1); §6.3 bullets (×2) | Name used in running prose |
| 14 | `{{SUPPORT_TIMEZONE}}` | 2 | §6.2 SLA footnotes `**` and `***` | Default `WIB (Western Indonesia Time)` |
| 15 | `{{SUPPORT_PORTAL_URL}}` | 1 | §6.2 Support Portal | Own paragraph; the old hyperlink was stripped |
| 16 | `{{SUPPORT_EMAIL}}` | 2 | §6.2 email paragraph; Escalation Flow R1C1 | mailto: links stripped |
| 17 | `{{SDM_NAME}}` | 2 | §6.2 Escalation Flow R2C1 (Level 2); Roles R2C1 | Same person in both by design |
| 18 | `{{SDM_EMAIL}}` | 2 | Same two cells | |
| 19 | `{{ESCALATION_L3_NAME_1}}` | 1 | §6.2 Escalation Flow R3C1, paragraph 1 | |
| 20 | `{{ESCALATION_L3_EMAIL_1}}` | 1 | Same | |
| 21 | `{{ESCALATION_L3_NAME_2}}` | 1 | §6.2 Escalation Flow R3C1, paragraph 2 | |
| 22 | `{{ESCALATION_L3_EMAIL_2}}` | 1 | Same | |
| 23 | `{{TECH_LEAD_NAME}}` | 1 | §6.2 Roles R1C1 | |
| 24 | `{{TECH_LEAD_EMAIL}}` | 1 | Same | |
| 25 | `{{TAM_NAME}}` | 1 | §6.2 Roles R3C1 | |
| 26 | `{{TAM_EMAIL}}` | 1 | Same | |
| 27 | `{{MANAGED_SERVICE_INTRO}}` | 1 | §6.3 | **Block.** The bullets below it are preserved boilerplate |

The eight **block** tokens each occupy exactly one `NORMAL_TEXT` paragraph at indent 0, with any inherited
bullet removed. They must expand into many paragraphs with headings and bullets — see `known-gotchas.md` #12.

### Not in the document

`{{CLIENT_TARGET_SYSTEMS}}` and `{{BI_TOOL}}` do **not** exist in the template. Their only source occurrences
(`Fincx`, `Tableau`) lived inside blocks that were collapsed wholesale into `{{SCOPE_OF_WORKS}}` and
`{{ARCHITECTURE_NARRATIVE}}`. Treat both as **generation input variables** that shape those two blocks' prose.
Adding them to the fill map produces spurious unmatched-token errors.

### Machine-readable fill map

18 fillable scalars + 8 narrative blocks + 1 anchor = the 27 tokens.
`anchor_token_do_not_fill` must **never** be added to a `replaceAllText` fill map — filling it destroys the
Phase D diagram insertion point. SKILL.md Phase D step 3b is what removes it from the finished document.

```json
{
  "scalar_tokens": {
    "{{PROJECT_TITLE}}": 1, "{{CLIENT_LEGAL_NAME}}": 2, "{{PROPOSAL_DATE}}": 1,
    "{{CLIENT_SHORT_NAME}}": 3, "{{SUPPORT_TIMEZONE}}": 2, "{{SUPPORT_PORTAL_URL}}": 1,
    "{{SUPPORT_EMAIL}}": 2, "{{SDM_NAME}}": 2, "{{SDM_EMAIL}}": 2,
    "{{ESCALATION_L3_NAME_1}}": 1, "{{ESCALATION_L3_EMAIL_1}}": 1,
    "{{ESCALATION_L3_NAME_2}}": 1, "{{ESCALATION_L3_EMAIL_2}}": 1,
    "{{TECH_LEAD_NAME}}": 1, "{{TECH_LEAD_EMAIL}}": 1,
    "{{TAM_NAME}}": 1, "{{TAM_EMAIL}}": 1,
    "{{INDUSTRY_CREDENTIAL}}": 1
  },
  "anchor_token_do_not_fill": {
    "{{ARCHITECTURE_DIAGRAM}}": 1
  },
  "narrative_block_tokens": {
    "{{PROBLEM_STATEMENT}}": 1, "{{ARCHITECTURE_NARRATIVE}}": 1, "{{ARCHITECTURE_SUMMARY}}": 1,
    "{{SOLUTION_COMPONENTS}}": 1, "{{SCOPE_OF_WORKS}}": 1, "{{DELIVERABLES}}": 1,
    "{{OUT_OF_SCOPE}}": 1, "{{MANAGED_SERVICE_INTRO}}": 1
  },
  "input_variables_only_not_in_doc": ["{{CLIENT_TARGET_SYSTEMS}}", "{{BI_TOOL}}"]
}
```

---

## 3. Tables — all eight are preserved intact

Do not restructure, re-row, or re-column any of these. Four of them are contractual language.

| Table | Size | Notes |
|---|---|---|
| §3.1 Timeline Gantt | 33 × 15 | Bahasa Indonesia phase labels (`Assessment Kondisi Eksisting`, `GAP Analisis`, `Desain Arsitektur`, `Konfigurasi Infrastruktur`, `Proses ETL`, `User Manual Pengguna`, `Transfer Knowledge`, …). Mostly empty cells by design — the shaded bars. Mixed Indonesian/English register is house style; do not "correct" it. Adjust phase rows to the deal, keeping the convention (`profile-extraction.md` §4). |
| §5.4 GCP SLA | 5 × 3 | Contractual. |
| §6.1 Priority / response times | 5 × 2 | Contractual. |
| §6.2 Incident Management RACI | 8 × 3 | |
| §6.2 Devoteam Support SLA | 5 × 4 | Contractual. Carries the `**` / `***` footnotes holding `{{SUPPORT_TIMEZONE}}`. |
| §6.2 Escalation Flow | 4 × 2 | Holds `{{SUPPORT_EMAIL}}`, `{{SDM_NAME}}`/`_EMAIL`, both `{{ESCALATION_L3_*}}` pairs. |
| §6.2 SLA-consumption matrix | 6 × 5 | Several empty `50%` cells — beware the empty-cell insert trap (`known-gotchas.md` #3). |
| §6.2 Roles | 4 × 2 | Holds `{{TECH_LEAD_*}}`, `{{SDM_*}}`, `{{TAM_*}}`. |

Docs API quirk when walking these: `table.rows` is the row **count** (an int); `table.tableRows` is the array.
Use Python, not jq (`known-gotchas.md` #5).

---

## 4. Paragraphs preserved verbatim

- **§1 Company Profile** — byte-identical to the accepted proposal except the single sentence replaced by
  `{{INDUSTRY_CREDENTIAL}}`.
- **§2.3** — the four generic Google Data Cloud intro paragraphs above `{{SOLUTION_COMPONENTS}}`.
- **§5.1 / §5.2 / §5.3** — agile methodology, change management, communication channels.
- **§6.1** — the Enhanced Support feature prose.
- **§6.2** — the opening statement, Devoteam Support prose, Hierarchical Escalation bullets, the Service Level
  Infrastructure Uptime disclaimer, and both `Notes :` disclaimers.
- **§6.3** — the Preventive / Corrective / Managed Services bullet tree below `{{MANAGED_SERVICE_INTRO}}`.

Full text of all of the above: `boilerplate.md` blocks 1–8.

---

## 5. Images

| Image | Location | What to do |
|---|---|---|
| **Legacy architecture diagram** (`kix.rqizdvorko9f`) | §2.2, directly above the `Full image: {{ARCHITECTURE_DIAGRAM}}` line | **Delete it.** It is a Finnet-era diagram and must never reach a customer. Image *content* cannot be replaced via the API (`known-gotchas.md` #9), but an inline image occupies one index in the body, so `deleteContentRange` over that index removes it. Then insert the new PNG at the anchor. |
| Devoteam / Google partner logos | §1 | Keep — generic Devoteam assets. |
| Agile methodology graphic | §5.1 | Keep — generic. |

The §2.3 product screenshots that lived inside §2.3.1–§2.3.5 were removed as collateral of the
`{{SOLUTION_COMPONENTS}}` collapse. If a deal wants product screenshots back, insert them manually.

The `Finnet-DWH Finance.png` Drive rich-link smart chip that sat in the `Full image:` paragraph was deleted —
smart chips are invisible to `replaceAllText` but their `title` and Drive `uri` are client-identifying
metadata. Watch for this whenever a future template revision pastes a Drive link into a paragraph.

---

## 6. Known template defects — flag these to the engineer every time

1. **§6.1 heading vs. body.** The heading says "Google Cloud Platform Enhanced Support" while the body
   describes "Premium Support". These are different Google Cloud Customer Care tiers with different response
   commitments and different prices. Confirm which tier the client is actually buying and make the section
   consistent. This is commercial exposure, not a typo.
2. **§1 corporate figures.** Talent counts, certification counts, and the Partner-of-the-Year count were
   carried over from an older proposal and are unverified — §1 also contradicts itself internally on the
   talent numbers. Refresh from current Devoteam collateral before sending.
3. **`{{SUPPORT_TIMEZONE}}` default.** The accepted source proposal wrote `WIT (Western Indonesia Time)`,
   which is wrong: WIB is Western/Jakarta, WIT is Eastern. Do not copy the sample's combination.
4. **`known-gotchas.md` #11's substring families.** `{{CLIENT_LEGAL_NAME}}` contains `{{CLIENT_…}}`,
   `{{ARCHITECTURE_NARRATIVE}}`/`{{ARCHITECTURE_SUMMARY}}` shadow `{{ARCHITECTURE_DIAGRAM}}`, and
   `{{SUPPORT_PORTAL_URL}}`/`{{SUPPORT_TIMEZONE}}` shadow `{{SUPPORT_EMAIL}}`. Sort fill requests by
   descending search-string length.

---

## 7. Rebuilding this template

If the master Doc is lost, corrupted, or superseded, it can be reconstructed:

1. `boilerplate.md` blocks 1–8 are the source of record for every preserved prose block and every preserved
   table in §1, §5.1–§5.4, §6.1, §6.2 and §6.3, with the exact token spellings already in place.
2. Rebuild §2, §2.2, §2.3, §2.4, §3.2 and §4 as single `NORMAL_TEXT` paragraphs containing only their block
   token, with headings above them as mapped in §1 of this file.
3. Rebuild §3.1 from the Bahasa Indonesia phase labels listed in `profile-extraction.md` §4.
4. §2.2 needs the `Full image: {{ARCHITECTURE_DIAGRAM}}` anchor line; a rebuilt template should carry **no**
   inline diagram image at all, which removes defect-by-omission risk entirely.
5. Verify the rebuild with `scripts/verify_residuals.py`: all 27 tokens present, zero `Finnet` / `Fincx` /
   `Tableau` / old PIC surnames. **Pass `--raw`.** By default the script flattens only paragraph textRuns and
   table cells, which cannot see a hyperlink URL, a smart chip's `title`/`uri`, or image alt-text — exactly
   the places §5 above warns that client-identifying metadata hides. `--raw` additionally greps the serialized
   Docs API JSON, so those are covered; both scans are reported separately.
