#!/usr/bin/env bash
# Runs proton_env_2026-11-10.sh against a fake env in a temp dir and asserts the result.
set -euo pipefail
S="$(cd "$(dirname "$0")/.." && pwd)/proton_env_2026-11-10.sh"
D="$(mktemp -d)"; trap 'rm -rf "$D"' EXIT
mkdir -p "$D/tenants"; cd "$D"
fail(){ echo "FAIL: $1"; exit 1; }
cat > tenants/proton.env <<'E'
# comment
TENANT=proton
SLA_INBOX_IDS=3
METRICS_PROVIDER=noop
SECRET_KEY_BASE=abc=def/+
E
cp tenants/proton.env orig

bash -n "$S"
out=$(bash "$S" --dry-run)
cmp -s orig tenants/proton.env || fail "dry-run changed env"
[ ! -e tenants/proton.env.bak-20261110 ] || fail "dry-run made backup"
echo "$out" | grep -q 'replace SLA_INBOX_IDS=<set>' || fail "dry-run replace line"
echo "$out" | grep -q 'append ACW_ENABLED=<set>' || fail "dry-run append line"

out=$(bash "$S")
cmp -s orig tenants/proton.env.bak-20261110 || fail "backup differs from original"
grep -qx 'SLA_INBOX_IDS=3,5' tenants/proton.env || fail "SLA replaced"
grep -qx 'METRICS_PROVIDER=bigquery' tenants/proton.env || fail "metrics replaced"
[ "$(grep -c '^SLA_INBOX_IDS=' tenants/proton.env)" = 1 ] || fail "dup SLA key"
grep -qx 'ACW_ENABLED=true' tenants/proton.env || fail "ACW appended"
grep -qx 'NPS_SAMPLE_RATE=0.5' tenants/proton.env || fail "NPS appended"
grep -qx 'SECRET_KEY_BASE=abc=def/+' tenants/proton.env || fail "untouched key changed"
grep -qx 'TENANT=proton' tenants/proton.env || fail "TENANT changed"
grep -qx '# comment' tenants/proton.env || fail "comment lost"
echo "$out" | grep -q 'abc=def\|=true$\|=bigquery' && fail "value printed"
echo "$out" | grep -q 'up -d --build backend' || fail "recreate cmd missing"
[ "$(grep -c . tenants/proton.env)" = 25 ] || fail "line count $(grep -c . tenants/proton.env)"

# re-run is idempotent and keeps the original backup
cp tenants/proton.env after1; bash "$S" >/dev/null
cmp -s after1 tenants/proton.env || fail "rerun not idempotent"
cmp -s orig tenants/proton.env.bak-20261110 || fail "backup overwritten"

bash "$S" --rollback >/dev/null
cmp -s orig tenants/proton.env || fail "rollback"
echo "PASS"
