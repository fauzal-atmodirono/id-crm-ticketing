#!/usr/bin/env bash
# Restore one tenant's databases and Chatwoot storage from a backup produced by
# deploy/scripts/backup.sh — optionally INTO a different tenant, which is the
# property that makes a drill possible without touching production.
#
# Usage:
#   deploy/scripts/restore.sh --tenant <src> --date <YYYY-MM-DD> \
#       [--into <dst>] [--apply] [--yes] [--force] \
#       [--from-gcs] [--local-only] [--skip-storage]
#
# ---------------------------------------------------------------------------
# WHAT THIS OVERWRITES
# ---------------------------------------------------------------------------
# With --apply, for the DESTINATION tenant (which is <src> unless --into says
# otherwise) this script destroys and replaces:
#
#   * every table in chatwoot_<dst>, agent_<dst> and backend_<dst> that the
#     archive contains a dump for (pg_restore --clean --if-exists: existing
#     objects are DROPPED, not merged);
#   * the entire contents of the <dst>_chatwoot_storage docker volume
#     (rm -rf then untar) — every attachment currently in it is gone;
#   * the running state of <dst>-chatwoot-rails, <dst>-chatwoot-sidekiq,
#     <dst>-agent and <dst>-backend, which are stopped and restarted.
#
# It does NOT touch: the tenant's env file, its Caddy route, its Redis data,
# its secrets, or any other tenant.
#
# ---------------------------------------------------------------------------
# THE SAFETY RULES, AND WHY EACH ONE IS THERE
# ---------------------------------------------------------------------------
#   1. **Dry run is the default.** Without --apply nothing is dropped, nothing
#      is written, no container is stopped: the plan is printed and the archive
#      is verified. A destructive default will one day meet a wrong argument.
#   2. **Verify before destroy.** Archive present, checksums match the manifest,
#      every dump parses under `pg_restore --list`, the storage tarball lists
#      cleanly. Only then is anything dropped. Restoring a truncated archive
#      over live data destroys the thing the backup existed to replace.
#   3. **--apply demands a typed confirmation** of the destination tenant name.
#      --yes skips the prompt for a scripted drill, and is REFUSED for an
#      in-place restore (no --into), because an unattended in-place restore of
#      production is the single worst accident available here.
#   4. **A destination with running containers is refused** unless --force.
#      Restoring underneath a live Rails process gives a half-migrated database
#      and a confusing outage rather than a clean recovery.
#
# ---------------------------------------------------------------------------
# WHAT HAS AND HAS NOT BEEN EXERCISED (read this before trusting it)
# ---------------------------------------------------------------------------
# EXERCISED, on a developer laptop, against stub `docker`/`gsutil` commands and
# fabricated archive files: argument parsing and rejection of bad input, the
# help text, the dry-run plan, checksum verification (including a deliberately
# corrupted archive being rejected), the missing-local-copy fallback path
# selection, the refusal to --apply without a confirmation, the refusal of
# --yes for an in-place restore, and the refusal to restore over running
# containers without --force. `bash -n` passes.
#
# **NOT exercised: any real restore.** No GCE VM, no live Postgres, no GCS
# bucket and no credentials existed in the environment this was written in, so
# no dump has ever been loaded by this script and no RTO has been measured by
# running it. A **restore rehearsal against a real backup is owed** before
# anyone relies on this in an incident — it is recorded as owed in
# docs/analysis/2026-08-09-blocked-work-register.md and the procedure is in
# docs/runbooks/disaster-recovery.md.
#
# ---------------------------------------------------------------------------
# KNOWN LIMITS OF THE ARCHIVE ITSELF (not of this script)
# ---------------------------------------------------------------------------
#   * Archives written before 2026-08-11 contain NO backend_<tenant> dump —
#     backup.sh did not dump it. Restoring such an archive cannot bring back the
#     operator-authored knowledge base or the RBAC tables; this script says so
#     rather than reporting a complete restore.
#   * Backups are nightly, so the RPO is up to 24 h of lost data. There is no
#     WAL archiving and no point-in-time recovery.
#   * Redis (job queues, caches) is not backed up and not restored. That is
#     correct — it is derived state — but in-flight Sidekiq jobs are lost.
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
INFRA_PROJECT="${INFRA_PROJECT:-platform-infra}"
INFRA_FILE="${INFRA_FILE:-docker-compose.infra.yml}"
TENANT_FILE="${TENANT_FILE:-docker-compose.tenant.yml}"
BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
BACKUP_GCS_BUCKET="${BACKUP_GCS_BUCKET:-}"
BACKUP_GCS_PREFIX="${BACKUP_GCS_PREFIX:-platform-backups}"
STAGE_ROOT="${RESTORE_STAGE_ROOT:-/var/tmp/platform-restore}"
# Test seams so the control flow can be exercised without Docker or gcloud.
# Never set these in production.
DOCKER="${PLATFORM_DOCKER_CMD:-docker}"
GSUTIL="${PLATFORM_GSUTIL_CMD:-gsutil}"

