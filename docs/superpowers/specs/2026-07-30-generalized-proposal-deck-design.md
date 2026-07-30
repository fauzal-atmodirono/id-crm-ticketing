# Config-driven proposal deck

**Date:** 2026-07-30
**Status:** Approved — ready for implementation plan

## Problem

`docs/build_proton_proposal_v2.py` (931 lines) generates the Technical Proposal
deck (30 slides, `.pptx`) for **PROTON only**. Customer identity is hardcoded
inline throughout the slide content: `PROTON` (~25×), `e.MAS` (the operation
name, 4×), `Zendesk` (the incumbent CRM being migrated from, ~13×), plus the
cover/logo asset paths and output filename. To produce a deck for another
customer today you would hand-edit the script.

Goal: generate the deck for **any customer** by editing a small config, with no
code edits for the identity/branding/commercials tokens. The Proton output must
stay byte-equivalent (default customer = `proton`).

## Approach: config + templated content

Chosen over a fully data-driven engine (too much upfront work — the deck's
narrative prose is genuinely bespoke per customer) and over a scaffold/copy
generator (leaves you hand-editing a fresh 900-line script each time).

Only **identity, branding, and offering** tokens move into config. The bespoke
narrative prose (the actual sentences on each slide — 6 BRD goals, 5-channel
SOP, gap-closure priorities, migration story) stays inline in the renderer and
is edited by hand when a customer's story genuinely differs. Name, incumbent
CRM, cloud, region, logos, cover image, and output path all flow from config.

## Files

Split `build_proton_proposal_v2.py` into two files under `docs/`:

### `proposal_config.py`
Holds only the variable fields. One entry per customer.

```python
from dataclasses import dataclass, field

@dataclass
class Customer:
    key: str                     # cli selector, e.g. "proton"
    name: str                    # "PROTON"          -> replaces PROTON
    operation: str               # "e.MAS Customer Operations" (prepared-for line)
    incumbent_crm: str           # "Zendesk"         -> what they migrate from
    cloud: str = "GCP"           # target cloud (fixed offering; overridable)
    region: str = "asia-southeast1"
    cover: str = "assets/cover.jpg"
    logo_dark: str = "assets/logo_dark.png"
    logo_white: str = "assets/logo_white.png"
    out: str = None              # output .pptx filename; default derived from name
    palette: dict = field(default_factory=dict)   # optional color overrides

CUSTOMERS = {
    "proton": Customer(
        key="proton",
        name="PROTON",
        operation="e.MAS Customer Operations",
        incumbent_crm="Zendesk",
        out="PROTON - Technical Proposal - Self-Managed CRM on Google Cloud (v2).pptx",
    ),
}
```

Asset paths and `out` are resolved relative to `BASE` (`docs/`) by the renderer.
`palette` empty → the baked-in Devoteam 2025 palette is used unchanged.

### `build_proposal.py`
The renderer: all existing helpers (`slide`, `rect`, `textbox`, gradient/diagram
builders), the Devoteam brand palette, and all 30 slide definitions — layout
**unchanged**. Differences from the current script:

- Reads the active customer: `python build_proposal.py [key]`, default `proton`.
  Unknown key → error listing valid keys.
- `C = CUSTOMERS[key]`. Every hardcoded identity token becomes an f-string
  interpolation: `PROTON` → `{C.name}`, `Zendesk` → `{C.incumbent_crm}`,
  `e.MAS Customer Operations` → `{C.operation}`, `GCP`/`asia-southeast1` →
  `{C.cloud}`/`{C.region}`.
- Constant vendor/tech tokens stay literal: `Devoteam`, `Power BI`, `BigQuery`,
  `Vertex`, `Montserrat`, the Devoteam palette.
- Asset/output paths come from `C` (resolved against `BASE`).

### Delete
`docs/build_proton_proposal.py` (v1, 583 lines, superseded by v2).

### Untouched
`docs/build_proton_commercials_xlsx.py` (already CSV-driven) stays as-is.

## Data flow

```
python build_proposal.py proton
  -> CUSTOMERS["proton"]  (Customer dataclass)
  -> build_proposal.py renders 30 slides, interpolating C.* tokens
  -> writes docs/PROTON - Technical Proposal ... (v2).pptx
```

## Error handling

- Unknown customer key → exit with a clear message listing valid keys.
- Missing asset file (cover/logo) → let it fail loud (same as today); paths are
  config, easy to fix.

## Testing / verification

- Regenerate the Proton deck from `build_proposal.py` with no key (default
  `proton`). Confirm: script runs clean, output `.pptx` opens, **30 slides**,
  Proton/Zendesk/e.MAS text intact — output equivalent to the current v2 script.
- Add a throwaway second customer entry, generate it, confirm the name/incumbent
  tokens swapped throughout and the file opens. (Then remove the throwaway
  entry, or keep as a documented example.)

## Out of scope

- Fully data-driven slide content (per-customer YAML for every sentence).
- Generalizing the commercials xlsx.
- Any change to slide layout, palette, or the 30-slide structure.
