#!/usr/bin/env bash
# Launch the local /assist + /assist/copilot test server wired to the LOCAL
# Chatwoot so Ask Copilot answers are grounded in real conversation context.
#
# It overrides the checked-in .env (which points at the remote proton VM) with
# env vars for THIS process only — non-destructive. The Chatwoot API token is
# read live from the running Chatwoot container, so it is always current.
#
# Prereqs: the local Chatwoot stack is up (container `default-chatwoot-rails`)
#          and this repo's .venv exists.
# Usage:   ./run_copilot_local.sh
# Serves:  http://localhost:8000  (health: /healthz)
set -euo pipefail
cd "$(dirname "$0")"

TOKEN=$(docker exec default-chatwoot-rails bundle exec rails runner \
  'print User.first.access_token.token' 2>/dev/null | tail -1)
if [ -z "${TOKEN:-}" ]; then
  echo "Could not read a Chatwoot access token from default-chatwoot-rails." >&2
  echo "Is the local Chatwoot stack running?" >&2
  exit 1
fi

# Non-destructive overrides (env beats .env in pydantic-settings). Each is
# overridable from the caller's environment if you need a different target.
export PROTON_BACKEND_KEY="${PROTON_BACKEND_KEY:-local-test-key}"
export CHATWOOT_API_URL="${CHATWOOT_API_URL:-http://crm.127-0-0-1.nip.io}"
export CHATWOOT_API_TOKEN="$TOKEN"
export CHATWOOT_ACCOUNT_ID="${CHATWOOT_ACCOUNT_ID:-1}"
export CHATWOOT_ENABLED=true
# CORS origins allowed to call /assist/* and /kb/* — the Chatwoot page (Suggest/
# Copilot) and the agent host that serves the Knowledge Manager dashboard app.
export ASSIST_LOCAL_ORIGINS="${ASSIST_LOCAL_ORIGINS:-http://crm.localhost,http://agent.localhost,http://crm.127-0-0-1.nip.io,http://agent.127-0-0-1.nip.io,http://localhost:3000}"

echo "Serving /assist + /assist/copilot on :8000 (grounded via ${CHATWOOT_API_URL})"
echo "PROTON_BACKEND_KEY=${PROTON_BACKEND_KEY}  (set PROTON_BACKEND_URL/KEY in the tenant to match)"
exec .venv/bin/python run_assist_local.py
