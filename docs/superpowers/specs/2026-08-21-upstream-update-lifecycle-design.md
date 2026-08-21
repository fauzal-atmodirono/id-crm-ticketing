# Upstream update lifecycle — keeping the Chatwoot fork current

**Date:** 2026-08-21 · **Status:** design, approved in brainstorm · **Owner:** platform engineer on duty

---

## 1. The problem, measured

The CRM is a 70-patch fork of Chatwoot Community pinned at `v4.15.1`. Upstream
is at `v4.17.0`, and `v4.16.2` carries an explicit "upgrade to this version for
the latest security fixes" advisory. We are two minor versions and one security
advisory behind, and nobody can currently say what it would cost to catch up —
because the tooling that would answer that has never been run.

Everything in this section was measured against the patch series on 2026-08-21,
not estimated:

| Fact | Value |
|---|---|
| Patches in series | **70** (`0001`–`0071`; `0042` does not exist) |
| Distinct files touched | 82 |
| Files **created by our patches** | **63** — upstream cannot textually conflict with a file it has never heard of |
| Files **upstream owns** | **19** |
| Patches touching ≥1 upstream-owned file | **41** |
| Patches touching only our own files | **29** |

The 19 upstream-owned files are not evenly weighted. Three of them carry most
of the exposure:

```
22 patches →  app/javascript/dashboard/components-next/sidebar/Sidebar.vue
14 patches →  app/javascript/dashboard/routes/dashboard/dashboard.routes.js
 5 patches →  app/javascript/dashboard/components/widgets/WootWriter/ReplyTopPanel.vue
 …16 more upstream files, each touched once or twice
```

**This is the whole cost structure of the fork.** Every custom admin surface we
have built — escalation routing (`0039`), Customer 360 (`0041`), cases
(`0043`), workforce (`0053`), taxonomy (`0060`), softphone (`0069`) — needs to
do the same two things: add a nav entry and add a route. Each does it as
another hand-rolled hunk against the same two upstream files. An upstream
release that refactors `Sidebar.vue` does not break one patch. It breaks
twenty-two, in a series where `rebase.sh`'s own report can only call failures
after the first *"possibly cascading"* — so the first run will not even give an
honest conflict count.

Three supporting facts:

- **`PATCH-INVENTORY.md` is stale.** It states 58 patches; there are 70. The
  generated artifact has drifted 12 patches away from the thing it describes.
- **`rebase.sh` has never been run against a real upstream tree.** Its own
  header says so: *"NOT exercised: an actual rebase… the apply loop, the failure
  report and the cascade marking are therefore UNPROVEN."* It was written in an
  environment with no network access to github.com — the same restriction that
  applies to the agent sandbox today. This is not an oversight to correct by
  trying harder locally; it is a structural reason the measurement has to move
  to CI.
- **The Dockerfile adds a dependency outside the frozen lockfile.**
  `pnpm install --frozen-lockfile` is followed by `pnpm add
  @twilio/voice-sdk@2.18.3` for patch `0069`. The reasoning is sound and well
  commented, but it is a second, independent way a version bump can fail.

There is also a gap that matters specifically for selling this product:
`deploy/tenants/*.env` is gitignored, so the pinned `CHATWOOT_IMAGE` for
`proton` and `aeon360` exists **only on the VM**. There is no committed,
queryable answer to *"what version is each tenant running, and when was it last
patched?"* — which is precisely the question a customer's security review asks.

## 2. Goals and non-goals

**Goals**

1. Make an upstream version bump a routine, measurable operation rather than an
   unbounded unknown — so security releases stop being deferred by default.
2. Produce a committed, verifiable record of what version every tenant runs and
   when it was promoted.
3. Automate detection, rebase-checking, image building, and canary deployment,
   with a human gate only at promotion to customer tenants.
4. Know what Chatwoot ships in Enterprise, so "build our own equivalent" is a
   decision we make on time rather than one we discover late.

**Non-goals**

- Automatic promotion to customer tenants (`proton`, `aeon360`). That stays an
  explicit human action, always.
- Building a real non-production environment. That is a separate, larger project
  already scoped in `docs/runbooks/environments.md` §2. This design works within
  its absence and is explicit about what that costs.
- Rewriting the fork as an upstream contribution, a plugin system, or anything
  that requires upstream to accept our changes.
- Actually building Enterprise-equivalent features. Phase 4 produces decisions
  and a backlog, not code.

## 3. The update lifecycle

The core of this design is a state machine. Every upstream release entering our
system moves through it, and every state has one owner — a robot or a human,
never both.

