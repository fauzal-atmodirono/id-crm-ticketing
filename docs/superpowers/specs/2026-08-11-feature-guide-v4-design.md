# PROTON CRM Feature Guide v4 — design

**Date:** 2026-08-11
**Status:** approved, not yet implemented
**Supersedes nothing.** v3 stays exactly as shipped.

## Why there is a v4

Feature Guide v3 was cut back on 2026-08-09 to describe only software that was
actually running on the `proton` tenant. Three Administration sections were
lifted out of it into `docs/client-materials/feature-guide-v3-pending.md`
because the packages behind them had never been through a Cloud Build. That was
the right call then, and it dated the moment the build landed.

Measured on 2026-08-11, the tenant now runs:

| Probe | Value |
|---|---|
| `docker exec proton-chatwoot-rails cat /app/.git_sha` | `0866fda` — dev-yuda HEAD |
| image | `proton-chatwoot:v4.15.1-custom-rc6` |
| rendered `/app/login` feature list | `ai_assist,nav_menu,copilot,knowledge,inbound_alerts,faq_suggestion_popup` |
| backend `/openapi.json` path count | 113 (was 93 at the time v3 was written) |

So the fork patch set through `0065` and the whole backend are deployed. The
guide is now the thing that lags, not the tenant.

Separately, 103 `[[SCREENSHOT: ...]]` markers exist across the 14 chapters and
only 44 PNGs back them. The other 59 currently render in the `.docx` as a grey
bordered box containing the caption. The client asked for real screenshots where
they can be had and **nothing at all** where they can't — no boxes, no
"screenshot to follow", no placeholder of any kind.

## Scope

Full re-verification of all 14 chapters against the live tenant, plus the new
material the current deployment makes true, plus a screenshot capture sweep
against the live tenant. v3's source and output are not modified.

## Components

### 1. `feature-guide-src-v4/`

A copy of the 14 v3 chapter files plus `OUTLINE.md`, then edited in place. The
builder already takes `FG_SRC_DIR`, `FG_OUT`, `FG_COVER_TITLE` and
`FG_COVER_SUBTITLE` from the environment specifically so a further edition needs
no change to `build_crm_feature_guide.py`, so the build invocation is:

```
FG_SRC_DIR=feature-guide-src-v4 \
FG_OUT='PROTON - CRM Feature Guide v4.docx' \
FG_COVER_SUBTITLE='Operator Handbook — Edition 4' \
python3 build_crm_feature_guide.py --no-placeholders
```

`feature-guide-src-v3/` and `PROTON - CRM Feature Guide v3.docx` are untouched.
v3 has already gone to the client; it must stay reproducible.

### 2. Placeholder suppression — `--no-placeholders`

`add_screenshot()` has two branches. The `else` branch — the one that draws a
one-cell table, shades it, borders it at `sz=8`, writes `"Screenshot: <caption>"`
in italic and pads it with three empty paragraphs — is what the client is
objecting to.

The new flag makes that branch emit **nothing**: no table, no caption, and not
even the trailing spacer paragraph that the `found` branch adds. The section
reads as if the marker were never there.

The default is deliberately unchanged. Two consumers depend on it:
`scripts/test_build_feature_guide_audiences.py` rebuilds the default output and
compares it against the pre-audience-filter generator, and a v3 rebuild must
still produce the document that was delivered. The flag is opt-in and v4 is the
only caller.

The `missing` list that `add_screenshot()` already accumulates keeps working, so
the build still reports which markers found no PNG. That report feeds §4.

### 3. Verification ledger — `feature-guide-v4-verification.md`

Internal, not a client deliverable, alongside `feature-guide-v3-pending.md`.

One row per checkable claim: chapter, the claim, the probe that settled it, the
verdict (`verified` / `corrected` / `removed`), and the date. "Full re-verify" is
otherwise an assertion about work nobody can audit, and this repo has already
been burned once by a guide that described software an operator could not reach.

### 4. Chapter content pass

Every factual claim in the 14 chapters is checked against the live tenant using
the probes that are already proven on this box, and only those:

