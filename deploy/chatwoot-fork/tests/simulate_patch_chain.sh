#!/bin/sh
# Apply every patch in order, but only to files that fork patches create
# (no upstream tree needed). Catches context drift on those files.
# Usage: simulate_patch_chain.sh <file-substring>   e.g. ProtonRsaPage.vue
set -e
here=$(cd "$(dirname "$0")/../patches" && pwd)
d=$(mktemp -d); cd "$d"; git init -q .
rc=0
for p in "$here"/*.patch; do
  grep -q "$1" "$p" || continue
  if git apply --include="*$1" "$p" 2>err; then echo "ok   $(basename "$p")"
  else echo "FAIL $(basename "$p")"; cat err; rc=1; fi
done
exit $rc
