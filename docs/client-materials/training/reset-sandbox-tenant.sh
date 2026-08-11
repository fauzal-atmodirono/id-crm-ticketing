#!/usr/bin/env bash
# Reset the TRAINING SANDBOX tenant between cohorts.
#
# ============================================================================
# NEVER EXECUTED. This script has not been run against any tenant. It was
# written in an environment with no live Chatwoot, no Postgres and no sandbox
# tenant, so nothing below has been observed working — see
# delivery-plan.md §7. Run it once with RESET_DRY_RUN=1 before trusting it
# with a cohort's tenant.
# ============================================================================
#
# What it does: purges the previous cohort's seeded demo batch and seeds a
# fresh one, by wrapping deploy/scripts/seed_demo_data — which has its own
# dry-run summary and typed confirmation — rather than issuing SQL of its own.
# A training reset should not be the one tool in the repo that deletes rows by
# hand.
#
# What it does NOT do, and it matters for the administrator cohort: the seeder
# owns conversations, contacts and RSA incidents, not configuration. Labels,
# SLA policies, escalation routing, custom attributes, roles and inbox
# settings a cohort changed during sessions 5–10 stay changed. To get back to
# a clean configuration, re-provision the tenant instead:
#   deploy/scripts/remove-tenant.sh <tenant> && deploy/scripts/add-tenant.sh <tenant>
# See delivery-plan.md §4.
#
# Usage (from anywhere in the repo):
#   TRAINING_TENANT=sandbox \
#   CHATWOOT_URL=https://sandbox.crm.example \
#   CHATWOOT_API_TOKEN=... CHATWOOT_ACCOUNT_ID=1 \
#   TRAINING_INBOX_ID=42 \
#   PROTON_BACKEND_URL=... PROTON_BACKEND_KEY=... \
#     docs/client-materials/training/reset-sandbox-tenant.sh
#
#   RESET_DRY_RUN=1  — pass --dry-run to both seeder commands, write nothing
#   RESET_COUNT=n    — demo contacts to seed (default 40, a cohort-sized set)
#   RESET_YES=1      — skip this script's own confirmation (the seeder still
#                      asks for its own unless it is in --dry-run)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SEEDER_DIR="${REPO_ROOT}/deploy/scripts"
STATE_DIR="${TRAINING_STATE_DIR:-${HOME}/.proton-training}"
COUNT="${RESET_COUNT:-40}"

# --- 1. Refuse to touch anything that might not be a sandbox ---------------
# The whole point of a sandbox is that an exercise cannot reach a real
# customer. A reset script that can be pointed at a production tenant by a
# one-character typo defeats that, so the allowed names are an allow-list and
# the known-real ones are named explicitly as well.
TENANT="${TRAINING_TENANT:-}"
if [[ -z "${TENANT}" ]]; then
  echo "ERROR: set TRAINING_TENANT (no default — this script never guesses a tenant)." >&2
  exit 1
fi
if [[ ! "${TENANT}" =~ ^(sandbox|training|sandbox[0-9]+|training[0-9]+)$ ]]; then
  echo "ERROR: '${TENANT}' is not an allowed training tenant name." >&2
  echo "       Allowed: sandbox, training, sandbox<N>, training<N>." >&2
  echo "       This is deliberate: exercises must not be able to reach a real customer." >&2
  exit 1
fi
# (No separate deny-list for `default`/`proton`/`wahchan`: the allow-list above
# already refuses every name that is not a training tenant, and a second check
# that can never fire would read like the one doing the work.)

for var in CHATWOOT_URL CHATWOOT_API_TOKEN CHATWOOT_ACCOUNT_ID TRAINING_INBOX_ID; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: ${var} is not set. See the usage block at the top of this script." >&2
    exit 1
  fi
done

DRY_ARGS=()
[[ -n "${RESET_DRY_RUN:-}" ]] && DRY_ARGS+=(--dry-run)

