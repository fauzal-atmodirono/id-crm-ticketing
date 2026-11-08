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
grep -qx 'ESCALATION_ATTACHMENT_BUDGET_BYTES=10485760' tenants/proton.env || fail "attachment budget"
grep -qx 'PHONE_HANDOFF_TIMEOUT_SECONDS=20' tenants/proton.env || fail "phone handoff timeout"
grep -qx 'MEDIA_DIAGNOSIS_PROMPT_ENABLED=true' tenants/proton.env || fail "media diagnosis flag"
grep -qx 'SECRET_KEY_BASE=abc=def/+' tenants/proton.env || fail "untouched key changed"
grep -qx 'TENANT=proton' tenants/proton.env || fail "TENANT changed"
grep -qx '# comment' tenants/proton.env || fail "comment lost"
echo "$out" | grep -q 'abc=def\|=true$\|=bigquery' && fail "value printed"
echo "$out" | grep -q 'up -d --build backend' || fail "recreate cmd missing"
[ "$(grep -c . tenants/proton.env)" = 28 ] || fail "line count $(grep -c . tenants/proton.env)"

# re-run is idempotent and keeps the original backup
cp tenants/proton.env after1; bash "$S" >/dev/null
cmp -s after1 tenants/proton.env || fail "rerun not idempotent"
cmp -s orig tenants/proton.env.bak-20261110 || fail "backup overwritten"

bash "$S" --rollback >/dev/null
cmp -s orig tenants/proton.env || fail "rollback"
# --- agent mode with a fake docker shim ---
mkdir -p bin
cat > bin/docker <<'SH'
#!/usr/bin/env bash
[ "$1 $2 $3" = "exec proton-agent printenv" ] || exit 9
[ -n "${FAKE_DOCKER_DOWN:-}" ] && { echo "Error: No such container" >&2; exit 1; }
printf '%s\n' 'PATH=/usr/bin' 'ESCALATION_POLICY_L1=a=b==c' 'ESCALATION_POLICY_L2=plain' \
  'ESCALATION_POLICY_J={"a": "b"}' 'OTHER_VAR=nope'
[ -n "${FAKE_BAD:-}" ] && printf '%s\n' "$FAKE_BAD"
exit 0
SH
chmod +x bin/docker
export PATH="$PWD/bin:$PATH"
reset(){ cp orig tenants/proton.env; rm -f tenants/proton.env.bak-20261110
  printf 'ESCALATION_POLICY_L2=old\nLIFECYCLE_IDLE_WARN_MINUTES=1\n' >> tenants/proton.env
  cp tenants/proton.env pre_agent; }
reset
out=$(bash "$S" --agent --dry-run)
cmp -s pre_agent tenants/proton.env || fail "agent dry-run changed env"
[ ! -e tenants/proton.env.bak-20261110 ] || fail "agent dry-run made backup"
echo "$out" | grep -q 'replace ESCALATION_POLICY_L2=<set>' || fail "agent dry-run replace"
echo "$out" | grep -q 'append ESCALATION_POLICY_L1=<set>' || fail "agent dry-run append"
echo "$out" | grep -q 'a=b\|plain' && fail "agent dry-run printed value"
out=$(bash "$S" --agent)
grep -qx 'ESCALATION_POLICY_L1=a=b==c' tenants/proton.env || fail "value with = not copied intact"
grep -qx 'ESCALATION_POLICY_L2=plain' tenants/proton.env || fail "L2 not replaced"
grep -qx 'ESCALATION_POLICY_J={"a": "b"}' tenants/proton.env || fail "plain JSON not copied"
[ "$(grep -c '^ESCALATION_POLICY_L2=' tenants/proton.env)" = 1 ] || fail "dup L2"
grep -q 'OTHER_VAR\|^PATH=' tenants/proton.env && fail "non-escalation var copied"
grep -qx 'LIFECYCLE_IDLE_WARN_MINUTES=10' tenants/proton.env || fail "lifecycle replace"
grep -qx 'LIFECYCLE_IDLE_CLOSE_OUT_OF_HOURS_GRACE_MINUTES=0' tenants/proton.env || fail "grace 0"
grep -qx 'EMAIL_AUTOACK_ENABLED=true' tenants/proton.env || fail "autoack"
echo "$out" | grep -q 'a=b\|plain' && fail "agent apply printed value"
echo "$out" | grep -q 'up -d --no-build agent' || fail "agent recreate cmd"
cmp -s pre_agent tenants/proton.env.bak-20261110 || fail "agent backup not first-run original"
bash "$S" --rollback >/dev/null
cmp -s pre_agent tenants/proton.env || fail "agent rollback"

# container missing: non-zero, no write, no backup, no value
reset
rc=0; out=$(FAKE_DOWN=1 FAKE_DOCKER_DOWN=1 bash "$S" --agent 2>&1) || rc=$?
[ "$rc" != 0 ] || fail "missing container exited 0"
cmp -s pre_agent tenants/proton.env || fail "missing container wrote env"
[ ! -e tenants/proton.env.bak-20261110 ] || fail "missing container made backup"

# unsafe values: one per case; each aborts naming the key only, writes nothing
for name in dollar hash bslash dq sq lead trail nl; do
  case "$name" in
    dollar) val='a$b';; hash) val='a #b';; bslash) val='a\b';; dq) val='"quoted"';;
    sq) val="'q'";; lead) val=' x';; trail) val='x ';; nl) val=$'line1\nline2';;
  esac
  reset
  rc=0; out=$(FAKE_BAD="ESCALATION_POLICY_BAD=$val" bash "$S" --agent 2>&1) || rc=$?
  [ "$rc" != 0 ] || fail "unsafe '$name' exited 0"
  echo "$out" | grep -q 'ESCALATION_POLICY_BAD' || fail "unsafe '$name' key not named"
  echo "$out" | grep -q 'line2\|quoted\|a\$b' && fail "unsafe '$name' printed value"
  cmp -s pre_agent tenants/proton.env || fail "unsafe '$name' wrote env"
  [ ! -e tenants/proton.env.bak-20261110 ] || fail "unsafe '$name' made backup"
  rc=0; FAKE_BAD="ESCALATION_POLICY_BAD=$val" bash "$S" --agent --dry-run >/dev/null 2>&1 || rc=$?
  [ "$rc" != 0 ] || fail "unsafe '$name' dry-run exited 0"
done
echo "PASS"
