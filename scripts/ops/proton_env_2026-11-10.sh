#!/usr/bin/env bash
# Proton tenant env changes for the 10 Nov demo fixes (FIX-06 scope, FIX-11,
# FIX-28..FIX-39). Run ON THE VM from /opt/platform/deploy:
#     bash ../scripts/ops/proton_env_2026-11-10.sh [--agent] [--dry-run | --rollback]
#   (or wherever this file was copied; paths below are relative to the CWD)
#
# Touches tenants/proton.env ONLY. Backs it up to tenants/proton.env.bak-20261110
# (first run wins; re-runs never overwrite the backup). Each key is replaced in
# place if present, appended if not. Values are never printed: KEY=<set>.
#
# NPS survey: config.py has no boolean for it. The post-call / end-of-conversation
# survey (WhatsApp, email, and the voice phone stream in features/chat/router.py)
# is gated by NPS_SAMPLE_RATE (float 0.0-1.0; 0.0 = never asked). Set to 0.5 here;
# sampled surveys ask NPS INSTEAD OF CSAT, so 0.5 keeps CSAT flowing. Edit below.
#
# --rollback restores the FIRST-RUN backup (the original file), undoing both the
# backend and the --agent changes.
#
# --agent (run ONLY after the backend part): copies every ESCALATION_POLICY_*
# variable from the running proton-agent container (docker exec proton-agent
# printenv; names only ever printed) into proton.env, sets the agent lifecycle
# keys, and prints (does not run) `up -d --no-build agent`. Works with --dry-run.
#
# Default (backend) mode applies only backend keys; the agent keys exist only under --agent.
# Every key in BACKEND_KEYS is a backend Settings field
# (backend/apps/backend/src/chatbot/platform/config.py); the backend recreate at
# the end applies them. proton-agent is deliberately NOT recreated.
set -euo pipefail

ENV_FILE="${ENV_FILE:-tenants/proton.env}"
BACKUP="${ENV_BACKUP:-${ENV_FILE}.bak-20261110}"

BACKEND_KEYS=(
  "SLA_INBOX_IDS=3,5"
  "METRICS_PROVIDER=bigquery"
  "METRICS_SYNC_ENABLED=true"
  "BIGQUERY_DATASET=proton_crm"
  "SENTIMENT_CLASSIFIER_ENABLED=true"
  "SENTIMENT_TONE_ADJUSTMENT_ENABLED=true"
  "NPS_SAMPLE_RATE=0.5"
  "CSAT_BY_AGENT_ENABLED=true"
  "SLA_WORKING_HOURS_ENABLED=true"
  "PHONE_AFTER_HOURS_ENABLED=true"
  "PHONE_VOICEMAIL_INGEST_ENABLED=true"
  "ESCALATION_PRESENCE_CHECK_ENABLED=true"
  "RESOLVED_CASE_INDEX_ENABLED=true"
  "AUTO_SUMMARY_ON_RESOLVE_ENABLED=true"
  "TOKEN_METERING_ENABLED=true"
  "AI_COST_REPORTING_ENABLED=true"
  "ACW_ENABLED=true"
  "ANOMALY_HOURLY_ENABLED=true"
  "PRESENCE_THRESHOLD_ALERTS_ENABLED=true"
  "FOLLOW_UP_DATE_ENABLED=true"
  "ROUTING_FAIR_SHARE_ENABLED=true"
  "ROUTING_SWEEP_ENABLED=true"
)

BACKEND_RECREATE='docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --build backend'
AGENT_RECREATE='docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --no-build agent'

# Agent keys (all verified in agent/app/config.py Settings).
AGENT_KEYS=(
  "LIFECYCLE_IDLE_WARN_MINUTES=10"
  "LIFECYCLE_IDLE_CLOSE_GRACE_MINUTES=5"
  "LIFECYCLE_IDLE_CLOSE_OUT_OF_HOURS_GRACE_MINUTES=0"
  "LIFECYCLE_DISCLAIMER_ENABLED=true"
  "LIFECYCLE_SURVEY_ENABLED=true"
  "EMAIL_AUTOACK_ENABLED=true"
  "LIFECYCLE_AUTO_CATEGORIZE=true"
)