COMMON_ARGS=(
  --tenant "${TENANT}"
  --chatwoot-url "${CHATWOOT_URL}"
  --chatwoot-token "${CHATWOOT_API_TOKEN}"
  --account-id "${CHATWOOT_ACCOUNT_ID}"
)
# The backend leg is optional: without it the seeder cannot create RSA
# incidents, so the RSA exercises have nothing to open. Warn rather than fail,
# because the conversation exercises are still resettable without it.
if [[ -n "${PROTON_BACKEND_URL:-}" && -n "${PROTON_BACKEND_KEY:-}" ]]; then
  COMMON_ARGS+=(--backend-url "${PROTON_BACKEND_URL}" --backend-key "${PROTON_BACKEND_KEY}")
else
  echo "WARNING: PROTON_BACKEND_URL/PROTON_BACKEND_KEY unset — no RSA incidents will be"
  echo "         seeded, so the 'Logging an RSA incident' exercise in the supervisor and"
  echo "         administrator sets will have an empty incident table to work from."
fi

mkdir -p "${STATE_DIR}"
BATCH_FILE="${STATE_DIR}/${TENANT}.last-batch-id"
NEW_BATCH="training-$(date -u +%Y%m%dT%H%M%SZ)"

echo "Tenant        : ${TENANT}"
echo "Chatwoot      : ${CHATWOOT_URL} (account ${CHATWOOT_ACCOUNT_ID}, inbox ${TRAINING_INBOX_ID})"
echo "Previous batch: $(cat "${BATCH_FILE}" 2>/dev/null || echo '(none recorded)')"
echo "New batch     : ${NEW_BATCH}"
echo "Demo contacts : ${COUNT}"
[[ -n "${RESET_DRY_RUN:-}" ]] && echo "Mode          : DRY RUN — nothing will be written"

if [[ -z "${RESET_YES:-}" && -z "${RESET_DRY_RUN:-}" ]]; then
  read -r -p "Type the tenant name to confirm the reset: " confirm
  if [[ "${confirm}" != "${TENANT}" ]]; then
    echo "Aborted." >&2
    exit 1
  fi
fi

cd "${SEEDER_DIR}"

# --- 2. Purge the previous cohort's batch ---------------------------------
# Only ever the batch id this script recorded. `purge` cannot target anything
# it is not told, which is why the id is written to a state file rather than
# rediscovered: losing it means the batch has to be cleaned up by hand, and
# guessing at "everything that looks like demo data" is how a reset script
# deletes a case somebody cared about.
if [[ -s "${BATCH_FILE}" ]]; then
  PREVIOUS="$(cat "${BATCH_FILE}")"
  echo "==> Purging batch ${PREVIOUS}"
  python3 -m seed_demo_data purge "${COMMON_ARGS[@]}" --batch "${PREVIOUS}" "${DRY_ARGS[@]}"
else
  echo "==> No previous batch recorded; nothing to purge."
  echo "    (First run, or the state file was lost. If a previous cohort's data is"
  echo "     still on the tenant, purge it with its own batch id before seeding.)"
fi

# --- 3. Seed the next cohort's data ---------------------------------------
echo "==> Seeding batch ${NEW_BATCH} (${COUNT} contacts)"
python3 -m seed_demo_data seed \
  "${COMMON_ARGS[@]}" \
  --inbox-id "${TRAINING_INBOX_ID}" \
  --count "${COUNT}" \
  --batch-id "${NEW_BATCH}" \
  --manifest-dir "${STATE_DIR}" \
  "${DRY_ARGS[@]}"

if [[ -z "${RESET_DRY_RUN:-}" ]]; then
  echo "${NEW_BATCH}" > "${BATCH_FILE}"
  echo "==> Recorded batch id in ${BATCH_FILE}"
fi

echo "==> Reset complete."
echo "    Configuration changed by the previous cohort is NOT reverted — see the"
echo "    header of this script and delivery-plan.md §4."
