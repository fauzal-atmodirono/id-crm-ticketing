#!/usr/bin/env bash
# Mint one wa.me entry link per test persona, printed as a markdown table.
#
# The persona list below is real data from
# `aeon360_customer_marts.member_repurchase_due` (pulled 2026-08-21) — pseudonymous
# member keys and product rows, no names and no phone numbers, which is why it is
# safe to keep in the repo.
#
# Needs `POST /entry-link` deployed (branch `feat/entry-link-deeplink` in
# my-aeon360-customer-waba). Until that ships this script exits 2 with the reason
# rather than printing half a table of broken links.
#
# The links it prints are CREDENTIALS: each authenticates its holder as one member
# for 72 hours. Paste them into a private test doc, never a shared page, a ticket,
# or a chat channel with people outside the test group.
set -euo pipefail

BASE="${BASE:-https://innovation.dev.aeon360.net/aeon360-customer-waba}"
: "${NUDGE_API_KEY:?set NUDGE_API_KEY (the nudge.api_key from aeon360-customer-waba-config)}"

# member_key|label|what to verify
PERSONAS=(
  "202408675835|Baby needs, heavy buyer|10 buys of TOLLYJOY BEST BUY 1 on a 10-day cycle, 17 days overdue. Should name the item and the cadence, not a generic catalogue answer."
  "202522100326|Fresh poultry, richest history|183 purchases, 70 SKUs due. Top item AYAM KAMPUNG BIG (CUBE), 7-day cycle, 21 days overdue. Tests whether it picks ONE thing rather than reciting a list."
  "202530400461|Cold beverage|BUBBLES02 BOTTLE 425ML, 20-day cycle, 28 days overdue."
  "202210795936|Vegetables, short shelf life|HK KAILAN 200G, 12-day cycle, 21 days overdue. Fresh produce — check it does not push a 'bulk order' framing."
  "300000182216|Different member-key format|Key starts 3000000, not 2026. KKH BROCCOLI ORG, 7-day cycle. Tests the parser on both key shapes."
  "202603301842|Nothing due — the honesty case|Soonest item is 118 days away. It must NOT invent a restock. Expect a graceful 'nothing needs restocking' and an offer to help with something else."
)

printf '| Persona | Member key | Deep link | What to verify |\n'
printf '|---|---|---|---|\n'

for row in "${PERSONAS[@]}"; do
  IFS='|' read -r key label verify <<<"$row"
  body="$(curl -fsS -X POST "$BASE/entry-link" \
      -H "Authorization: Bearer $NUDGE_API_KEY" \
      -H 'Content-Type: application/json' \
      -d "{\"member_key\":\"$key\",\"reason\":\"scenario-test\"}" 2>/dev/null)" || {
    echo "" >&2
    echo "FAILED minting for $key against $BASE/entry-link" >&2
    echo "  404 → /entry-link is not deployed yet (merge feat/entry-link-deeplink)" >&2
    echo "  401 → NUDGE_API_KEY is wrong" >&2
    echo "  503 → nudge.api_key or twilio.whatsapp_number unset on the service" >&2
    exit 2
  }
  url="$(printf '%s' "$body" | sed -E 's/.*"url"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/')"
  printf '| %s | `%s` | %s | %s |\n' "$label" "$key" "$url" "$verify"
done

echo
echo "Links expire in 72 hours. Re-run to refresh." >&2
