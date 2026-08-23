#!/usr/bin/env bash
# Provision one tenant end to end: generate secrets, create its Postgres
# roles/databases on the shared server, render its Caddy route, and bring its
# app stack up. Run from the VM after shared infra (docker-compose.infra.yml)
# is up.
#
# Usage:
#   deploy/scripts/add-tenant.sh <tenant-name> [--bare]
#
# --bare (also implied when <tenant-name> is "default") serves the tenant at the
# un-prefixed hostnames crm/tickets/agent/mail.<ip>.nip.io instead of
# <tenant>.crm.<ip>.nip.io. Use it for at most ONE tenant.
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
INFRA_PROJECT="platform-infra"
INFRA_FILE="docker-compose.infra.yml"
TENANT_FILE="docker-compose.tenant.yml"

TENANT="${1:-}"
MODE="${2:-}"
if [[ ! "${TENANT}" =~ ^[a-z][a-z0-9]*$ ]]; then
  echo "ERROR: tenant name must match ^[a-z][a-z0-9]*$ (got '${TENANT}')" >&2
  exit 1
fi
if [[ -n "${MODE}" && "${MODE}" != "--bare" ]]; then
  echo "ERROR: unknown option '${MODE}' (only --bare is supported)" >&2
  exit 1
fi

# Public-hostname prefix: bare (no "<tenant>." label) for a --bare tenant or the
# conventional "default" tenant; "<tenant>." otherwise. Container aliases stay
# ${TENANT}-prefixed regardless — this only affects the public Caddy vhosts and
# the FRONTEND_URL / *_PUBLIC_URL values.
if [[ "${MODE}" == "--bare" || "${TENANT}" == "default" ]]; then
  HOST_PREFIX=""
else
  HOST_PREFIX="${TENANT}."
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
# Read infra.env values literally — do NOT `source` it: the Mailpit bcrypt
# hash is stored unquoted (correct for Compose's dotenv parser), and shell
# `source` would parameter-expand its `$2a$14$...` prefix and corrupt it.
get_infra_var() {
  grep -E "^$1=" infra.env | head -n1 | cut -d= -f2-
}
PUBLIC_IP="$(get_infra_var PUBLIC_IP)"
MAILPIT_AUTH_USER="$(get_infra_var MAILPIT_AUTH_USER)"
MAILPIT_AUTH_HASH="$(get_infra_var MAILPIT_AUTH_HASH)"
: "${PUBLIC_IP:?PUBLIC_IP must be set in infra.env}"
: "${MAILPIT_AUTH_USER:?MAILPIT_AUTH_USER must be set in infra.env}"
: "${MAILPIT_AUTH_HASH:?MAILPIT_AUTH_HASH must be set in infra.env}"

echo "==> Provisioning tenant '${TENANT}' (PUBLIC_IP=${PUBLIC_IP})"

# --- Firestore -------------------------------------------------------------
# Every tenant gets its OWN Firestore database; it is where the custom-feature
# switchboard, the term dictionary and the escalation stores live.
#
# LEAVING THIS UNSET IS NOT A NO-OP, IT IS A BROKEN TENANT. example.env ships
# FIRESTORE_PROJECT_ID/FIRESTORE_DATABASE_ID blank, and with them blank every
# store read raises CustomFeatureStoreUnavailable -> the switchboard 503s ->
# the SPA fails closed -> the operator opens a CRM with no features and no
# page to switch any on. The bahana tenant came up exactly that way.
#
# FIRESTORE_LOCATION IS IMMUTABLE once the database exists. Changing it later
# means creating a SECOND database and migrating, not editing a field — so it
# is an explicit knob, deliberately NOT inherited from whatever region another
# tenant happens to use. Set it to the customer's data-residency region:
# bahana is Jakarta (asia-southeast2); proton and aeon360 are asia-southeast1.
# The default below is the VM's own region, which is the right answer when
# there is no residency requirement pulling the other way.
FIRESTORE_PROJECT_ID_VALUE="${FIRESTORE_PROJECT_ID_VALUE:-$(gcloud config get-value project 2>/dev/null)}"
FIRESTORE_LOCATION="${FIRESTORE_LOCATION:-asia-southeast2}"
FIRESTORE_DB="${TENANT}-db"