```
                        ┌──────────────────────────────────────────┐
                        │  upstream: chatwoot/chatwoot releases     │
                        └────────────────────┬─────────────────────┘
                                             │ nightly poll (GitHub API)
                                             ▼
   ┌──────────┐   new tag ≠ UPSTREAM_VERSION                        ROBOT
   │ DETECTED │◄──────────────────────────────────────────────────┐
   └────┬─────┘                                                    │
        │ clone tag, run rebase.sh --src … --ref <tag>              │
        ▼                                                          │
   ┌──────────────────┐        exit 1        ┌──────────────────┐  │
   │  REBASE_CLEAN    │◄────────────────────►│ REBASE_CONFLICT  │  │
   │  (exit 0)        │        exit 0        │  N patches fail  │  │
   └────────┬─────────┘                      └────────┬─────────┘  │
            │                                         │            │
            │ Cloud Build (amd64)                     │ opens PR   │
            │ + bundle assertions                     │ with the   │
            ▼                                         │ full       │
   ┌──────────────────┐   vite build or               │ failure    │
   │     BUILT        │   assertion fails             │ report     │
   │  :<tag>-rc<n>    │──────────────────┐            ▼            │
   └────────┬─────────┘                  │      ┌───────────┐      │
            │                            └─────►│  HUMAN:   │──────┘
            │ deploy to `default` only          │  fix the  │  re-run
            ▼                                   │  series   │
   ┌──────────────────┐   health/smoke fails    └───────────┘
   │  CANARY_GREEN    │──────────────┐
   │  smoke passed    │              ▼
   └────────┬─────────┘        ┌──────────────┐
            │                  │ ROLLED_BACK  │──► PR stays open, red
            │                  │ (auto)       │
            │                  └──────────────┘
            │ opens PR: UPSTREAM_VERSION bump + inventory + ledger
            ▼
   ┌──────────────────────────────────────────┐
   │  HUMAN GATE — review PR, decide promotion │      HUMAN
   └────────────────────┬─────────────────────┘
                        │ merge + promote per tenant
                        ▼
   ┌──────────────────────────────────────────┐
   │  PROMOTED  → deploy/VERSIONS.yml updated  │
   │  drift-check verifies via GET /api        │
   └──────────────────────────────────────────┘
```

**The invariant:** a robot may change `default`. Only a human may change a
tenant a customer pays for. Everything the robot does produces a pull request,
so the audit trail is the same artifact as the review surface.

### 3.1 Why a PR is the unit of work

The alternative designs (a dashboard, a Slack alert, a report in GCS) all
separate *knowing* from *acting*. A pull request carrying the bumped
`UPSTREAM_VERSION`, the regenerated `PATCH-INVENTORY.md`, any re-exported patch
files, and the appended `VERSIONS.yml` entry is simultaneously the notification,
the evidence, the review, and the change. It is also the thing you hand a
customer's auditor.

## 4. Phase 1 — the rebase-check job (credential-free)

**Ships first because it needs nothing.** Cloning a public repo and running
`git apply` requires no GCP credentials, no Workload Identity Federation, no VM
access. It is a single GitHub Actions workflow in a repo that currently has
none.

`rebase.sh` already has exactly the right contract for CI and does not need to
change:

- `--src <checkout>` — it deliberately does **not** clone; the workflow clones
  and points it at the checkout.
- `--ref <tag>` — checks out the target tag in a scratch copy.
- **exit 0** = every patch applied. **exit 1** = a report naming every failure,
  the first as independent and the rest as *possibly cascading*, with 20 lines
  of `git apply` output each and a path to the full log.
- On failure it keeps the scratch tree, with each applied patch committed
  separately — which is what lets a human regenerate a fixed patch with a real
  `git diff` instead of editing `@@` arithmetic by hand.

**Workflow:** `.github/workflows/upstream-watch.yml`

1. Nightly cron plus `workflow_dispatch` for on-demand runs.
2. Query the GitHub API for `chatwoot/chatwoot` releases; compare the newest
   tag against `deploy/chatwoot-fork/UPSTREAM_VERSION`.
3. If equal, exit quietly. A watcher that reports "nothing happened" every day
   trains people to ignore it.
4. If newer: shallow-clone the tag, run `rebase.sh --src … --ref <tag> --keep`.
5. Regenerate `PATCH-INVENTORY.md` via `rebase.sh --inventory` — which
   incidentally fixes today's 58-vs-70 drift on the first run.