- **What the SPA actually ships** — the rendered feature list from inside the
  container (`wget`, not `curl`; `127.0.0.1`, not `localhost` — puma binds IPv4).
  Not the compose file, not the env file, not the patch directory.
- **What the backend actually serves** — `/openapi.json` listed **by prefix**.
  Never an exact-path probe: `/alerts/rules` reads as missing when the real
  paths are `/alerts/rules/{defaults,mine}` and the router was mounted all along.
- **Flag state** — `printenv` inside *both* `proton-agent` and
  `proton-chatwoot-rails`. The two do not read the same source: the backend
  takes the tenant env file wholesale via `env_file:`, while Rails gets only
  what the compose `x-chatwoot-env` block passes through, and the VM's compose
  file has been stale before.
- **Rails-side config** — read through the running container, not inferred.

The three sections held back into `feature-guide-v3-pending.md` are restored
**only where the probe supports them**. Expected outcome, to be confirmed rather
than assumed: Agent Availability & Workforce Dashboard and AI Conversational
Quality now have shipping code behind them; AI Cost & Performance Measurement
does not, because P8's eleven BigQuery views were never created and
`/metrics/targets` was absent at last check. Anything that fails its probe stays
in the pending file with its `<!-- VERIFY-LIVE -->` comment intact.

New material the current deployment makes true, each subject to the same gate:
the Translate composer action, the FAQ suggestion strip, inbound alerts, the
case-taxonomy admin page, the agent status selector, the Agent Channel
Priorities editor, and multimodal AI assist with media-grounded KB retrieval.

### 5. Screenshot sweep — read-only, live tenant

Chrome against `http://proton.crm.34-50-103-151.nip.io`, using the session
already present in the user's profile. Captures land in `feature-guide-assets/`
under the existing `chNN-<id>.png` names the markers already reference, so no
marker text changes when a shot arrives.

**Read-only is a hard constraint.** This is a production tenant with real
customer conversations. No message is sent, no setting is saved, no destructive
control is clicked, and nothing is done that could fire escalation mail — the
escalation path on this tenant demonstrably mails real Devoteam addresses. Where
a marker calls for a particular state (an SLA breach note, a department
suggestion note, escalation labels applied in order), the shot comes from an
**existing** conversation that already shows it, found by searching, not by
staging new activity.

The client accepted, on being told, that shots will contain real Proton
conversations and real PIC addresses.

Markers that still have no PNG at the end of the sweep have their marker line
**deleted from the v4 source** and are listed in the ledger with the reason. A
dangling marker in the source is how a placeholder comes back by accident in
some later edition.

## Sequencing

**Phase A — a complete document without the sweep.** Probe, write the ledger,
fork and edit the chapters, add `--no-placeholders`, build v4 against the 44
PNGs that already exist. The output is shippable on its own: correct, current,
and free of placeholder boxes.

**Phase B — the sweep.** Capture, drop the markers that came back empty, rebuild.

Phase A first because Phase B is the slow, failure-prone half. If capture stalls
on a page that will not render or a state that does not exist on the tenant,
there is already a v4 in hand rather than a half-edited source tree.

## Out of scope

- The `default` and `wahchan` tenants. Proton only.
- Rebuilding the local dev CRM image.
- Any change to `feature-guide-src-v3/` or the v3 `.docx`.
- Staging new data on the live tenant to manufacture a screenshot.
- The three training curricula under `training/`. The audience markers in the v3
  chapters are HTML comments that the builder strips, so copying them into v4 is
  inert; regenerating the curricula from v4 is a separate decision.

## Success criteria

1. `PROTON - CRM Feature Guide v4.docx` builds and contains **zero** placeholder
   boxes.
2. Every claim in the 14 chapters has a ledger row with a probe and a verdict.
3. No marker in `feature-guide-src-v4/` lacks a PNG.
4. A default (v3) rebuild is unchanged — identical zip-member digests, which is
   the comparison `scripts/test_build_feature_guide_audiences.py` already makes,
   since python-docx restamps member mtimes on every build — and that test still
   passes.
5. The live tenant is unchanged: no messages, no settings writes, no mail sent.