# The Chatwoot image a NEW tenant boots. Bump this when a newer custom image
# ships; it is the one line that decides whether a tenant gets this platform
# or stock Chatwoot.
#
# Why it is pinned here rather than left to the compose default:
# docker-compose.tenant.yml says `${CHATWOOT_IMAGE:-chatwoot/chatwoot:v4.15.1}`
# and example.env ships CHATWOOT_IMAGE blank. `:-` fires on empty as well as
# unset, so a tenant provisioned from the template silently comes up on
# UPSTREAM Chatwoot: Captain and the SAML settings that patches 0029/0032
# remove are back, and none of the Knowledge / RBAC / SLA / Audit Log pages
# this platform exists to provide are present. It looks like a working CRM,
# which is exactly why it went unnoticed on the bahana tenant.
CHATWOOT_IMAGE_PIN="${CHATWOOT_IMAGE_PIN:-asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom-rc12}"

# --- 1. Generate per-tenant secrets + env file ------------------------------
# New tenants are industry-neutral. The backend's TERM_PROFILE default is
# `automotive` for backwards compatibility with tenants that predate the term
# dictionary, so provisioning must say `generic` explicitly or a non-
# automotive customer opens their CRM reading "Dealer" and "Vehicle". The
# template ships that line commented out (`# TERM_PROFILE=generic`) so an
# unprovisioned example.env stays inert; this sed uncomments it, the same way
# every other per-tenant value below is substituted into the template.
CHATWOOT_DB_PASSWORD="$(openssl rand -hex 16)"
AGENT_DB_PASSWORD="$(openssl rand -hex 16)"
BACKEND_DB_PASSWORD="$(openssl rand -hex 16)"
REDIS_PASSWORD="$(openssl rand -hex 16)"
SECRET_KEY_BASE="$(openssl rand -hex 64)"

sed \
  -e "s/^TENANT=.*/TENANT=${TENANT}/" \
  -e "s/^PUBLIC_IP=.*/PUBLIC_IP=${PUBLIC_IP}/" \
  -e "s/^CHATWOOT_DB_PASSWORD=.*/CHATWOOT_DB_PASSWORD=${CHATWOOT_DB_PASSWORD}/" \
  -e "s/^AGENT_DB_PASSWORD=.*/AGENT_DB_PASSWORD=${AGENT_DB_PASSWORD}/" \
  -e "s/^BACKEND_DB_PASSWORD=.*/BACKEND_DB_PASSWORD=${BACKEND_DB_PASSWORD}/" \
  -e "s|^KNOWLEDGE_DATABASE_URL=.*|KNOWLEDGE_DATABASE_URL=postgresql://backend_${TENANT}:${BACKEND_DB_PASSWORD}@postgres:5432/backend_${TENANT}|" \
  -e "s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=${REDIS_PASSWORD}/" \
  -e "s/^SECRET_KEY_BASE=.*/SECRET_KEY_BASE=${SECRET_KEY_BASE}/" \
  -e "s|^HOST_PREFIX=.*|HOST_PREFIX=${HOST_PREFIX}|" \
  -e "s/^# TERM_PROFILE=generic$/TERM_PROFILE=generic/" \
  -e "s|^CHATWOOT_IMAGE=.*|CHATWOOT_IMAGE=${CHATWOOT_IMAGE_PIN}|" \
  -e "s|^FIRESTORE_PROJECT_ID=.*|FIRESTORE_PROJECT_ID=${FIRESTORE_PROJECT_ID_VALUE}|" \
  -e "s|^FIRESTORE_DATABASE_ID=.*|FIRESTORE_DATABASE_ID=${FIRESTORE_DB}|" \
  tenants/example.env > "${ENV_FILE}"
# Create the tenant's Firestore database if it does not exist. Idempotent:
# `describe` succeeding means someone already made it, and we must NOT try to
# recreate it — nor silently accept it if its location differs from what this
# run intends, because that location cannot be changed afterwards.
if gcloud firestore databases describe --database="${FIRESTORE_DB}" \
     --project="${FIRESTORE_PROJECT_ID_VALUE}" >/dev/null 2>&1; then
  EXISTING_LOC=$(gcloud firestore databases describe --database="${FIRESTORE_DB}" \
    --project="${FIRESTORE_PROJECT_ID_VALUE}" --format="value(locationId)" 2>/dev/null)
  if [[ "${EXISTING_LOC}" != "${FIRESTORE_LOCATION}" ]]; then
    echo "ERROR: ${FIRESTORE_DB} already exists in ${EXISTING_LOC}, but this run" >&2
    echo "       intends ${FIRESTORE_LOCATION}. A Firestore location is immutable." >&2
    echo "       Either re-run with FIRESTORE_LOCATION=${EXISTING_LOC}, or pick a" >&2
    echo "       different database name and migrate deliberately." >&2
    exit 1
  fi
  echo "==> Firestore ${FIRESTORE_DB} already exists in ${EXISTING_LOC}"
