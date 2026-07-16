#!/usr/bin/env bash
# Decommission a tenant: stop its stack, drop its databases/roles, and remove
# its Caddy route. Prompts before destroying data.
#
# Usage:
#   deploy/scripts/remove-tenant.sh <tenant-name> [--purge-volumes]
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
INFRA_PROJECT="platform-infra"
INFRA_FILE="docker-compose.infra.yml"
TENANT_FILE="docker-compose.tenant.yml"

TENANT="${1:-}"
PURGE="${2:-}"
if [[ ! "${TENANT}" =~ ^[a-z][a-z0-9]*$ ]]; then
  echo "ERROR: tenant name must match ^[a-z][a-z0-9]*$ (got '${TENANT}')" >&2
  exit 1
fi

cd "${DEPLOY_DIR}"
ENV_FILE="tenants/${TENANT}.env"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} not found — nothing to remove for '${TENANT}'." >&2
  exit 1
fi

echo "This will STOP tenant '${TENANT}' and DROP its databases (chatwoot_${TENANT}, zammad_${TENANT}, agent_${TENANT})."
if [[ "${PURGE}" == "--purge-volumes" ]]; then
  echo "It will ALSO delete its storage volumes (attachments etc.) — irreversible."
fi
read -r -p "Type the tenant name to confirm: " confirm
if [[ "${confirm}" != "${TENANT}" ]]; then
  echo "Aborted." >&2
  exit 1
fi

# --- 1. Stop the tenant stack -----------------------------------------------
down_args=(down)
[[ "${PURGE}" == "--purge-volumes" ]] && down_args+=(--volumes)
docker compose -p "${TENANT}" -f "${TENANT_FILE}" --env-file "${ENV_FILE}" "${down_args[@]}" || true

# --- 2. Drop databases + roles ----------------------------------------------
docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<SQL
DROP DATABASE IF EXISTS chatwoot_${TENANT};
DROP DATABASE IF EXISTS zammad_${TENANT};
DROP DATABASE IF EXISTS agent_${TENANT};
DROP ROLE IF EXISTS chatwoot_${TENANT};
DROP ROLE IF EXISTS zammad_${TENANT};
DROP ROLE IF EXISTS agent_${TENANT};
SQL

# --- 3. Remove the Caddy route + reload -------------------------------------
rm -f "caddy/tenants/${TENANT}.caddy"
docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T caddy \
  caddy reload --config /etc/caddy/Caddyfile || true

# --- 4. Remove the env file -------------------------------------------------
rm -f "${ENV_FILE}"

echo "==> Tenant '${TENANT}' removed."
[[ "${PURGE}" != "--purge-volumes" ]] && \
  echo "    Storage volumes kept. Delete manually with: docker volume rm ${TENANT}_chatwoot_storage ${TENANT}_zammad_storage ${TENANT}_redis_data"
