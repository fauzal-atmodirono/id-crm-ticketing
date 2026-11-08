#!/usr/bin/env bash
# Proton tenant env changes for the 10 Nov demo fixes (FIX-06 scope, FIX-11,
# FIX-28..FIX-39). Run ON THE VM from /opt/platform/deploy:
#     bash ../scripts/ops/proton_env_2026-11-10.sh [--dry-run | --rollback]
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
# Agent-only keys: none. Every key below is a backend Settings field
# (backend/apps/backend/src/chatbot/platform/config.py); the backend recreate at
# the end applies them. proton-agent is deliberately NOT recreated.
set -euo pipefail

ENV_FILE="${ENV_FILE:-tenants/proton.env}"
BACKUP="${ENV_BACKUP:-${ENV_FILE}.bak-20261110}"

KEYS=(
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

RECREATE='docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --build backend'

mode="${1:-apply}"
case "$mode" in
  apply|--dry-run|--rollback) ;;
  *) echo "usage: $0 [--dry-run|--rollback]" >&2; exit 2 ;;
esac

if [ "$mode" = "--rollback" ]; then
  [ -f "$BACKUP" ] || { echo "no backup at $BACKUP" >&2; exit 1; }
  cp -p "$BACKUP" "$ENV_FILE"
  echo "restored $ENV_FILE from $BACKUP"
  echo "then run: $RECREATE"
  exit 0
fi

[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE (run from /opt/platform/deploy)" >&2; exit 1; }

if [ "$mode" = "--dry-run" ]; then
  for kv in "${KEYS[@]}"; do
    k="${kv%%=*}"
    if grep -q "^${k}=" "$ENV_FILE"; then act=replace; else act=append; fi
    echo "[dry-run] $act ${k}=<set>"
  done
  exit 0
fi

[ -f "$BACKUP" ] || cp -p "$ENV_FILE" "$BACKUP"
echo "backup: $BACKUP"

tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
cp -p "$ENV_FILE" "$tmp"
# make sure the file ends in a newline before appending
[ -s "$tmp" ] && [ "$(tail -c1 "$tmp" | wc -l)" -eq 0 ] && echo >> "$tmp"

for kv in "${KEYS[@]}"; do
  k="${kv%%=*}"
  v="${kv#*=}"
  if grep -q "^${k}=" "$tmp"; then
    # awk with ENVIRON: no sed delimiter/escape issues, last-write-wins on dupes
    K="$k" V="$v" awk 'BEGIN{FS=OFS="="} $1==ENVIRON["K"]{print ENVIRON["K"] "=" ENVIRON["V"]; next} {print}' \
      "$tmp" > "$tmp.new" && mv "$tmp.new" "$tmp"
  else
    printf '%s=%s\n' "$k" "$v" >> "$tmp"
  fi
  echo "${k}=<set>"
done

cat "$tmp" > "$ENV_FILE"
echo
echo "done. Apply to the backend only (never recreate proton-agent):"
echo "  $RECREATE"
