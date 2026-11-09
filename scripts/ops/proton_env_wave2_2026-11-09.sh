#!/usr/bin/env bash
# Proton Wave 2 env keys (9 Nov 2026, user asked to deploy Wave 2 and switch on
# every Wave 2 flag except password expiry). Run ON THE VM from
# /opt/platform/deploy:
#     sudo bash /tmp/proton_env_wave2_2026-11-09.sh [--dry-run]
#
# Touches tenants/proton.env only. Backup: tenants/proton.env.bak-wave2-20261109
# (first run wins). Inode-preserving write (cat tmp > file). Values are never
# printed: KEY=<set>. CALL_ABANDON_DATABASE_URL is copied from RSA_DATABASE_URL
# (same per-tenant Postgres, its own table) without printing it.
#
# Not set here on purpose: PASSWORD_MAX_AGE_DAYS (user: off until after the
# demo); MFA keys (scripts/ops/proton_mfa_keys.sh owns them).
set -euo pipefail

ENV_FILE="${ENV_FILE:-tenants/proton.env}"
BACKUP="${ENV_FILE}.bak-wave2-20261109"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

KEYS=(
  "CHATWOOT_IMAGE=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom-rc17"
  "WEEKLY_REPORT_EMAIL_ENABLED=true"
  "WEEKLY_REPORT_EMAIL_RECIPIENTS=devotech29@gmail.com"
  "CALL_ABANDON_ENABLED=true"
  "SESSION_IDLE_TIMEOUT_MINUTES=15"
  "SESSION_TOKEN_LIFESPAN_HOURS=12"
  "PHONE_HOLD_TRANSFER_ENABLED=true"
  "DATA_SCOPED_RBAC_ENABLED=true"
  "PII_MASKING_ENABLED=true"
  "PHONE_DTMF_MENU_ENABLED=true"
  "DUPLICATE_DETECTION_ENABLED=true"
  "PROTON_BACKEND_TIMEOUT_SECONDS=20"
)

[ -f "$ENV_FILE" ] || { echo "no $ENV_FILE (run from /opt/platform/deploy)"; exit 1; }
rsa_url="$(grep -E '^RSA_DATABASE_URL=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
[ -n "$rsa_url" ] || { echo "RSA_DATABASE_URL unset: cannot derive CALL_ABANDON_DATABASE_URL"; exit 1; }
KEYS+=("CALL_ABANDON_DATABASE_URL=$rsa_url")

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
cp "$ENV_FILE" "$tmp"
for kv in "${KEYS[@]}"; do
  k="${kv%%=*}"; v="${kv#*=}"
  if grep -qE "^${k}=" "$tmp"; then
    action=replace
    awk -v k="$k" -v v="$v" 'BEGIN{FS=OFS="="} $1==k{print k "=" v; next} {print}' "$tmp" > "$tmp.n" && mv "$tmp.n" "$tmp"
  else
    action=append
    printf '%s=%s\n' "$k" "$v" >> "$tmp"
  fi
  echo "${action} ${k}=<set>"
done

if [ "$DRY" = 1 ]; then echo "dry-run: $ENV_FILE not changed"; exit 0; fi
[ -f "$BACKUP" ] || cp -p "$ENV_FILE" "$BACKUP"
cat "$tmp" > "$ENV_FILE"
echo "ok: wrote ${#KEYS[@]} keys; backup $BACKUP"