else
  echo "==> Creating Firestore ${FIRESTORE_DB} in ${FIRESTORE_LOCATION} (IMMUTABLE)"
  gcloud firestore databases create --database="${FIRESTORE_DB}" \
    --location="${FIRESTORE_LOCATION}" --type=firestore-native \
    --project="${FIRESTORE_PROJECT_ID_VALUE}"
fi

echo "==> Wrote ${ENV_FILE} (hostnames: ${HOST_PREFIX:-<bare>}crm.${PUBLIC_IP}.nip.io)"

# --- 2. Create Postgres roles + databases on the running server -------------
echo "==> Creating databases chatwoot_${TENANT} / agent_${TENANT} / backend_${TENANT}"
docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<SQL
CREATE ROLE chatwoot_${TENANT}  LOGIN PASSWORD '${CHATWOOT_DB_PASSWORD}';
CREATE ROLE agent_${TENANT}     LOGIN PASSWORD '${AGENT_DB_PASSWORD}';
CREATE ROLE backend_${TENANT}   LOGIN PASSWORD '${BACKEND_DB_PASSWORD}';
CREATE DATABASE chatwoot_${TENANT} OWNER chatwoot_${TENANT};
CREATE DATABASE agent_${TENANT}    OWNER agent_${TENANT};
CREATE DATABASE backend_${TENANT}  OWNER backend_${TENANT};
SQL

# Chatwoot needs superuser-only extensions in its DB. It connects as the
# non-superuser chatwoot_<tenant> role, so it cannot create these itself and
# its `db:chatwoot_prepare` aborts unless they already exist: pgvector for
# embeddings, and pg_stat_statements referenced by its schema. (Trusted
# extensions like pg_trgm/pgcrypto the app can still create on its own.)
docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U postgres -d "chatwoot_${TENANT}" \
  -c 'CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_stat_statements;'

# backend knowledge DB needs the vector extension enabled by a superuser (the
# backend_<tenant> role is non-superuser and cannot CREATE EXTENSION vector itself).
docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U postgres -d "backend_${TENANT}" \
  -c 'CREATE EXTENSION IF NOT EXISTS vector;'

# --- 3. Render + install the Caddy route, then reload -----------------------
cat > "caddy/tenants/${TENANT}.caddy" <<CADDY
http://${HOST_PREFIX}crm.${PUBLIC_IP}.nip.io {
	# Chatwoot is a Rack app, so it needs the underscore-twin strip. See the
	# snippet's own comment in caddy/Caddyfile for why the proxy allows
	# underscore headers through at all.
	import strip_underscore_forwarding
	# Proton AI backend paths proxied same-origin so the Chatwoot SPA reaches the
	# backend without CORS or a browser-unreachable internal host.
	#
	# EVERY backend prefix the fork calls must be listed here. A missing prefix
	# does not fail loudly: the request falls through to the Chatwoot handler
	# below, which answers with its own HTML 404 page, so the SPA reports
	# "404: <!DOCTYPE html>..." and the feature looks broken in the frontend
	# while the backend route is mounted and healthy. That is exactly what
	# happened to /alerts/* on 2026-08-11 -- patch 0057's preferences page was
	# unreachable for that reason alone. Rule of thumb: if you add a router in
	# main.py that the fork calls, add its prefix here in the same change.
	@proton_backend path /metrics/* /kb/* /assist/* /routing/* /authz/* /admin/* /rsa/* /voice/* /alerts/* /calls/*
	reverse_proxy @proton_backend ${TENANT}-backend:8080
	reverse_proxy ${TENANT}-chatwoot-rails:3000
}

http://${HOST_PREFIX}agent.${PUBLIC_IP}.nip.io {
	reverse_proxy ${TENANT}-agent:8000
}

http://${HOST_PREFIX}mail.${PUBLIC_IP}.nip.io {
	basic_auth {
		${MAILPIT_AUTH_USER} ${MAILPIT_AUTH_HASH}
	}
	reverse_proxy mailpit:8025
}
CADDY
echo "==> Wrote caddy/tenants/${TENANT}.caddy; reloading Caddy"
docker compose -p "${INFRA_PROJECT}" -f "${INFRA_FILE}" exec -T caddy \
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

  http://${HOST_PREFIX}crm.${PUBLIC_IP}.nip.io      (Chatwoot — onboarding wizard)
  http://${HOST_PREFIX}agent.${PUBLIC_IP}.nip.io    (agent /healthz)
  http://${HOST_PREFIX}mail.${PUBLIC_IP}.nip.io     (shared Mailpit, basic_auth)

Next: run each app's setup wizard, then fill the CHATWOOT_*/GEMINI_*
tokens in ${ENV_FILE} (README §5–6) and re-apply the agent:
  docker compose -p ${TENANT} -f ${TENANT_FILE} --env-file ${ENV_FILE} up -d agent
EOF
