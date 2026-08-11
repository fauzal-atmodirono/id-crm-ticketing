#!/usr/bin/env bash
# Apply the whole Proton patch series against an upstream Chatwoot ref and
# report EVERY patch that fails, not just the first one.
#
# Usage:
#   deploy/chatwoot-fork/rebase.sh --src <chatwoot-checkout> [--ref <upstream-ref>]
#   deploy/chatwoot-fork/rebase.sh --inventory [--out PATCH-INVENTORY.md]
#
# ---------------------------------------------------------------------------
# WHY IT KEEPS GOING AFTER THE FIRST FAILURE
# ---------------------------------------------------------------------------
# Rebasing this fork onto a new upstream is a recurring cost: every upstream
# Chatwoot security release needs the series re-applied and an amd64 Cloud
# Build. Knowing that 3 of the series conflict is a half-day of work you can
# plan; discovering them one at a time, each behind a rebuild, is a week. So a
# failing patch is recorded and SKIPPED, and the rest are still attempted.
#
# The honest caveat, which this script prints rather than hides: **once a patch
# is skipped, a later failure may be a consequence of that skip rather than an
# independent conflict.** Patches in this series stack (0054 applies on top of
# 0053's added lines; 0056 on 0002 plus 0055). So the report marks every failure
# after the first as "possibly cascading" and tells you to re-run once the
# earlier ones are fixed. The count of failures is a ceiling, not a total.
#
# ---------------------------------------------------------------------------
# WHAT IT DOES NOT DO
# ---------------------------------------------------------------------------
# It does NOT fetch or clone upstream Chatwoot. You point it at a checkout you
# already have (--src), it copies it to a scratch directory and works there, so
# your checkout is never modified. It does not build an image, and a clean run
# here does NOT mean the image builds: the Vite build in
# deploy/chatwoot-fork/Dockerfile can still fail on a patch that applied
# cleanly but referenced something upstream renamed.
#
# The Dockerfile applies the same patches with `git apply --whitespace=fix` in
# shell-glob order over patches/*.patch. This script sorts the same way, so
# "applies here" and "applies in the build" mean the same thing.
#
# ---------------------------------------------------------------------------
# WHAT HAS AND HAS NOT BEEN EXERCISED
# ---------------------------------------------------------------------------
# EXERCISED: `--inventory` was run and produced the committed
# PATCH-INVENTORY.md; it needs no upstream checkout because it only reads the
# patch files. Argument parsing, the help text, and the missing/invalid --src
# rejections were run. `bash -n` passes.
#
# **NOT exercised: an actual rebase.** This sandbox cannot reach github.com, so
# no Chatwoot checkout exists here and the series has never been applied by this
# script to a real upstream tree. The apply loop, the failure report and the
# cascade marking are therefore UNPROVEN against a real conflict. Recorded as
# owed in docs/analysis/2026-08-09-blocked-work-register.md.
set -euo pipefail

FORK_DIR="${FORK_DIR:-$(cd "$(dirname "$0")" && pwd)}"
PATCH_DIR="${FORK_DIR}/patches"
PINNED_REF="$(cat "${FORK_DIR}/UPSTREAM_VERSION" 2>/dev/null || echo "unknown")"

SRC=""
REF=""
MODE="rebase"
OUT="${FORK_DIR}/PATCH-INVENTORY.md"
WORKDIR="${REBASE_WORKDIR:-}"
KEEP=0

die() { echo "ERROR: $*" >&2; exit 1; }

usage() {
  cat <<EOF
Apply the Proton patch series and report every failure.

  --src <dir>     A Chatwoot checkout to apply onto. Copied to a scratch dir;
                  your checkout is never modified. Required for a rebase.
  --ref <ref>     Upstream ref to check out in the scratch copy before applying.
                  Default: leave the copy on whatever it is already on.
                  The pinned ref in UPSTREAM_VERSION is ${PINNED_REF}.
  --keep          Keep the scratch directory (default: delete it on success).
  --inventory     Do not rebase. Derive PATCH-INVENTORY.md from the patch files
                  themselves and write it out. Needs no checkout.
  --out <file>    Where --inventory writes (default PATCH-INVENTORY.md).
  -h, --help      This text.

Typical use, when upstream cuts a release:
  git -C ~/src/chatwoot fetch --tags
  ./rebase.sh --src ~/src/chatwoot --ref v4.16.0
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --src) SRC="${2:-}"; shift 2 ;;
    --ref) REF="${2:-}"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --inventory) MODE="inventory"; shift ;;
    --out) OUT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument '$1'" ;;
  esac
