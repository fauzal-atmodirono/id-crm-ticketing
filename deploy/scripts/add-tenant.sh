#!/usr/bin/env bash
# Provision one tenant end to end: generate secrets, create its Postgres
# roles/databases on the shared server, render its Caddy route, and bring its
# app stack up. Run from the VM after shared infra (docker-compose.infra.yml)
# is up.
#
# Usage:
#   deploy/scripts/add-tenant.sh <tenant-name>
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
INFRA_PROJECT="platform-infra"
INFRA_FILE="docker-compose.infra.yml"
TENANT_FILE="docker-compose.tenant.yml"

TENANT="${1:-}"
if [[ ! "${TENANT}" =~ ^[a-z][a-z0-9]*$ ]]; then
  echo "ERROR: tenant name must match ^[a-z][a-z0-9]*$ (got '${TENANT}')" >&2
  exit 1
fi

cd "${DEPLOY_DIR}"

ENV_FILE="tenants/${TENANT}.env"
if [[ -e "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} already exists — tenant '${TENANT}' looks provisioned." >&2
  echo "       Use remove-tenant.sh first, or edit the env and re-run compose up." >&2
  exit 1
fi

# --- Preconditions: shared network + postgres must be up --------------------
if ! docker network inspect platform >/dev/null 2>&1; then
  echo "ERROR: shared 'platform' network not found. Bring infra up first:" >&2
  echo "       docker compose -p ${INFRA_PROJECT} -f ${INFRA_FILE} --env-file infra.env up -d" >&2
  exit 1
fi

if [[ ! -f infra.env ]]; then
  echo "ERROR: deploy/infra.env not found (needed for PUBLIC_IP + Mailpit auth)." >&2
  exit 1
fi
# shellcheck disable=SC1091
set -a; . ./infra.env; set +a
: "${PUBLIC_IP:?PUBLIC_IP must be set in infra.env}"
: "${MAILPIT_AUTH_USER:?MAILPIT_AUTH_USER must be set in infra.env}"
: "${MAILPIT_AUTH_HASH:?MAILPIT_AUTH_HASH must be set in infra.env}"

echo "==> Provisioning tenant '${TENANT}' (PUBLIC_IP=${PUBLIC_IP})"

# --- 1. Generate per-tenant secrets + env file ------------------------------
CHATWOOT_DB_PASSWORD="$(openssl rand -hex 16)"
ZAMMAD_DB_PASSWORD="$(openssl rand -hex 16)"
AGENT_DB_PASSWORD="$(openssl rand -hex 16)"
REDIS_PASSWORD="$(openssl rand -hex 16)"
SECRET_KEY_BASE="$(openssl rand -hex 64)"

sed \
  -e "s/^TENANT=.*/TENANT=${TENANT}/" \
  -e "s/^PUBLIC_IP=.*/PUBLIC_IP=${PUBLIC_IP}/" \
  -e "s/^CHATWOOT_DB_PASSWORD=.*/CHATWOOT_DB_PASSWORD=${CHATWOOT_DB_PASSWORD}/" \
  -e "s/^ZAMMAD_DB_PASSWORD=.*/ZAMMAD_DB_PASSWORD=${ZAMMAD_DB_PASSWORD}/" \
  -e "s/^AGENT_DB_PASSWORD=.*/AGENT_DB_PASSWORD=${AGENT_DB_PASSWORD}/" \
  -e "s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=${REDIS_PASSWORD}/" \
  -e "s/^SECRET_KEY_BASE=.*/SECRET_KEY_BASE=${SECRET_KEY_BASE}/" \
  tenants/example.env > "${ENV_FILE}"
echo "==> Wrote ${ENV_FILE}"

# --- 2. Create Postgres roles + databases on the running server -------------
echo "==> Creating databases chatwoot_${TENANT} / zammad_${TENANT} / agent_${TENANT}"
docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<SQL
CREATE ROLE chatwoot_${TENANT} LOGIN PASSWORD '${CHATWOOT_DB_PASSWORD}';
CREATE ROLE zammad_${TENANT}   LOGIN PASSWORD '${ZAMMAD_DB_PASSWORD}';
CREATE ROLE agent_${TENANT}    LOGIN PASSWORD '${AGENT_DB_PASSWORD}';
CREATE DATABASE chatwoot_${TENANT} OWNER chatwoot_${TENANT};
CREATE DATABASE zammad_${TENANT}   OWNER zammad_${TENANT};
CREATE DATABASE agent_${TENANT}    OWNER agent_${TENANT};
SQL

# Chatwoot uses pgvector; create the extension as superuser in its DB.
docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U postgres -d "chatwoot_${TENANT}" \
  -c 'CREATE EXTENSION IF NOT EXISTS vector;'

# --- 3. Render + install the Caddy route, then reload -----------------------
cat > "caddy/tenants/${TENANT}.caddy" <<CADDY
http://${TENANT}.crm.${PUBLIC_IP}.nip.io {
	reverse_proxy ${TENANT}-chatwoot-rails:3000
}

http://${TENANT}.tickets.${PUBLIC_IP}.nip.io {
	reverse_proxy ${TENANT}-zammad-nginx:8080
}

http://${TENANT}.agent.${PUBLIC_IP}.nip.io {
	reverse_proxy ${TENANT}-agent:8000
}

http://${TENANT}.mail.${PUBLIC_IP}.nip.io {
	basic_auth {
		${MAILPIT_AUTH_USER} ${MAILPIT_AUTH_HASH}
	}
	reverse_proxy mailpit:8025
}
CADDY
echo "==> Wrote caddy/tenants/${TENANT}.caddy; reloading Caddy"
docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec caddy \
  caddy reload --config /etc/caddy/Caddyfile

# --- 4. Bring up the tenant stack -------------------------------------------
compose_tenant() {
  docker compose -p "${TENANT}" -f "${TENANT_FILE}" --env-file "${ENV_FILE}" "$@"
}

echo "==> Starting ${TENANT} redis + memcached"
compose_tenant up -d redis memcached

echo "==> Preparing Chatwoot database for ${TENANT}"
compose_tenant run --rm chatwoot-rails bundle exec rails db:chatwoot_prepare

echo "==> Starting the full ${TENANT} stack"
compose_tenant up -d

cat <<EOF

==> Tenant '${TENANT}' is up. Give containers a minute, then visit:

  http://${TENANT}.crm.${PUBLIC_IP}.nip.io      (Chatwoot — onboarding wizard)
  http://${TENANT}.tickets.${PUBLIC_IP}.nip.io  (Zammad — setup wizard)
  http://${TENANT}.agent.${PUBLIC_IP}.nip.io    (agent /healthz)
  http://${TENANT}.mail.${PUBLIC_IP}.nip.io     (shared Mailpit, basic_auth)

Next: run each app's setup wizard, then fill the CHATWOOT_*/ZAMMAD_*/GEMINI_*
tokens in ${ENV_FILE} (README §5–6) and re-apply the agent:
  docker compose -p ${TENANT} -f ${TENANT_FILE} --env-file ${ENV_FILE} up -d agent
EOF