6. Open (or update) a PR titled `chore(chatwoot): upstream <tag>` carrying the
   full rebase report in the body, labelled `upstream-clean` or
   `upstream-conflict`.

**What Phase 1 alone buys:** the first honest answer to "what does catching up
cost?" Run it once against `v4.17.0` and the unknown becomes a number. If that
number is small, the seam refactor in Phase 2 may be smaller than assumed — and
that is the point of sequencing this first.

**Caveat the workflow must print, not hide:** a clean rebase is *not* a working
build. `rebase.sh` says this itself. A patch can apply cleanly against a file
upstream has since refactored and still break the Vite build or the runtime
behaviour. Phase 1 measures textual conflict only.

## 5. Phase 2 — the registration seam

Scoped by Phase 1's real conflict report, not by this document's assumptions.

**The change:** one patch takes ownership of `Sidebar.vue` and
`dashboard.routes.js` and makes both read from a fork-owned manifest — a file
this series creates, and therefore a file upstream can never conflict with.
Every feature patch then appends an entry to the manifest instead of editing an
upstream file.

```
BEFORE                                  AFTER
──────                                  ─────
0039 ──┐                                0039 ──┐
0041 ──┤                                0041 ──┤
0043 ──┼──► Sidebar.vue        (22×)    0043 ──┼──► proton/registry.js   (fork-owned)
0053 ──┤    dashboard.routes.js(14×)    0053 ──┤          │
0060 ──┤    ← upstream owns these       0060 ──┤          ▼
0069 ──┘                                0069 ──┘   0003 ──► Sidebar.vue        (1×)
                                                   0003 ──► dashboard.routes.js(1×)
```

