#!/usr/bin/env bash
# Nightly backup for the multi-tenant platform: for every tenant defined by
# deploy/tenants/*.env, pg_dump its databases and tar its Chatwoot
# storage volumes into /backups/YYYY-MM-DD/, then prune dirs older than 7 days
# and (if BACKUP_GCS_BUCKET is set) sync the night's directory offsite.
# Cron-safe (no interactive prompts, absolute paths only).
#
# Install with:
#   0 3 * * * /opt/platform/deploy/scripts/backup.sh >> /var/log/platform-backup.log 2>&1
#
# ---------------------------------------------------------------------------
# The offsite copy (P13 task 1)
# ---------------------------------------------------------------------------
# A backup kept on the machine it protects is a convenience copy: losing the VM
# loses the data and the backups together. So when BACKUP_GCS_BUCKET is set the
# night's directory is copied to a GCS bucket that should live in a DIFFERENT
# region from the VM, and the copy is verified before the script reports success.
#
# BACKUP_GCS_BUCKET unset is the supported default and keeps today's behaviour
# byte for byte — a tenant that never sets it sees no change at all.
#
# **A failed offsite sync exits non-zero and shouts**, because a silent offsite
# failure recreates exactly the situation this exists to fix while looking
# solved. Local pruning runs BEFORE the sync so a persistent sync failure can
# never fill the disk (disk exhaustion on this single VM takes everything down
# at once). Note the "alert" is stderr + syslog + an optional webhook: **no
# alert channel is wired up on any VM today** — see
# docs/runbooks/monitoring-alerts.md for what an operator has to add before
# anything actually reaches a human. Until then the reachable signal is cron's
# own mail on a non-zero exit.
#
# ---------------------------------------------------------------------------
# What has and has not been exercised
# ---------------------------------------------------------------------------
# NOT exercised: no run against a real VM, real Postgres, or a real GCS bucket
# has happened — there is no such infrastructure and no credentials in the
# environment this was written in. `bash -n` passes and the offsite path was
# exercised against a stub gsutil (PLATFORM_GSUTIL_CMD, below). Treat the
# offsite copy as unproven until a nightly run has been inspected on the VM.
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/platform/deploy}"
INFRA_PROJECT="${INFRA_PROJECT:-platform-infra}"
INFRA_FILE="${INFRA_FILE:-docker-compose.infra.yml}"
BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
# Offsite copy. Unset bucket = local-only, i.e. exactly today's behaviour.
BACKUP_GCS_BUCKET="${BACKUP_GCS_BUCKET:-}"
BACKUP_GCS_PREFIX="${BACKUP_GCS_PREFIX:-platform-backups}"
BACKUP_ALERT_WEBHOOK="${BACKUP_ALERT_WEBHOOK:-}"
# Test seams: overridable so the offsite path can be exercised without gcloud
# installed and without a bucket. Never set these in production.
GSUTIL="${PLATFORM_GSUTIL_CMD:-gsutil}"
DATE="$(date +%F)"
DEST="${BACKUP_ROOT}/${DATE}"

# Loud failure. Every channel here is best-effort except the exit status, which
# is the only one guaranteed to be noticed today (cron mails a non-zero exit).
alert() {
  local msg="$1"
  echo "ALERT: ${msg}" >&2
  logger -t platform-backup "ALERT: ${msg}" 2>/dev/null || true
  if [[ -n "${BACKUP_ALERT_WEBHOOK}" ]]; then
    curl -fsS -m 15 -X POST -H 'Content-Type: application/json' \
      --data "$(printf '{"text":"platform-backup: %s"}' "${msg}")" \
      "${BACKUP_ALERT_WEBHOOK}" >/dev/null 2>&1 \
      || echo "WARNING: alert webhook POST failed" >&2
  fi
}

echo "==> $(date -Is) Starting backup into ${DEST}"
mkdir -p "${DEST}"
cd "${DEPLOY_DIR}"

pg() {
  docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres "$@"
}

db_exists() {
  # Guard the dump loop: `backend_<tenant>` only exists for tenants provisioned
  # after the backend service was added, and under `set -e` a pg_dump against a
  # missing database would abort the whole night's backup for every later
  # tenant. Missing is warned about, never silently skipped.
  local db="$1"
  pg psql -tAX -U postgres -d postgres \
    -c "SELECT 1 FROM pg_database WHERE datname = '${db}'" 2>/dev/null | grep -q '^1$'
}

resolve_volume() {
  # Named volume "<tenant>_<logical>" — match exactly by tenant-prefixed name.
  local name="$1"
  docker volume ls --format '{{.Name}}' | grep -E "(^|_)${name}\$" | head -n1
}