mode=apply; agent=0
for a in "$@"; do
  case "$a" in
    --agent) agent=1 ;;
    --dry-run|--rollback) mode="$a" ;;
    *) echo "usage: $0 [--agent] [--dry-run|--rollback]" >&2; exit 2 ;;
  esac
done

if [ "$mode" = "--rollback" ]; then
  [ -f "$BACKUP" ] || { echo "no backup at $BACKUP" >&2; exit 1; }
  cp -p "$BACKUP" "$ENV_FILE"
  echo "restored $ENV_FILE from $BACKUP (first-run backup)"
  echo "then run: $BACKEND_RECREATE"
  exit 0
fi

[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE (run from /opt/platform/deploy)" >&2; exit 1; }

if [ "$agent" = 1 ]; then
  KEYS=()
  # Read the container env first, so a missing container aborts before any write.
  if ! raw="$(docker exec proton-agent printenv 2>/dev/null)"; then
    echo "cannot read env from container proton-agent (not running?); nothing written" >&2
    exit 1
  fi
  # Values may contain '='; split at the first '=' only. Only NAMES are ever
  # printed. The value must survive a compose env-file round trip unchanged,
  # so anything compose would reinterpret aborts the whole run (key name only).
  bad=(); last=""
  while IFS= read -r line; do
    case "$line" in
      ESCALATION_POLICY_*=*)
        last="${line%%=*}"; v="${line#*=}"; KEYS+=("$line")
        case "$v" in
          *'$'*|*' #'*|*\\*|'"'*|"'"*|' '*|*' '|$'\t'*|*$'\t'|*$'\r') bad+=("$last") ;;
        esac ;;
      [A-Za-z_]*=*) last="" ;;
      *) [ -n "$last" ] && bad+=("$last") ;;  # continuation line = newline in value
    esac
  done <<< "$raw"
  if [ "${#KEYS[@]}" -eq 0 ]; then
    echo "no ESCALATION_POLICY_* found in proton-agent; aborting" >&2; exit 1
  fi
  if [ "${#bad[@]}" -gt 0 ]; then
    echo "ABORT: these keys have values that are unsafe to round-trip through an env file" >&2
    echo "(contain \$, ' #', backslash, leading quote, edge whitespace, or a newline)." >&2
    printf '  %s\n' $(printf '%s\n' "${bad[@]}" | sort -u) >&2
    echo "Copy those keys by hand (quote for compose), then re-run. Nothing was written." >&2
    exit 1
  fi
  KEYS+=("${AGENT_KEYS[@]}")
  RECREATE="$AGENT_RECREATE"
else
  KEYS=("${BACKEND_KEYS[@]}")
  RECREATE="$BACKEND_RECREATE"
fi

if [ "$mode" = "--dry-run" ]; then
  for kv in "${KEYS[@]}"; do
    k="${kv%%=*}"
    if grep -q "^${k}=" "$ENV_FILE"; then act=replace; else act=append; fi
    echo "[dry-run] $act ${k}=<set>"
  done
  echo "[dry-run] would then print: $RECREATE"
  exit 0
fi

[ -f "$BACKUP" ] || cp -p "$ENV_FILE" "$BACKUP"
echo "backup: $BACKUP"

tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
trap 'rm -f "$tmp" "$tmp.new"' EXIT
cp -p "$ENV_FILE" "$tmp"
# make sure the file ends in a newline before appending
[ -s "$tmp" ] && [ "$(tail -c1 "$tmp" | wc -l)" -eq 0 ] && echo >> "$tmp"

for kv in "${KEYS[@]}"; do
  k="${kv%%=*}"
  v="${kv#*=}"
  if grep -q "^${k}=" "$tmp"; then
    # awk with ENVIRON: no sed delimiter/escape issues; every line for the key
    # is rewritten, so a duplicated key collapses to the same value
    K="$k" V="$v" awk 'BEGIN{FS=OFS="="} $1==ENVIRON["K"]{print ENVIRON["K"] "=" ENVIRON["V"]; next} {print}' \
      "$tmp" > "$tmp.new" && mv "$tmp.new" "$tmp"
  else
    printf '%s=%s\n' "$k" "$v" >> "$tmp"
  fi
  echo "${k}=<set>"
done

cat "$tmp" > "$ENV_FILE"
echo
echo "done. Apply with (not run for you):"
echo "  $RECREATE"