**Projected effect** (computed against today's series, not estimated):

| | Now | After seam |
|---|---|---|
| Patches touching ≥1 upstream file | 41 | **22** |
| Patches touching only fork-owned files | 29 | **48** |
| Patches touching `Sidebar.vue` | 22 | **1** |
| Patches touching `dashboard.routes.js` | 14 | **1** |
| Upstream-owned files | 19 | 19 *(unchanged)* |

Nineteen patches become entirely fork-owned — `0009`, `0012`, `0016`, `0021`,
`0025`, `0026`, `0027`, `0029`, `0032`, `0035`, `0039`, `0041`, `0043`, `0045`,
`0053`, `0054`, `0057`, `0060`, `0069` — meaning upstream can no longer conflict
with them at all. Note the file count does **not** drop: `Sidebar.vue` and
`dashboard.routes.js` are still files upstream owns. What changes is that a
refactor of either becomes **one** conflict to resolve instead of twenty-two.

**Cost:** mechanically rewriting the **23 patches** that touch a seam file.
Conceptually straightforward, tedious in practice, and it must be done as one
atomic change with the bundle assertions from §6.2 as the safety net — a
half-migrated series is worse than either end state.

**Explicitly deferred:** the long tail of 16 upstream files touched once or
twice. Each is a single conflict on a bad day. Collapsing them would cost more
than it saves. YAGNI applies.

## 6. Phase 3 — build, canary, smoke, promote

### 6.1 Build

On `REBASE_CLEAN`, the workflow triggers the existing
`deploy/chatwoot-fork/cloudbuild.yaml` via Workload Identity Federation. No new
build infrastructure — that pipeline already builds amd64 off-VM, which
`CLAUDE.md` requires and a local arm64 build cannot satisfy.

It must use `_TAG_SUFFIX`. That substitution exists because on 2026-08-11 both
`default.env` and `proton.env` pinned exactly `v4.15.1-custom`, so rebuilding
that tag would have handed the Proton tenant a new image on its next recreate
with no one running a command against Proton. **An automated build that writes
to an unsuffixed tag would recreate that hazard on a schedule.** The pipeline
builds `:<tag>-custom-rc<run-number>` and nothing else.

### 6.2 Bundle assertions — the cheap check that catches the real failure

The failure mode that matters is not "patch did not apply". It is "patch applied,
Vite built, and the feature silently vanished" — because upstream renamed the
thing the patch hooked into. Runtime browser testing would catch it, at high
cost and high flakiness.

Instead, assert at build time. After `pnpm exec vite build`, grep the emitted
`public/vite` bundle for a set of marker strings — one per major custom surface
(knowledge, reports, escalation routing, Customer 360, taxonomy, workforce,
softphone). Any marker missing fails the build.

This is a few seconds of work in a step that already takes 10–15 minutes, it
runs in the place with the most context about what just happened, and it turns
a silent regression into a build failure. It also protects the Phase 2
migration: if the manifest refactor drops a nav entry, the build says so.

The one file that lives outside `public/vite` — patch `0001`'s
`app/views/layouts/vueapp.html.erb`, which injects `window.__PROTON_CONFIG__` —
gets its own assertion, because without it every feature gate stays off and the
UI looks fine while doing nothing.

### 6.3 Canary — and an honest statement of its limits

The image deploys to the `default` tenant only, which is out of customer scope.

**What it proves:** the image boots; Rails serves; migrations apply against a
real Chatwoot schema; Sidekiq starts and drains; the patched assets are the ones
being served.

**What it does not prove, and the design must not pretend otherwise:**
`docs/runbooks/environments.md` states that non-production *"Does not exist.
Never provisioned"*, and argues that a non-prod tenant on the production VM is
the obvious move and the wrong one — because it shares that VM's Postgres
server, Docker daemon, kernel and disk. So the `default` canary cannot validate
anything involving a shared resource: a Postgres major-version upgrade, a kernel
dependency, a disk-pressure interaction, or contention between tenants. It is an
**image-validity canary, not an environment canary.**

It also is not free. Pulling a fresh Chatwoot image onto a 16 GB VM consumes
disk and I/O on the machine serving customers. Therefore:

- a disk-headroom precondition, checked before pull, aborting the run rather
  than filling the disk;
- the canary window is scheduled off-peak;
- `docker image prune` of superseded `-rc` tags after a successful run;
- and the run touches `chatwoot-rails` and `chatwoot-sidekiq` for `default`
  only — never `docker compose up` without an explicit service list.

### 6.4 Smoke — asserting the right image is actually live

`chatwoot-rails` already healthchecks `GET /api`. The Dockerfile deliberately
overwrites the upstream `.git_sha` with `PROTON_BUILD_SHA` so that endpoint
reports *our* build rather than upstream's frozen release commit. That gives us
an identity check for free.

Smoke sequence, in order, each with a timeout:

1. Container healthy per the existing compose healthcheck.
2. `GET /api` returns 200, `version` equals the new upstream tag, **and the
   reported build sha equals the sha we just built**. This is the step that
   catches a pull that silently served a cached older layer.
3. `GET /auth/sign_in` returns 200 and contains the `__PROTON_CONFIG__` marker
   — the runtime counterpart to the `vueapp.html.erb` build assertion.
4. Sidekiq container up, queue latency below threshold.

Any failure triggers automatic rollback: repoint `default` at the previously
recorded good tag from `VERSIONS.yml`, recreate, re-assert step 2, and leave the
PR open and red. **The rollback path is only credible if it is exercised** — so
it is tested deliberately, by pointing the canary at a knowingly broken image,
before the pipeline is trusted. A rollback that has never run is a comment, not
a control.

### 6.5 Promotion — the human gate

The PR is the decision point. A human reviews the rebase report, the build
result, and the canary evidence, then merges and promotes per tenant using the
existing documented procedure. Promotion updates `deploy/VERSIONS.yml`.

Promotion is deliberately per-tenant and never batched. `proton` and `aeon360`
have different Twilio wiring, different Firestore resources and different flag
states; "promote everywhere" is how one tenant's edge case becomes everyone's
outage.

## 7. The version ledger

`deploy/VERSIONS.yml` — committed, at `deploy/` root rather than
`deploy/tenants/` because `deploy/tenants/*.env` is gitignored and the ledger
must never be.

```yaml
upstream_pinned: v4.15.1
tenants:
  default:
    image_tag: v4.15.1-custom
    build_sha: c9c4828
    upstream: v4.15.1
    promoted: 2026-08-18
    promoted_by: <handle>
  proton:
    image_tag: v4.15.1-custom-rc9
    build_sha: c9c4828
    upstream: v4.15.1
    promoted: 2026-08-18
    promoted_by: <handle>
```

**A ledger nobody verifies becomes fiction within two deploys.** So a nightly
drift-check reads each tenant's public `GET /api` and compares the reported
version and build sha against the ledger, opening an issue on mismatch. This
needs no VM access and no GCP credentials — only HTTP to the public URL — so it
can run in the same credential-free workflow as Phase 1.

*To verify during implementation:* that Chatwoot's `GET /api` is reachable
unauthenticated on our Caddy routing. If it is not, the drift-check falls back
to an authenticated call and moves into the credentialed workflow. This is the
one assumption in the design that has not been checked against a live tenant.

## 8. Phase 4 — Enterprise watch (deliberately light)

The nightly watcher already reads the releases feed. Extend it to also diff, tag
to tag, the paths where Chatwoot gates paid functionality — the `enterprise/`
tree and the feature-flag configuration — and append a dated entry to
`docs/roadmap/upstream-enterprise-watch.md`:

> **v4.17.0** — EE added `<feature>`. Our equivalent: none / `patch 00NN` /
> not wanted. Decision: <date>, <who>.

That is the entire scope. It produces **decisions and a dated backlog, not
code.** Building any equivalent is a separate project, brainstormed on its own
merits when we choose to spend the effort. The value here is that "Chatwoot
shipped that eight months ago and we never noticed" stops being possible.

## 9. Sequencing and what each phase is worth on its own

| Phase | Delivers | Needs credentials | Value standing alone |
|---|---|---|---|
| 1 — rebase-check | True conflict count vs any tag; inventory drift fixed | **None** | High: converts the central unknown into a number |
| 4 — Enterprise watch | Dated EE-vs-us backlog | **None** | Moderate; rides along with Phase 1 |
| 2 — seam manifest | 41 → 22 exposed patches | None (local work) | High, but only justified by Phase 1's findings |
| 3 — build/canary/promote | Automated path to a verified image | WIF + VM access | High, and the largest build |

Phases 1 and 4 ship together because they share a workflow and neither needs a
secret. Phase 2 is scoped only after Phase 1 has reported against `v4.17.0`.
Phase 3 is last because it is the only phase that can affect the production VM,
and it should be built against a series that has already been made cheap to
rebase.

## 10. Risks

**The seam refactor breaks something subtle.** Thirty patches rewritten
mechanically is thirty chances to drop a prop or a permission guard. Mitigated
by the §6.2 bundle assertions landing *before* the refactor, so the migration
has a net beneath it, and by doing it as one reviewable atomic change.

**The canary gives false confidence.** It is an image-validity check on a
production-adjacent tenant, and §6.3 says so in the design rather than in
someone's memory. It must be described that way in the PR template too, or the
first person to read a green check will read it as more than it is.

**Automation touching the production VM.** Bounded by: explicit service lists,
disk preconditions, `default` only, tested rollback, off-peak windows.

**Upstream restructures faster than the seam absorbs.** If Chatwoot reorganises
the dashboard wholesale, no manifest saves us. The honest answer is that a patch
series against a fast-moving upstream is a standing commitment — `rebase.sh`'s
own inventory output says exactly this — and this design lowers its recurring
cost without eliminating it.

**The `@twilio/voice-sdk` install outside the frozen lockfile.** An upstream
bump can change the pnpm version or dependency graph such that the unpinned
`pnpm add` resolves differently or fails. It gets its own assertion in the build
and its own line in the rebase report.

## 11. What has and has not been exercised

Following the convention of `rebase.sh` and `docs/runbooks/environments.md`:
this document is a design. Nothing in it is running.

**Measured against the real repository on 2026-08-21:** the patch counts, the
file ownership split, the per-file touch counts, the `PATCH-INVENTORY.md` drift,
`rebase.sh`'s CLI and exit-code contract, the compose healthcheck, the gitignore
status of tenant envs, and the absence of any `.github/workflows`.

**Read from upstream on 2026-08-21:** that `v4.17.0` is the current release and
`v4.16.2` carries a security advisory.

**Not exercised:** every phase. No workflow has been written, `rebase.sh` has
still never been run against a real upstream tree, no `-rc` image has been
built, and the rollback path does not exist. The Phase 2 table is arithmetic
over today's patch series — a projection of what the refactor would achieve if
performed exactly as described, not a measurement of anything that has run.

## 12. Decisions taken in the brainstorm

1. **Driver:** productization — a defensible per-tenant version story — plus
   "as updated as possible", with automation.
2. **Automation boundary:** auto through canary deploy to `default`; promotion
   to `proton`/`aeon360` is always human.
3. **Sequencing:** prove first, then refactor. Ship the rebase-check job before
   spending the 30-patch rewrite.
4. **Enterprise tracking:** folded in as a light phase producing decisions, not
   a first-class parallel workstream.
5. **Orchestration:** GitHub Actions as the brain, Cloud Build as the muscle,
   staged so Phase 1 needs no credentials at all. Rejected: an all-GCP
   Cloud Scheduler design (puts the review artifact in the least reviewable
   place) and a self-hosted runner on the production VM (takes the attack
   surface without escaping the Cloud Build dependency).