shopt -s nullglob
tenant_envs=(tenants/*.env)
if [[ ${#tenant_envs[@]} -eq 0 ]]; then
  echo "WARNING: no tenants/*.env found; nothing to back up" >&2
fi

for env_file in "${tenant_envs[@]}"; do
  [[ "$(basename "${env_file}")" == "example.env" ]] && continue
  tenant="$(grep -E '^TENANT=' "${env_file}" | head -n1 | cut -d= -f2-)"
  tenant="${tenant//[[:space:]\"\']/}"
  if [[ ! "${tenant}" =~ ^[a-z][a-z0-9]*$ ]]; then
    echo "WARNING: ${env_file} has invalid/empty TENANT ('${tenant}'), skipping" >&2
    continue
  fi

  echo "==> Backing up tenant: ${tenant}"
  # `backend` holds the operator-authored pgvector knowledge base and the RBAC
  # tables; it was created by add-tenant.sh from the start but was NOT in this
  # loop until 2026-08-11, so **archives written before that date contain no
  # backend_<tenant> dump** and restoring one of them cannot bring the KB back.
  for app in chatwoot agent backend; do
    if ! db_exists "${app}_${tenant}"; then
      echo "    WARNING: database ${app}_${tenant} does not exist, skipping" >&2
      continue
    fi
    echo "    dumping ${app}_${tenant}"
    pg pg_dump -U postgres -Fc "${app}_${tenant}" > "${DEST}/${tenant}-${app}.dump"
  done

  # Row counts at dump time, so restore.sh can compare the restored tenant
  # against the source rather than asking an operator to eyeball it. Best
  # effort: if the query fails, NO counts file is written and restore.sh says
  # the comparison is unavailable. It must never write a 0 it did not measure.
  counts="$(pg psql -tAX -U postgres -d "chatwoot_${tenant}" -c \
    "SELECT json_build_object('conversations',(SELECT count(*) FROM conversations),'contacts',(SELECT count(*) FROM contacts),'messages',(SELECT count(*) FROM messages))::text" \
    2>/dev/null || true)"
  if [[ -n "${counts}" ]]; then
    printf '%s\n' "${counts}" > "${DEST}/${tenant}-counts.json"
  else
    echo "    WARNING: could not record source row counts for ${tenant}" >&2
  fi

  for logical in chatwoot_storage; do
    volume="$(resolve_volume "${tenant}_${logical}")"
    if [[ -z "${volume}" ]]; then
      echo "    WARNING: no volume for ${tenant}_${logical}, skipping" >&2
      continue
    fi
    echo "    archiving ${volume}"
    docker run --rm -v "${volume}:/src:ro" -v "${DEST}:/dest" \
      alpine tar czf "/dest/${tenant}-${logical}.tar.gz" -C /src .
  done
done


# --- Checksum manifest -------------------------------------------------------
# restore.sh verifies against this before it drops anything, so an archive
# truncated by a full disk is caught before it destroys the thing it was
# supposed to replace. Filenames here are all script-generated
# (<tenant>-<app>.dump / <tenant>-<logical>.tar.gz) and contain no spaces.
echo "==> Writing checksum manifest"
(
  cd "${DEST}"
  find . -maxdepth 1 -type f ! -name SHA256SUMS ! -name SHA256SUMS.tmp \
    | sed 's|^\./||' | sort | xargs -r sha256sum > SHA256SUMS.tmp
  mv SHA256SUMS.tmp SHA256SUMS
)

echo "==> Pruning backups older than ${RETENTION_DAYS} days"
find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -exec rm -rf {} +

# --- Offsite copy ------------------------------------------------------------
# Runs after pruning on purpose: a sync that fails every night must not also
# stop the local prune and fill the disk.
if [[ -z "${BACKUP_GCS_BUCKET}" ]]; then
  echo "==> BACKUP_GCS_BUCKET unset — backups are LOCAL ONLY on this VM."
  echo "    Losing the VM loses the data and its backups together."
  echo "    See docs/runbooks/disaster-recovery.md to set the bucket up."
else
  remote="gs://${BACKUP_GCS_BUCKET}/${BACKUP_GCS_PREFIX}/${DATE}"
  echo "==> Syncing ${DEST} to ${remote}"
  if ! "${GSUTIL}" -m rsync -r -d "${DEST}" "${remote}"; then
    alert "offsite sync to ${remote} FAILED — tonight's backup exists only on this VM"
    exit 1
  fi

  # Verify rather than trust the exit code. `gsutil rsync`/`cp` validate their
  # own upload checksums, so the end-to-end content guarantee is theirs; what is
  # checked here independently is that the manifest object came back byte for
  # byte and that every file it names is present remotely with the right size.
  echo "==> Verifying the offsite copy"
  if ! "${GSUTIL}" cat "${remote}/SHA256SUMS" 2>/dev/null | diff -q - "${DEST}/SHA256SUMS" >/dev/null; then
    alert "offsite manifest at ${remote}/SHA256SUMS does not match the local one"
    exit 1
  fi
  verify_failed=0
  while read -r _sum name; do
    # `tr -d` because `wc -c <` pads its output with leading spaces on BSD
    # userlands; comparing the raw strings made every object look mismatched.
    local_size="$(wc -c < "${DEST}/${name}" | tr -d '[:space:]')"
    remote_size="$("${GSUTIL}" stat "${remote}/${name}" 2>/dev/null \
      | awk -F: '/Content-Length/ { gsub(/[^0-9]/, "", $2); print $2 }' | head -n1)"
    if [[ -z "${remote_size}" ]]; then
      alert "offsite object ${remote}/${name} is MISSING"
      verify_failed=1
    elif [[ "${remote_size}" -ne "${local_size}" ]]; then
      alert "offsite object ${remote}/${name} is ${remote_size} bytes, local is ${local_size}"
      verify_failed=1
    fi
  done < "${DEST}/SHA256SUMS"
  if [[ "${verify_failed}" -ne 0 ]]; then
    exit 1
  fi
  echo "==> Offsite copy verified: $(wc -l < "${DEST}/SHA256SUMS" | tr -d '[:space:]') objects at ${remote}"
fi

echo "==> $(date -Is) Backup complete: ${DEST}"