SRC=""
DATE=""
DST=""
APPLY=0
ASSUME_YES=0
FORCE=0
SOURCE_PREF="auto"   # auto | gcs | local
SKIP_STORAGE=0

die() { echo "ERROR: $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Restore a tenant from a backup.sh archive. Dry run unless --apply is given.

  --tenant <name>     Tenant the backup was TAKEN FROM (required)
  --date <YYYY-MM-DD> Backup date, i.e. the /backups/<date> directory (required)
  --into <name>       Restore INTO this tenant instead of --tenant. Use this for
                      a drill: it is what keeps production untouched.
  --apply             Actually do it. Without this, nothing is changed.
  --yes               Skip the typed confirmation (scripted drills only).
                      REFUSED for an in-place restore, i.e. without --into.
  --force             Allow restoring into a tenant whose containers are running.
  --from-gcs          Ignore any local copy and fetch the archive from GCS.
  --local-only        Never fall back to GCS; fail if the local copy is missing.
  --skip-storage      Restore databases only, not the Chatwoot storage volume.
  -h, --help          This text.

Examples
  # See what a drill would do (changes nothing):
  ./restore.sh --tenant proton --date 2026-08-10 --into scratch
  # Run the drill from the offsite copy, as a real disaster would:
  ./restore.sh --tenant proton --date 2026-08-10 --into scratch --from-gcs --apply
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tenant) SRC="${2:-}"; shift 2 ;;
    --date) DATE="${2:-}"; shift 2 ;;
    --into) DST="${2:-}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    --force) FORCE=1; shift ;;
    --from-gcs) SOURCE_PREF="gcs"; shift ;;
    --local-only) SOURCE_PREF="local"; shift ;;
    --skip-storage) SKIP_STORAGE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument '$1'" ;;
  esac
done

