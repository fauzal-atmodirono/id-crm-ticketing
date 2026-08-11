# Milestone sign-off artefacts

**Closes:** §6.3.2 (milestone sign-off artefacts)

Ten named documents, in the form §6.3.2 asks for, each mapped to the evidence in
this repository that it attests to.

## Why each one carries a traceability line

**A sign-off document that cannot be traced to what it attests to is a signature
on nothing.** Roughly half of these artefacts already existed in substance under
a different name — design specifications are technical design documents, deploy
runbooks are a deployment checklist, the feature guide is a training manual. The
deliverable here is not new prose; it is producing them in the named form and
**naming the path to the evidence**, so a reviewer can check rather than trust.

Each artefact therefore states three things:

1. **What it is** and which requirement it satisfies.
2. **Traceability** — the exact repository paths that constitute its evidence.
3. **Status, honestly** — including where the evidence is thinner than the
   artefact's name implies. Four of the ten are **not ready to sign**, and each
   says so at the top rather than in a footnote.

## The ten

| # | Artefact | Source | Ready to sign? |
|---|---|---|---|
| 01 | [Technical design document](01-technical-design-document.md) | The design specs, consolidated | Yes |
| 02 | [Test plan](02-test-plan.md) | `../qa-plan.md` | Yes |
| 03 | [Test report](03-test-report.md) | Suite output + the SIT report | **No** — the SIT has not run |
| 04 | [Deployment checklist](04-deployment-checklist.md) | `README.md` + the deploy runbooks | Yes, with the fork-build caveat |
| 05 | [Training manual](05-training-manual.md) | The v3 feature guide, filtered by audience into `../../training/` (generated) | Yes as a manual; **no exercise has been dry-run** |
| 06 | [Configuration document](06-configuration-document.md) | `../../handover/configuration.md` (generated) | Yes |
| 07 | [Architecture document](07-architecture-document.md) | `../../handover/architecture.md` | **No** — the required outside review has not happened |
| 08 | [User acceptance sign-off](08-user-acceptance-sign-off.md) | Template + UAT script | **No** — UAT has not been run |
| 09 | [Handover checklist](09-handover-checklist.md) | New | Yes, as a checklist; items within it are open |
| 10 | [Support handover](10-support-handover.md) | P13 runbooks + the support model | Partly — the support model is undefined |

**Seven of ten are ready; 03, 07 and 08 are not, and they are not a paperwork
problem.** The SIT has not been executed, the architecture map has not been
reviewed by an outsider, and no UAT has been run. Presenting any of those three
as signable would be the failure this programme has already had to correct four
times in client-facing text.

**05 moved from "does not exist" to "signable as a manual"** when the three role
curricula landed as renderings of the one handbook source. Its remaining gap is
delivery, not documentation: no sandbox tenant has been provisioned, so no
exercise has been dry-run, and nine topics are untaught because the fork patches
behind them have never been built.
