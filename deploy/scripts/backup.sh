#!/usr/bin/env bash
# Nightly backup: pg_dump's the chatwoot/zammad/agent databases and tars the
# chatwoot/zammad storage volumes into /backups/YYYY-MM-DD/, then prunes
# backup directories older than 7 days. Cron-safe (no interactive prompts,
# absolute paths only).
#
# Install with:
#   0 3 * * * /opt/platform/deploy/scripts/backup.sh >> /var/log/platform-backup.log 2>&1
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/platform/deploy}"
BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
DATE="$(date +%F)"
DEST="${BACKUP_ROOT}/${DATE}"

echo "==> $(date -Is) Starting backup into ${DEST}"
mkdir -p "${DEST}"

cd "${DEPLOY_DIR}"

# ---------------------------------------------------------------------------
# Database dumps (custom format, restorable with pg_restore)
# ---------------------------------------------------------------------------
for db in chatwoot zammad agent; do
  echo "==> Dumping database: ${db}"
  docker compose exec -T postgres pg_dump -U postgres -Fc "${db}" > "${DEST}/${db}.dump"
done

# ---------------------------------------------------------------------------
# Storage volumes (Chatwoot attachments, Zammad attachments/knowledge base)
# ---------------------------------------------------------------------------
resolve_volume() {
  # Compose prefixes named volumes with the project name (e.g.
  # "deploy_chatwoot_storage"); match by suffix so this works regardless of
  # COMPOSE_PROJECT_NAME.
  local logical_name="$1"
  docker volume ls --format '{{.Name}}' | grep -E "(^|_)${logical_name}\$" | head -n1
}

for logical in chatwoot_storage zammad_storage; do
  volume="$(resolve_volume "${logical}")"
  if [[ -z "${volume}" ]]; then
    echo "WARNING: could not resolve docker volume for ${logical}, skipping" >&2
    continue
  fi
  echo "==> Archiving volume: ${volume}"
  docker run --rm \
    -v "${volume}:/src:ro" \
    -v "${DEST}:/dest" \
    alpine tar czf "/dest/${logical}.tar.gz" -C /src .
done

# ---------------------------------------------------------------------------
# Prune backups older than RETENTION_DAYS
# ---------------------------------------------------------------------------
echo "==> Pruning backups older than ${RETENTION_DAYS} days"
find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -exec rm -rf {} +

echo "==> $(date -Is) Backup complete: ${DEST}"