[[ -n "${SRC}" ]] || { usage >&2; die "--tenant is required"; }
[[ -n "${DATE}" ]] || { usage >&2; die "--date is required"; }
[[ "${SRC}" =~ ^[a-z][a-z0-9]*$ ]] || die "--tenant must match ^[a-z][a-z0-9]*\$ (got '${SRC}')"
DST="${DST:-${SRC}}"
[[ "${DST}" =~ ^[a-z][a-z0-9]*$ ]] || die "--into must match ^[a-z][a-z0-9]*\$ (got '${DST}')"
[[ "${DATE}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "--date must be YYYY-MM-DD (got '${DATE}')"
if [[ "${SOURCE_PREF}" == "gcs" && "${BACKUP_GCS_BUCKET}" == "" ]]; then
  die "--from-gcs needs BACKUP_GCS_BUCKET set (there is no offsite copy configured)"
fi

IN_PLACE=0
[[ "${DST}" == "${SRC}" ]] && IN_PLACE=1
if [[ "${APPLY}" -eq 1 && "${ASSUME_YES}" -eq 1 && "${IN_PLACE}" -eq 1 ]]; then
  die "--yes is refused for an in-place restore (no --into): an unattended restore over the source tenant is the accident this guard exists for. Confirm interactively, or restore into a scratch tenant with --into."
fi

cd "${DEPLOY_DIR}"

MODE="DRY RUN (nothing will be changed)"
[[ "${APPLY}" -eq 1 ]] && MODE="APPLY (destructive)"

cat <<EOF
==> restore.sh — ${MODE}
    source tenant : ${SRC}
    backup date   : ${DATE}
    destination   : ${DST}$([[ "${IN_PLACE}" -eq 1 ]] && echo "   <-- IN PLACE, over the live tenant")
    archive source: ${SOURCE_PREF}
EOF

# ---------------------------------------------------------------------------
# 1. Locate the archive: local copy first unless told otherwise, then GCS.
#    The GCS path is the real disaster case — a lost VM takes /backups with it.
# ---------------------------------------------------------------------------
LOCAL_DIR="${BACKUP_ROOT}/${DATE}"
ARCHIVE_DIR=""
ARCHIVE_ORIGIN=""

local_copy_usable() {
  [[ -d "${LOCAL_DIR}" ]] && [[ -f "${LOCAL_DIR}/${SRC}-chatwoot.dump" ]]
}

fetch_from_gcs() {
  local remote="gs://${BACKUP_GCS_BUCKET}/${BACKUP_GCS_PREFIX}/${DATE}"
  local stage="${STAGE_ROOT}/${DATE}"
  echo "==> Fetching the archive from ${remote}"
  if [[ "${APPLY}" -eq 0 ]]; then
    # A dry run must not write to disk either, so it only proves the objects are
    # there and reports that checksum/parse verification needs the real fetch.
    echo "    [dry run] would stage into ${stage}"
    "${GSUTIL}" ls "${remote}/" >/dev/null 2>&1 \
      || die "no archive at ${remote} (checked with gsutil ls)"
    echo "    objects present at ${remote}:"
    "${GSUTIL}" ls "${remote}/" | sed 's/^/      /'
    return 1   # nothing staged; caller falls back to verify-what-it-can
  fi
  mkdir -p "${stage}"
  "${GSUTIL}" -m cp "${remote}/*" "${stage}/" \
    || die "failed to fetch the archive from ${remote}"
  ARCHIVE_DIR="${stage}"
  ARCHIVE_ORIGIN="gcs (${remote}), staged in ${stage}"
  return 0
}

GCS_DRY=0
case "${SOURCE_PREF}" in
  local)
    local_copy_usable || die "no local archive for ${SRC} at ${LOCAL_DIR} and --local-only was given"
    ARCHIVE_DIR="${LOCAL_DIR}"; ARCHIVE_ORIGIN="local ${LOCAL_DIR}"
    ;;
  gcs)
    fetch_from_gcs || GCS_DRY=1
    ;;
  auto)
    if local_copy_usable; then
      ARCHIVE_DIR="${LOCAL_DIR}"; ARCHIVE_ORIGIN="local ${LOCAL_DIR}"
    else
      echo "==> No usable local copy at ${LOCAL_DIR} — falling back to the offsite copy"
      [[ -n "${BACKUP_GCS_BUCKET}" ]] \
        || die "no local archive at ${LOCAL_DIR} and BACKUP_GCS_BUCKET is unset — there is nothing to restore from"
      fetch_from_gcs || GCS_DRY=1
    fi
    ;;
esac

if [[ "${GCS_DRY}" -eq 1 ]]; then
  cat <<EOF

==> DRY RUN, offsite archive: the objects above exist, but checksum and
    pg_restore --list verification need the archive on disk and this dry run
    deliberately writes nothing. Re-run with --apply to fetch and verify, or
    stage it yourself and re-run with --local-only.
EOF
  exit 0
fi

echo "==> Archive: ${ARCHIVE_ORIGIN}"

