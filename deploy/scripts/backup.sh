#!/usr/bin/env bash
# Nightly backup for the multi-tenant platform: for every tenant defined by
# deploy/tenants/*.env, pg_dump its three databases and tar its Chatwoot/Zammad
# storage volumes into /backups/YYYY-MM-DD/, then prune dirs older than 7 days.
# Cron-safe (no interactive prompts, absolute paths only).
#
# Install with:
#   0 3 * * * /opt/platform/deploy/scripts/backup.sh >> /var/log/platform-backup.log 2>&1
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/platform/deploy}"
INFRA_PROJECT="${INFRA_PROJECT:-platform-infra}"
INFRA_FILE="${INFRA_FILE:-docker-compose.infra.yml}"
BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
DATE="$(date +%F)"
DEST="${BACKUP_ROOT}/${DATE}"

echo "==> $(date -Is) Starting backup into ${DEST}"
mkdir -p "${DEST}"
cd "${DEPLOY_DIR}"

pg() {
  docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres "$@"
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
  [[ -z "${tenant}" ]] && { echo "WARNING: no TENANT in ${env_file}, skipping" >&2; continue; }

  echo "==> Backing up tenant: ${tenant}"
  for app in chatwoot zammad agent; do
    echo "    dumping ${app}_${tenant}"
    pg pg_dump -U postgres -Fc "${app}_${tenant}" > "${DEST}/${tenant}-${app}.dump"
  done

  for logical in chatwoot_storage zammad_storage; do
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

echo "==> Pruning backups older than ${RETENTION_DAYS} days"
find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -exec rm -rf {} +

echo "==> $(date -Is) Backup complete: ${DEST}"