done

[[ -d "${PATCH_DIR}" ]] || die "${PATCH_DIR} not found"
shopt -s nullglob
PATCHES=("${PATCH_DIR}"/*.patch)
[[ ${#PATCHES[@]} -gt 0 ]] || die "no patches in ${PATCH_DIR}"

# ---------------------------------------------------------------------------
# Inventory. Everything here is READ OUT OF THE PATCH FILES, never guessed:
# the summary is the patch's own Subject line (or, for the older patches that
# carry no mail header, its filename); the file list and the added/removed
# counts come from parsing the diff.
#
# Conflict risk is derived, not editorial. A patch that only touches files
# created by an earlier patch in this same series is fork-owned: upstream cannot
# conflict with a file it has never heard of. A patch that modifies a file
# upstream owns can conflict on any upstream release.
# ---------------------------------------------------------------------------
patch_summary() {
  local p="$1" subject
  # -E, not a BRE with \?: BSD sed does not understand \? and silently matched
  # nothing, so every patch that DOES carry a Subject line was being reported
  # under its filename instead. A generated inventory that quietly downgrades
  # its own best source of truth is worse than no inventory.
  subject="$(sed -nE 's/^Subject: (\[PATCH\] )?//p' "${p}" | head -n1)"
  if [[ -n "${subject}" ]]; then
    printf '%s' "${subject}"
  else
    # No mail header: fall back to the filename's own words. Stated as derived
    # so nobody reads it as a description someone actually wrote.
    local base="${p##*/}"
    base="${base%.patch}"
    base="${base#[0-9][0-9][0-9][0-9]-}"
    printf '%s (from filename)' "${base//-/ }"
  fi
}

patch_files() {
  # /dev/null is the +++ side of a deletion; it is not a file the patch touches.
  sed -n 's|^+++ b/||p' "$1" | grep -v '^/dev/null$' | sort -u
}

patch_new_files() {
  # `new file mode` immediately precedes the ---/+++ pair for a created file.
  awk '/^diff --git /{f=$4; sub(/^b\//,"",f)} /^new file mode/{print f}' "$1" | sort -u
}

