#!/usr/bin/env bash
# Runs proton_mfa_keys.sh against a fake env and a fake `docker` shim.
set -euo pipefail
S="$(cd "$(dirname "$0")/.." && pwd)/proton_mfa_keys.sh"
D="$(mktemp -d)"; trap 'rm -rf "$D"' EXIT
mkdir -p "$D/tenants" "$D/bin"; cd "$D"
fail(){ echo "FAIL: $1"; exit 1; }

# docker shim: prints rails-style YAML with distinctive fake secrets.
cat > bin/docker <<'E'
#!/usr/bin/env bash
[ -z "${FAKE_DOCKER_DOWN:-}" ] || exit 1
echo "Add this entry to the credentials of the target environment:"
echo "active_record_encryption:"
echo "  primary_key: PRIMsecretAAA111"
echo "  deterministic_key: DETsecretBBB222"
echo "  key_derivation_salt: SALTsecretCCC333"
E
chmod +x bin/docker
export PATH="$D/bin:$PATH"

reset(){ rm -f tenants/proton.env.bak-mfa-20261110; cp orig tenants/proton.env; }
cat > orig <<'E'
# comment
TENANT=proton
SECRET_KEY_BASE=abc=def/+
ACTIVE_RECORD_ENCRYPTION_PRIMARY_KEY=
E
reset

bash -n "$S"
out=$(bash "$S" --dry-run)
cmp -s orig tenants/proton.env || fail "dry-run changed env"
[ ! -e tenants/proton.env.bak-mfa-20261110 ] || fail "dry-run made backup"
echo "$out" | grep -q 'replace ACTIVE_RECORD_ENCRYPTION_PRIMARY_KEY=<set>' || fail "dry-run replace"
echo "$out" | grep -q 'append ACTIVE_RECORD_ENCRYPTION_KEY_DERIVATION_SALT=<set>' || fail "dry-run append"

ino=$(ls -i tenants/proton.env | awk '{print $1}')
out=$(bash "$S" 2>&1)
[ "$ino" = "$(ls -i tenants/proton.env | awk '{print $1}')" ] || fail "inode changed"
cmp -s orig tenants/proton.env.bak-mfa-20261110 || fail "backup differs"
grep -qx 'ACTIVE_RECORD_ENCRYPTION_PRIMARY_KEY=PRIMsecretAAA111' tenants/proton.env || fail "primary"
grep -qx 'ACTIVE_RECORD_ENCRYPTION_DETERMINISTIC_KEY=DETsecretBBB222' tenants/proton.env || fail "det"
grep -qx 'ACTIVE_RECORD_ENCRYPTION_KEY_DERIVATION_SALT=SALTsecretCCC333' tenants/proton.env || fail "salt"
[ "$(grep -c '^ACTIVE_RECORD_ENCRYPTION_PRIMARY_KEY=' tenants/proton.env)" = 1 ] || fail "dup key"
grep -qx 'SECRET_KEY_BASE=abc=def/+' tenants/proton.env || fail "untouched key changed"
grep -qx '# comment' tenants/proton.env || fail "comment lost"
echo "$out" | grep -q 'AAA111\|BBB222\|CCC333' && fail "value printed"
echo "$out" | grep -q 'force-recreate chatwoot-rails chatwoot-sidekiq' || fail "recreate cmd"

# second run refuses, changes nothing
cp tenants/proton.env after
rc=0; out=$(bash "$S" 2>&1) || rc=$?
[ "$rc" != 0 ] || fail "re-run did not refuse"
cmp -s after tenants/proton.env || fail "re-run changed env"
echo "$out" | grep -q 'AAA111\|BBB222\|CCC333' && fail "refusal printed value"

# rollback
bash "$S" --rollback >/dev/null
cmp -s orig tenants/proton.env || fail "rollback"

# container down: non-zero, no write, no backup
reset
rc=0; FAKE_DOCKER_DOWN=1 bash "$S" >/dev/null 2>&1 || rc=$?
[ "$rc" != 0 ] || fail "down container exited 0"
cmp -s orig tenants/proton.env || fail "down container wrote env"
[ ! -e tenants/proton.env.bak-mfa-20261110 ] || fail "down container made backup"
echo PASS
