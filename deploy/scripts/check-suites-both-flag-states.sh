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
  # P3 — case record extensions
  CASE_FIELDS_ENABLED=true
  # P4 — reporting
  REOPEN_TRACKING_ENABLED=true
  # P5 — targets & report delivery
  CONTROL_ITEMS_ENABLED=true
  TARGETS_SEED_ENABLED=true
  # P6 — agent presence & workforce. ROUTING_ENABLED is NOT set here: it is a
  # Phase-5 switch P6 does not own, and the routing sweeper is gated on it as
  # well as on ROUTING_SWEEP_ENABLED, so its on-path is covered instead by
  # features/routing/test_p6_wiring.py, which sets both.
  PRESENCE_TRACKING_ENABLED=true
  PRESENCE_CUSTOM_STATUSES_ENABLED=true
  PRESENCE_THRESHOLD_ALERTS_ENABLED=true
  ACW_ENABLED=true
  ROUTING_FAIR_SHARE_ENABLED=true
  ROUTING_SWEEP_ENABLED=true
  FOLLOW_UP_DATE_ENABLED=true
  # P7 — AI conversational quality. TRANSLATION_OUTBOUND_TAMIL_ENABLED is
  # DELIBERATELY NOT in this list: the plan requires the full suite green
  # with all flags off, then with all on EXCEPT outbound Tamil, because that
  # flag ships disabled pending a signed-off Tamil evaluation. Do not add it
  # here to "complete" the set -- that would defeat the one exception this
  # script exists to honour.
  SENTIMENT_CLASSIFIER_ENABLED=true
  SENTIMENT_TONE_ADJUSTMENT_ENABLED=true
  TRANSLATION_ENABLED=true
  FAQ_KEYWORD_WEIGHT=0.5
  FAQ_SUGGESTION_POPUP_ENABLED=true
  MEDIA_DIAGNOSIS_PROMPT_ENABLED=true
  RESOLVED_CASE_INDEX_ENABLED=true
  AUTO_SUMMARY_ON_RESOLVE_ENABLED=true
  # P8 — AI & agent measurement. NPS_SAMPLE_RATE=1.0 and
  # CSAT_RANKING_MIN_SAMPLES=25 are concrete non-default values rather than
  # booleans, chosen the same way FAQ_KEYWORD_WEIGHT=0.5 above was: 1.0
  # deterministically forces the NPS question on every sampled survey, and 25
  # is not the default 10, so a test that reads the ranking floor from
  # settings and then asserts a hardcoded 10 fails here instead of passing
  # forever. `src/chatbot/test_p8_flags.py` asserts all six lines are present,
  # so this block stops being maintained from memory.
  TOKEN_METERING_ENABLED=true
  AI_COST_REPORTING_ENABLED=true
  NPS_SAMPLE_RATE=1.0
  CSAT_BY_AGENT_ENABLED=true
  CSAT_RANKING_MIN_SAMPLES=25
  CALL_QA_ENABLED=true
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