do_inventory() {
  local total=${#PATCHES[@]}
  # First pass: every file any patch CREATES is fork-owned from then on.
  local fork_owned_list="" p f
  for p in "${PATCHES[@]}"; do
    while read -r f; do
      [[ -n "${f}" ]] && fork_owned_list="${fork_owned_list}${f}"$'\n'
    done < <(patch_new_files "${p}")
  done

  {
    cat <<EOF
# Patch inventory — deploy/chatwoot-fork/patches/

**${total} patches**, applied in shell-glob (numeric) order by
\`deploy/chatwoot-fork/Dockerfile\` at image build time. Pinned upstream:
**${PINNED_REF}** (\`UPSTREAM_VERSION\`).

> **This file is generated.** Run \`./rebase.sh --inventory\` to regenerate it
> after adding a patch. Every column below is read out of the patch files
> themselves — nothing here is hand-written prose that could drift away from
> what the patches do.
>
> * **Summary** is the patch's own \`Subject:\` line. The older patches carry no
>   mail header; theirs is derived from the filename and marked as such.
> * **+/-** are added and removed line counts from the diff.
> * **Files** are the paths the diff touches; **new** means the patch creates
>   the file.
> * **Upstream conflict risk** is derived, not editorial:
>   **low** = every file it touches was created by a patch in this series, so
>   upstream cannot conflict with a file it has never heard of;
>   **upstream-owned** = it modifies at least one file upstream owns, so any
>   upstream release can conflict with it. A high line count on an
>   upstream-owned file is the expensive combination.
>
> Risk here is about *textual conflict on rebase only*. It says nothing about
> whether a patch still works after an upstream refactor — a patch can apply
> cleanly and break the Vite build or the runtime behaviour. See
> \`docs/runbooks/environments.md\` for what to verify after a rebase.

| # | Summary | Files | +/- | Upstream conflict risk |
|---|---|---|---|---|
EOF
    local upstream_touching=0 fork_only=0
    for p in "${PATCHES[@]}"; do
      local base num summary files_list nfiles added removed risk touched
      base="${p##*/}"
      num="${base%%-*}"
      summary="$(patch_summary "${p}")"
      files_list="$(patch_files "${p}")"
      nfiles="$(printf '%s\n' "${files_list}" | grep -c . || true)"
      added="$(grep -c '^+[^+]' "${p}" || true)"
      removed="$(grep -c '^-[^-]' "${p}" || true)"

      # Does it modify anything not created somewhere in this series?
      risk="low"
      while read -r touched; do
        [[ -z "${touched}" ]] && continue
        if ! printf '%s' "${fork_owned_list}" | grep -qxF "${touched}"; then
          risk="upstream-owned"
          break
        fi
      done < <(printf '%s\n' "${files_list}")
      if [[ "${risk}" == "low" ]]; then
        fork_only=$(( fork_only + 1 ))
      else
        upstream_touching=$(( upstream_touching + 1 ))
      fi

      # Render the file list compactly, flagging created files.
      local rendered="" created
      created="$(patch_new_files "${p}")"
      while read -r touched; do
        [[ -z "${touched}" ]] && continue
        if printf '%s' "${created}" | grep -qxF "${touched}"; then
          rendered="${rendered}\`${touched}\` (new)<br>"
        else
          rendered="${rendered}\`${touched}\`<br>"
        fi
      done < <(printf '%s\n' "${files_list}")
      rendered="${rendered%<br>}"

      printf '| %s | %s | %s file(s)<br>%s | +%s / -%s | %s |\n' \
        "${num}" "${summary//|/\\|}" "${nfiles}" "${rendered}" "${added}" "${removed}" "${risk}"
    done

    cat <<EOF

## What the totals mean for pricing the fork

- **${total} patches** in the series.
- **${upstream_touching}** modify at least one upstream-owned file, so each one
  is exposed to every upstream release.
- **${fork_only}** touch only files this series created, so upstream cannot
  conflict with them textually.

Tooling reduces the cost of this liability; it does not remove it. A patch
series against a fast-moving upstream is a standing commitment and should be
priced as one — see the note in
\`docs/superpowers/plans/2026-08-08-rfp-p13-ops-hardening.md\` task 6.

Generated by \`deploy/chatwoot-fork/rebase.sh --inventory\`.
EOF
  } > "${OUT}"
  echo "==> Wrote ${OUT} (${total} patches)"
}

if [[ "${MODE}" == "inventory" ]]; then
  do_inventory
  exit 0
fi

# ---------------------------------------------------------------------------
# Rebase
# ---------------------------------------------------------------------------
[[ -n "${SRC}" ]] || { usage >&2; die "--src is required for a rebase (there is no clone step; point it at a checkout you have)"; }
[[ -d "${SRC}" ]] || die "--src '${SRC}' is not a directory"
[[ -d "${SRC}/.git" ]] || die "--src '${SRC}' is not a git checkout (need .git to check out a ref and to report conflicts)"
command -v git >/dev/null 2>&1 || die "git not found"

if [[ -z "${WORKDIR}" ]]; then
  WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/chatwoot-rebase.XXXXXX")"
fi
mkdir -p "${WORKDIR}"
SCRATCH="${WORKDIR}/src"
LOGS="${WORKDIR}/logs"
mkdir -p "${LOGS}"

cleanup() {
  if [[ "${KEEP}" -eq 0 && "${FAILED_COUNT:-1}" -eq 0 ]]; then
    rm -rf "${WORKDIR}"
  else
    echo "==> Scratch tree and per-patch logs kept at ${WORKDIR}"
  fi
}
trap cleanup EXIT

echo "==> Copying ${SRC} to ${SCRATCH} (your checkout is not touched)"
rm -rf "${SCRATCH}"
cp -a "${SRC}" "${SCRATCH}"

cd "${SCRATCH}"
if [[ -n "${REF}" ]]; then
  echo "==> Checking out ${REF}"
  git checkout --quiet --force "${REF}" || die "cannot check out '${REF}' in the scratch copy — fetch it first in ${SRC}"
fi
git reset --quiet --hard
git clean -qfd
ACTUAL_REF="$(git describe --tags --always 2>/dev/null || git rev-parse --short HEAD)"
# A local identity, so the per-patch commits below work on a machine with no
# global git config (a CI runner, typically).
git config user.email rebase@proton.local
git config user.name proton-rebase

echo "==> Applying ${#PATCHES[@]} patches onto ${ACTUAL_REF} (pinned: ${PINNED_REF})"
if [[ -n "${REF}" && "${REF}" != "${PINNED_REF}" ]]; then
  echo "    NOTE: ${REF} differs from the pinned ${PINNED_REF}. If this run is"
  echo "          clean, update UPSTREAM_VERSION as part of the same change."
fi

APPLIED=()
FAILED=()
FAILED_COUNT=0
# Guard: everything below commits and reverts inside this tree. Refuse to run if
# we are not standing in the scratch copy — a revert in the wrong directory
# would throw away someone's real work.
[[ "$(pwd -P)" == "$(cd "${SCRATCH}" && pwd -P)" ]] \
  || die "internal: not in the scratch tree (${SCRATCH}); refusing to commit or revert anything"

for p in "${PATCHES[@]}"; do
  base="${p##*/}"
  if git apply --whitespace=fix "${p}" 2>"${LOGS}/${base}.log"; then
    APPLIED+=("${base}")
    printf '    ok      %s\n' "${base}"
    # Commit each applied patch into the scratch tree. This is what makes the
    # revert below safe: without it, reverting a failed patch's residue would
    # reset to plain upstream and silently discard every patch applied so far.
    # It also leaves a per-patch history, which is how you regenerate a fixed
    # patch with a real `git diff` instead of editing @@ arithmetic by hand.
    git add -A . >/dev/null
    git commit -q -m "proton: ${base}" --allow-empty
  else
    FAILED+=("${base}")
    FAILED_COUNT=$(( FAILED_COUNT + 1 ))
    if [[ "${FAILED_COUNT}" -eq 1 ]]; then
      printf '    FAILED  %s\n' "${base}"
    else
      printf '    FAILED  %s   (possibly cascading — see report)\n' "${base}"
    fi
    # Drop any residue and carry on from the last applied patch, so the next
    # patch gets a coherent base rather than half of a failed apply.
    git checkout --quiet -- . 2>/dev/null || true
    git clean -qfd
  fi
done

echo
echo "======================================================================"
echo " Rebase report: ${#APPLIED[@]} applied, ${#FAILED[@]} failed, onto ${ACTUAL_REF}"
echo "======================================================================"
if [[ ${#FAILED[@]} -eq 0 ]]; then
  cat <<EOF
Every patch applied.

That is NOT the same as "the image builds". The Vite build can still fail on a
patch that applied cleanly against a file upstream has since refactored. Next:

  1. Update deploy/chatwoot-fork/UPSTREAM_VERSION to ${ACTUAL_REF}.
  2. Build for amd64, off-VM:
       gcloud builds submit deploy/chatwoot-fork/ \\
         --config deploy/chatwoot-fork/cloudbuild.yaml \\
         --substitutions _REGISTRY=<AR repo>
  3. Deploy to non-prod first and verify — docs/runbooks/environments.md.
EOF
  exit 0
fi

cat <<EOF
${#FAILED[@]} patch(es) failed. All of them are listed here on purpose: fixing
them as a batch is a planned half-day, fixing them one rebuild at a time is a
week.

EOF
i=0
for base in "${FAILED[@]}"; do
  i=$(( i + 1 ))
  echo "----------------------------------------------------------------------"
  if [[ "${i}" -eq 1 ]]; then
    echo " ${base}  — independent failure"
  else
    echo " ${base}  — POSSIBLY CASCADING"
    echo "   An earlier patch was skipped, and patches in this series stack"
    echo "   (0054 sits on 0053's added lines; 0056 on 0002 plus 0055). This may"
    echo "   be a consequence of that skip rather than its own conflict."
  fi
  echo "   git apply said:"
  sed 's/^/     /' "${LOGS}/${base}.log" | head -n 20
  echo "   full log: ${LOGS}/${base}.log"
done
cat <<EOF
----------------------------------------------------------------------

Treat ${#FAILED[@]} as a CEILING on the number of real conflicts, not a total.
Fix them in order, re-running after each, until this reports zero — only then
is the count meaningful.

The scratch tree with the applied patches is at ${SCRATCH}; regenerate a fixed
patch with a real \`git diff\` there rather than editing @@ arithmetic by hand.
EOF
exit 1
