#!/usr/bin/env bash
# Proton MFA enablement keys (FIX-25, RFP 2026_028 7.3). Run ON THE VM from
# /opt/platform/deploy:
#     bash ../scripts/ops/proton_mfa_keys.sh [--dry-run | --rollback]
#
# Chatwoot shows the profile-page "Two-factor authentication" card only when the
# three ActiveRecord encryption env vars are present. This script GENERATES them
# inside the running chatwoot-rails container (`rails db:encryption:init`) and
# writes them to tenants/proton.env. Values are NEVER printed: KEY=<set>.
#
# Safety:
#  - Touches tenants/proton.env ONLY. Backup tenants/proton.env.bak-mfa-20261110
#    (first run wins; re-runs never overwrite it).
#  - Inode-preserving write (cat tmp > file): safe for single-file bind mounts.
#  - If ANY of the three keys already has a value, the script refuses and
#    changes nothing: rotating them would orphan OTP secrets and encrypted
#    channel credentials. Never re-run to "refresh" keys.
#  - Generation happens before any write, so a down container aborts cleanly.
#  - --rollback restores the first-run backup. Do NOT roll back after any user
#    has enrolled MFA or an inbox credential was saved with the keys set.
#
# KEEP A COPY of the three values in the team secret store (the env file backup
# is not enough if the VM is lost). Losing them locks enrolled users out.
#
# Env overrides: ENV_FILE, ENV_BACKUP, CHATWOOT_CONTAINER (default
# proton-chatwoot-rails).
set -euo pipefail

ENV_FILE="${ENV_FILE:-tenants/proton.env}"
BACKUP="${ENV_BACKUP:-${ENV_FILE}.bak-mfa-20261110}"
CONTAINER="${CHATWOOT_CONTAINER:-proton-chatwoot-rails}"
NAMES=(ACTIVE_RECORD_ENCRYPTION_PRIMARY_KEY ACTIVE_RECORD_ENCRYPTION_DETERMINISTIC_KEY ACTIVE_RECORD_ENCRYPTION_KEY_DERIVATION_SALT)
RECREATE='docker compose -p proton -f docker-compose.tenant.yml --env-file tenants/proton.env up -d --force-recreate chatwoot-rails chatwoot-sidekiq'

mode=apply
for a in "$@"; do
  case "$a" in
    --dry-run|--rollback) mode="$a" ;;
    *) echo "usage: $0 [--dry-run|--rollback]" >&2; exit 2 ;;
  esac
done

if [ "$mode" = "--rollback" ]; then
  [ -f "$BACKUP" ] || { echo "no backup at $BACKUP" >&2; exit 1; }
  cp -p "$BACKUP" "$ENV_FILE"
  echo "restored $ENV_FILE from $BACKUP (first-run backup)"
  echo "then run: $RECREATE"
  exit 0
fi

[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE (run from /opt/platform/deploy)" >&2; exit 1; }

# Refuse if any key already has a non-empty value (never rotate).
for k in "${NAMES[@]}"; do
  if grep -Eq "^${k}=.+" "$ENV_FILE"; then
    echo "refusing: ${k} is already set in $ENV_FILE. Keys are never rotated; nothing changed." >&2
    exit 1
  fi
done

if [ "$mode" = "--dry-run" ]; then
  for k in "${NAMES[@]}"; do
    if grep -q "^${k}=" "$ENV_FILE"; then act=replace; else act=append; fi
    echo "[dry-run] $act ${k}=<set>"
  done
  echo "[dry-run] would generate inside container $CONTAINER, then print: $RECREATE"
  exit 0
fi

# Generate first (stderr dropped: it could echo output). Output stays in a
# shell variable; only the key NAMES are ever printed.
if ! raw="$(docker exec "$CONTAINER" bundle exec rails db:encryption:init 2>/dev/null)"; then
  echo "could not run rails db:encryption:init in $CONTAINER (not running?); nothing written" >&2
  exit 1
fi

get() { # $1 = yaml key; prints value only into the caller's variable
  printf '%s\n' "$raw" | awk -v key="$1" '$1 == key ":" { print $2; exit }'
}
vals=()
for y in primary_key deterministic_key key_derivation_salt; do
  v="$(get "$y")"
  # Rails emits plain alphanumeric strings; anything else means the output
  # format changed, so abort instead of writing something compose could mangle.
  if [ -z "$v" ] || ! printf '%s' "$v" | grep -Eq '^[A-Za-z0-9_+/=-]+$'; then
    echo "unexpected output from db:encryption:init (field ${y}); nothing written" >&2
    unset raw v vals
    exit 1
  fi
  vals+=("$v")
done
unset raw

[ -f "$BACKUP" ] || cp -p "$ENV_FILE" "$BACKUP"
echo "backup: $BACKUP"

tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
trap 'rm -f "$tmp" "$tmp.new"' EXIT
cp -p "$ENV_FILE" "$tmp"
[ -s "$tmp" ] && [ "$(tail -c1 "$tmp" | wc -l)" -eq 0 ] && echo >> "$tmp"

i=0
for k in "${NAMES[@]}"; do
  v="${vals[$i]}"; i=$((i + 1))
  if grep -q "^${k}=" "$tmp"; then
    K="$k" V="$v" awk 'BEGIN{FS=OFS="="} $1==ENVIRON["K"]{print ENVIRON["K"] "=" ENVIRON["V"]; next} {print}' \
      "$tmp" > "$tmp.new" && mv "$tmp.new" "$tmp"
  else
    printf '%s=%s\n' "$k" "$v" >> "$tmp"
  fi
  echo "${k}=<set>"
done
unset vals v

cat "$tmp" > "$ENV_FILE"
echo
echo "done. Copy the three values to the team secret store NOW (do not paste them in chat)."
echo "Apply with (not run for you):"
echo "  $RECREATE"
