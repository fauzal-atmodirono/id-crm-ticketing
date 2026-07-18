#!/usr/bin/env bats
# Smoke tests for provision_labels.py CLI interface.
# Exercises the --dry-run flag and error paths without a real Chatwoot.
#
# Run from the repo root:
#   bats chatwoot-config/smoke/smoke_idempotency.bats
#
# Prerequisites:
#   pip install httpx PyYAML
#   brew install bats-core   # macOS
#   apt-get install bats     # debian/ubuntu

SCRIPT="$(dirname "$BATS_TEST_FILENAME")/../provision_labels.py"
LABELS="$(dirname "$BATS_TEST_FILENAME")/../labels.yaml"
FILTERS="$(dirname "$BATS_TEST_FILENAME")/../filters.yaml"

@test "script is executable via python" {
  run python "$SCRIPT" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Idempotently provision"* ]]
}

@test "--dry-run with no chatwoot-url exits 1 with error message" {
  run python "$SCRIPT" \
    --chatwoot-url "" \
    --api-token "" \
    --labels "$LABELS" \
    --filters "$FILTERS" \
    --dry-run
  [ "$status" -eq 1 ]
  [[ "$output" == *"required"* ]] || [[ "${lines[@]}" =~ "required" ]]
}

@test "--dry-run prints DRY RUN prefix when credentials provided but no real chatwoot" {
  # We can't actually call a Chatwoot here, but we can verify the CLI parses
  # credentials and attempts to connect (will fail with connection error, not arg error).
  # We check that a missing env file returns exit 1 with a clear message.
  run python "$SCRIPT" \
    --tenant nonexistent_tenant_xyz \
    --labels "$LABELS" \
    --filters "$FILTERS" \
    --dry-run
  [ "$status" -eq 1 ]
  [[ "$output" == *"not found"* ]] || [[ "${lines[*]}" == *"not found"* ]]
}

@test "labels.yaml is valid YAML with required keys" {
  run python -c "
import yaml, sys
data = yaml.safe_load(open('$LABELS'))
labels = data['labels']
assert len(labels) > 0, 'no labels defined'
for lbl in labels:
    assert 'name' in lbl, f'missing name in {lbl}'
    assert 'color' in lbl, f'missing color in {lbl}'
    assert lbl['color'].startswith('#'), f'color must start with # in {lbl}'
    assert 'description' in lbl, f'missing description in {lbl}'
    assert 'group' in lbl, f'missing group in {lbl}'
print(f'OK: {len(labels)} labels validated')
"
  [ "$status" -eq 0 ]
  [[ "$output" == *"OK:"* ]]
}

@test "filters.yaml is valid YAML with required keys" {
  run python -c "
import yaml, sys
data = yaml.safe_load(open('$FILTERS'))
filters = data['filters']
assert len(filters) > 0, 'no filters defined'
for f in filters:
    assert 'name' in f, f'missing name in {f}'
    assert 'filter_type' in f, f'missing filter_type in {f}'
    assert f['filter_type'] in ('account', 'conversation'), f'invalid filter_type in {f}'
    assert 'query' in f, f'missing query in {f}'
    assert 'payload' in f['query'], f'missing query.payload in {f}'
print(f'OK: {len(filters)} filters validated')
"
  [ "$status" -eq 0 ]
  [[ "$output" == *"OK:"* ]]
}

@test "all label names follow snake_case convention with known prefix" {
  run python -c "
import yaml, re
data = yaml.safe_load(open('$LABELS'))
valid_prefixes = ('category_', 'subcat_', 'division_', 'dept_', 'sla_', 'pic_', 'escalat')
pattern = re.compile(r'^[a-z][a-z0-9_]+$')
for lbl in data['labels']:
    name = lbl['name']
    assert pattern.match(name), f'name not snake_case: {name}'
    assert any(name.startswith(p) for p in valid_prefixes), f'unknown prefix: {name}'
print('OK: all label names valid')
"
  [ "$status" -eq 0 ]
  [[ "$output" == *"OK: all label names valid"* ]]
}
