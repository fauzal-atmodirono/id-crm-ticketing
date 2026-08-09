#!/usr/bin/env bash
# Run both suites twice: every feature flag off, then every flag forced ON.
#
# The flags-off run is the ship-dark guarantee. The flags-ON run is the one
# that actually finds bugs, because the on-path is the code nobody exercises
# until a tenant opts in. It has already caught one: ESCALATION_CC_DEALER=true
# against a dealer record whose shape predated cc_emails raised AttributeError
# and killed the entire dealer forward (fixed in 78a3cd9).
#
# Add every new default-off flag to FLAGS_ON below as you introduce it. A flag
# missing from this list is a flag whose on-path is untested.
#
# Usage: deploy/scripts/check-suites-both-flag-states.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AGENT="$ROOT/agent"
BACKEND="$ROOT/backend/apps/backend"

# google.genai.Client() demands a key at IMPORT time; without this, five
# backend modules fail to collect and it reads as a broken suite.
export GOOGLE_API_KEY="${GOOGLE_API_KEY:-test-key}"

FLAGS_ON=(
  # P1 — working-hours SLA
  SLA_WORKING_HOURS_ENABLED=true
  SLA_ACKNOWLEDGEMENT_ENABLED=true
  BUSINESS_HOURS_STAMP_ENABLED=true
  ESCALATION_REPLY_ACKNOWLEDGEMENT_ENABLED=true
  # P2 — omnichannel escalation
  ESCALATION_ALL_CHANNELS_ENABLED=true
  ESCALATION_CC_DEALER=true
  ESCALATION_FAILURE_NOTE_ENABLED=true
  ESCALATION_PRESENCE_CHECK_ENABLED=true
  ESCALATION_ATTACHMENT_BUDGET_BYTES=10485760
  BOUNCE_HANDLING_ENABLED=true
)

failed=0

run() {
  local label="$1"; shift
  echo
  echo "=== $label ==="
  ( cd "$AGENT" && env "$@" .venv/bin/python -m pytest -q 2>&1 | tail -3 ) || failed=1
  ( cd "$BACKEND" && env "$@" uv run pytest -q 2>&1 | tail -3 ) || failed=1
}

run "flags OFF (ship-dark guarantee)"
run "flags ON (the run that finds bugs)" "${FLAGS_ON[@]}"

echo
if [ "$failed" -ne 0 ]; then
  echo "FAILED — see output above."
  exit 1
fi
echo "Both flag states green."