# ---------------------------------------------------------------------------
# 2. Verify before destroying anything.
# ---------------------------------------------------------------------------
DUMPS=()
for app in chatwoot agent backend; do
  f="${ARCHIVE_DIR}/${SRC}-${app}.dump"
  if [[ -f "${f}" ]]; then
    DUMPS+=("${app}")
  elif [[ "${app}" == "backend" ]]; then
    echo "    NOTE: no ${SRC}-backend.dump in this archive. Archives written"
    echo "          before 2026-08-11 do not contain one, so the operator-authored"
    echo "          knowledge base and RBAC tables CANNOT be restored from it." >&2
  else
    die "${f} is missing — this archive cannot restore ${SRC}"
  fi
done
[[ ${#DUMPS[@]} -gt 0 ]] || die "no dumps for ${SRC} in ${ARCHIVE_DIR}"

STORAGE_TAR="${ARCHIVE_DIR}/${SRC}-chatwoot_storage.tar.gz"
if [[ "${SKIP_STORAGE}" -eq 0 && ! -f "${STORAGE_TAR}" ]]; then
  echo "    WARNING: ${STORAGE_TAR} is missing — attachments will NOT be restored." >&2
  SKIP_STORAGE=1
fi

echo "==> Verifying checksums"
if [[ -f "${ARCHIVE_DIR}/SHA256SUMS" ]]; then
  ( cd "${ARCHIVE_DIR}" && grep " ${SRC}-" SHA256SUMS | sha256sum -c - ) \
    || die "checksum verification FAILED for ${SRC} in ${ARCHIVE_DIR} — refusing to restore a corrupt archive. Nothing has been changed."
  echo "    checksums OK"
else
  echo "    WARNING: no SHA256SUMS manifest in this archive (written by backups" >&2
  echo "             taken before 2026-08-11). Integrity is UNVERIFIED — the" >&2
  echo "             pg_restore --list parse below is the only check available." >&2
fi

echo "==> Verifying each dump parses (pg_restore --list)"
# The only pg_restore on this VM is the one inside the shared postgres
# container, so this check — and therefore a dry run too — needs shared infra
# up. Checked separately so a failure below unambiguously means a bad dump
# rather than an unreachable container.
"${DOCKER}" compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres true >/dev/null 2>&1 \
  || die "cannot reach the shared postgres container (project ${INFRA_PROJECT}, file ${INFRA_FILE}). Bring shared infra up first: docker compose -p ${INFRA_PROJECT} -f ${INFRA_FILE} --env-file infra.env up -d"
for app in "${DUMPS[@]}"; do
  if ! "${DOCKER}" compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres \
        pg_restore --list > /dev/null < "${ARCHIVE_DIR}/${SRC}-${app}.dump"; then
    die "${SRC}-${app}.dump does not parse as a pg_dump archive — refusing to restore. Nothing has been changed."
  fi
  echo "    ${SRC}-${app}.dump parses"
done

if [[ "${SKIP_STORAGE}" -eq 0 ]]; then
  echo "==> Verifying the storage tarball lists cleanly"
  tar tzf "${STORAGE_TAR}" > /dev/null \
    || die "${STORAGE_TAR} is not a readable gzip tar — refusing to restore. Nothing has been changed."
  echo "    ${SRC}-chatwoot_storage.tar.gz OK"
fi

# ---------------------------------------------------------------------------
# 3. Pre-flight the destination.
# ---------------------------------------------------------------------------
DST_ENV="tenants/${DST}.env"
[[ -f "${DST_ENV}" ]] \
  || die "${DEPLOY_DIR}/${DST_ENV} not found — tenant '${DST}' is not provisioned. Create it first with scripts/add-tenant.sh (a restore replaces a tenant's data; it does not create the tenant, its roles, or its Caddy route)."

SERVICES=(chatwoot-rails chatwoot-sidekiq agent backend)
RUNNING=()
for svc in "${SERVICES[@]}"; do
  if "${DOCKER}" ps --format '{{.Names}}' 2>/dev/null | grep -qx "${DST}-${svc}"; then
    RUNNING+=("${DST}-${svc}")
  fi
done
if [[ ${#RUNNING[@]} -gt 0 ]]; then
  echo "==> Destination '${DST}' has running containers: ${RUNNING[*]}"
  if [[ "${FORCE}" -eq 0 ]]; then
    die "refusing to restore into a tenant with running containers. This is a live tenant. Either stop it, restore into a scratch tenant with --into, or pass --force if you really mean to take '${DST}' down and overwrite it."
  fi
  echo "    --force given: they will be STOPPED, overwritten, and started again."
fi

# ---------------------------------------------------------------------------
# 4. The plan.
# ---------------------------------------------------------------------------
cat <<EOF

==> PLAN
    For each of: ${DUMPS[*]}
      docker compose exec -T postgres pg_restore -U postgres \\
        -d <app>_${DST} --clean --if-exists --no-owner --no-privileges \\
        < ${ARCHIVE_DIR}/${SRC}-<app>.dump
      ** every existing object in <app>_${DST} is DROPPED and replaced **
EOF
if [[ "${SKIP_STORAGE}" -eq 0 ]]; then
  cat <<EOF
    Storage volume ${DST}_chatwoot_storage:
      rm -rf its entire contents, then untar ${SRC}-chatwoot_storage.tar.gz
      ** every attachment currently in ${DST}_chatwoot_storage is DELETED **
EOF
else
  echo "    Storage volume: SKIPPED (attachments left as they are)"
fi
cat <<EOF
    Containers stopped then started: ${SERVICES[*]/#/${DST}-}
    Untouched: ${DST}.env, its Caddy route, its Redis data, all other tenants.

EOF

if [[ "${APPLY}" -eq 0 ]]; then
  cat <<EOF
==> DRY RUN complete. The archive verified and NOTHING was changed.
    Re-run with --apply to execute the plan above.
EOF
  exit 0
fi

# ---------------------------------------------------------------------------
# 5. Deliberate confirmation.
# ---------------------------------------------------------------------------
if [[ "${ASSUME_YES}" -eq 0 ]]; then
  if [[ "${IN_PLACE}" -eq 1 ]]; then
    echo "!!! This overwrites tenant '${DST}' IN PLACE with the ${DATE} backup."
    echo "!!! Any data created since ${DATE} in '${DST}' will be LOST."
  else
    echo "This overwrites tenant '${DST}' with ${SRC}'s ${DATE} backup."
  fi
  read -r -p "Type the destination tenant name ('${DST}') to confirm: " confirm
  [[ "${confirm}" == "${DST}" ]] || die "confirmation did not match — aborted. Nothing has been changed."
else
  echo "==> --yes given, skipping the confirmation prompt (drill into '${DST}')."
fi

START_EPOCH="$(date +%s)"
echo "==> $(date -Is) Restore starting (source ${SRC} ${DATE} -> ${DST})"

compose_dst() {
  "${DOCKER}" compose -p "${DST}" -f "${TENANT_FILE}" --env-file "${DST_ENV}" "$@"
}
pg_dst() {
  "${DOCKER}" compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres "$@"
}

echo "==> Stopping ${DST} application services"
compose_dst stop "${SERVICES[@]}" || true

for app in "${DUMPS[@]}"; do
  echo "==> Restoring ${app}_${DST} from ${SRC}-${app}.dump"
  # --no-owner/--no-privileges: the dump was taken as `postgres` but the
  # destination roles are per-tenant (chatwoot_<dst> etc.), so ownership from
  # the source tenant must not be reasserted — that is exactly what would break
  # a cross-tenant drill restore.
  #
  # pg_restore exits non-zero on benign "does not exist, skipping" noise from
  # --clean against a fresh database, so its status is reported rather than
  # trusted, and the row counts below are what actually decides success.
  if pg_dst pg_restore -U postgres -d "${app}_${DST}" \
       --clean --if-exists --no-owner --no-privileges \
       < "${ARCHIVE_DIR}/${SRC}-${app}.dump"; then
    echo "    ${app}_${DST}: pg_restore reported success"
  else
    echo "    WARNING: pg_restore reported errors for ${app}_${DST}." >&2
    echo "             Some are expected with --clean against a fresh database" >&2
    echo "             ('does not exist, skipping'). Check the row counts below" >&2
    echo "             before calling this restore good." >&2
  fi
done

if [[ "${SKIP_STORAGE}" -eq 0 ]]; then
  VOLUME="$("${DOCKER}" volume ls --format '{{.Name}}' | grep -E "(^|_)${DST}_chatwoot_storage\$" | head -n1 || true)"
  if [[ -z "${VOLUME}" ]]; then
    echo "WARNING: no ${DST}_chatwoot_storage volume found — attachments NOT restored." >&2
  else
    echo "==> Restoring attachments into volume ${VOLUME}"
    "${DOCKER}" run --rm -v "${VOLUME}:/dest" -v "${ARCHIVE_DIR}:/src:ro" alpine \
      sh -c "rm -rf /dest/* /dest/..?* /dest/.[!.]* 2>/dev/null; tar xzf /src/${SRC}-chatwoot_storage.tar.gz -C /dest"
  fi
fi

echo "==> Starting ${DST} application services"
compose_dst start "${SERVICES[@]}"

# ---------------------------------------------------------------------------
# 6. Verify the restore, against the counts recorded at dump time when they
#    exist. A restore nobody checked is a restore nobody can rely on.
# ---------------------------------------------------------------------------
echo "==> Post-restore row counts in chatwoot_${DST}"
restored="$(pg_dst psql -tAX -U postgres -d "chatwoot_${DST}" -c \
  "SELECT json_build_object('conversations',(SELECT count(*) FROM conversations),'contacts',(SELECT count(*) FROM contacts),'messages',(SELECT count(*) FROM messages))::text" \
  2>/dev/null || true)"
if [[ -z "${restored}" ]]; then
  echo "    WARNING: could not read row counts from chatwoot_${DST}." >&2
  echo "             The restore is UNVERIFIED — check it by hand." >&2
else
  echo "    restored: ${restored}"
  SRC_COUNTS="${ARCHIVE_DIR}/${SRC}-counts.json"
  if [[ -f "${SRC_COUNTS}" ]]; then
    src_counts="$(tr -d '[:space:]' < "${SRC_COUNTS}")"
    echo "    source  : ${src_counts}"
    if [[ "$(printf '%s' "${restored}" | tr -d '[:space:]')" == "${src_counts}" ]]; then
      echo "    MATCH: conversation, contact and message counts equal the source."
    else
      echo "    MISMATCH: restored counts differ from the counts recorded at dump time." >&2
      echo "              Do NOT treat this restore as good. Investigate before use." >&2
    fi
  else
    echo "    No ${SRC}-counts.json in this archive, so there is nothing to compare"
    echo "    against — count comparison UNAVAILABLE, not passed. Verify by hand"
    echo "    against the source system if it still exists."
  fi
fi

ELAPSED=$(( $(date +%s) - START_EPOCH ))
cat <<EOF

==> $(date -Is) Restore finished in ${ELAPSED}s ($(( ELAPSED / 60 ))m$(( ELAPSED % 60 ))s).
    That figure is the DATA-RESTORE time only. A real RTO also includes
    noticing the outage, provisioning a VM, bootstrapping it and provisioning
    the tenant. Record the full end-to-end figure in
    docs/runbooks/disaster-recovery.md — the measured number is the deliverable.

    Still to check by hand (this script cannot):
      * log in to Chatwoot as ${DST} and open a restored conversation
      * confirm an attachment on it downloads (proves storage, not just the DB)
      * confirm the agent service answers /healthz
EOF
