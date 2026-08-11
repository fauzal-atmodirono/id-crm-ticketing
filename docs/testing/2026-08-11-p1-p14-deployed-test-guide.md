# How to test P1–P14 on the Proton tenant — 2026-08-11

What is actually deployed, where to click, and what counts as a pass. Written
after the 2026-08-11 deploy (fork image `3006906` / `v4.15.1-custom-rc1`,
backend + agent rebuilt from `ab47b43`).

**CRM:** http://proton.crm.34-50-103-151.nip.io

**Before you start:** hard-refresh the browser (Cmd-Shift-R). The vite bundle
changed; a cached bundle is the single most common reason a "deployed" patch
appears missing. Rails needs ~60–90s after a recreate before any page load
means anything — that time has long passed, but remember it for the next one.

Only **P2, P6, P7, P9, P10** produced fork patches, so only those have new UI.
Everything else in the programme is backend, docs, or BigQuery.

---

## 1. Testable in the UI right now

### P7 task 3 — Translate (patch 0055)

1. Open a conversation whose latest **customer** message is Malay, Tamil or
   Chinese. It must be an incoming message, not an agent reply.
2. In the reply composer's top panel (next to Copilot), click **Translate**.

**Pass:** a toast reads `Translated (<lang> → en) and posted as a private note`,
and a private note appears in the conversation with the English text.

**This is the highest-risk item in the deploy** — the button ships in the fork
but the translation is done by `/assist/translate` in the backend, and those
two travelled separately. If you see "Translation failed. Please try again.",
that is the backend, not the button.

Nothing is ever sent to the customer: `translate_router.py` posts a private
note only, by design.

### P7 task 7 — FAQ suggestion strip (patch 0056)

1. Open a conversation whose latest customer message closely matches a live FAQ
   (Knowledge → FAQs).
2. The strip appears above the composer with the FAQ title, **Apply** and
   **Dismiss**.

**Pass:** Apply pastes the **full answer** into the reply box — not a truncated
one. Dismiss hides it, and it stays hidden for that message even after
switching conversations and back.

**Two things worth deliberately checking**, because both were bugs fixed in
review and are exactly what a regression would undo:
- The pasted text must be the whole answer. A version cut off mid-sentence
  means the 280-char display snippet is being pasted.
- Switch to a *different* conversation with the same message count. The strip
  must clear. If conversation A's suggestion shows on B, the watch regressed.

Only FAQ hits with a confidence score ≥ 0.75 are shown, so a vague message
correctly shows nothing.

### P9 tasks 2/3/6 — inbound alerting (patch 0057)

- The alert indicator lives in the sidebar.
- Preferences page: `/app/accounts/<id>/proton/alert-preferences`.

**Pass:** a new inbound customer message raises a toast/sound/desktop
notification without a page refresh.

**Expected, not a bug:** the preferences page reports that alert rules are not
enabled — `ALERT_RULES_ENABLED` is deliberately off, so every agent gets the
built-in defaults. The page renders the backend's `{"disabled": true}` body
verbatim rather than guessing. Turn the flag on if you want to test per-agent
rules.

### P6 — workforce dashboard and status selector (patches 0053, 0054)

- Dashboard: `/app/accounts/<id>/proton/workforce`
- Availability selector: top bar.

**Pass:** the dashboard renders with agent rows. One column cannot be populated
— see blocked-work register §3c; that is known and not a deploy fault.

**Note:** `/routing/status` and `/routing/presence` are **not mounted** because
their flag is off. Custom labelled statuses (Follow-up, Lunch) are not built at
all — native Busy/Offline is what routing respects.

### P10 — Roles & Permissions, and case taxonomy (patches 0059, 0060)

- Roles: Settings → Roles & Permissions (rail + detail, staged saves).
- Taxonomy admin: `/app/accounts/<id>/proton/taxonomy`.

**Pass (roles):** edits stage and only persist on save; cancelling discards.
**Do not tick "Chatwoot access" on a role whose members are administrators** —
a custom role *replaces* `administrator` and will demote them.

**Pass (taxonomy):** the tree shows 8 divisions / 89 Level-1 / 246 Level-2, and
`/admin/taxonomy/coverage` responds.

### P2 task 7 — escalation manager contact (patch 0052)

CRM → Escalation Routing. **Pass:** a manager contact can be set per department
and survives a reload.

---

## 2. Backend-only — test with HTTP, not the UI

Deployed and reachable (109 OpenAPI paths):
`/assist/translate`, `/kb/suggest`, `/alerts/rules/defaults`,
`/alerts/rules/mine`, `/calls/{conversation_id}/recording`,
`/admin/taxonomy/*`, `/admin/customer360/search`.

The cheapest proof a route is live, with no side effects — an unauthenticated
call must return **401, not 404**:

```sh
sudo docker exec proton-backend python -c "
import urllib.request, urllib.error, json
req = urllib.request.Request('http://localhost:8080/assist/translate', method='POST',
    data=json.dumps({'conversation_id':'0','text':'x'}).encode(),
    headers={'Content-Type':'application/json'})
try: print(urllib.request.urlopen(req).status)
except urllib.error.HTTPError as e: print(e.code)"
```

**List paths by prefix, never probe for an exact string.** Checking for
`/alerts/rules` reports MISSING while `/alerts/rules/mine` is live — that error
was made during this deploy and reported as a gap that did not exist.

---

## 3. What cannot be tested, and why

| Item | Why not |
|---|---|
| P8 — eleven BigQuery views | Never created. Register §3c-2 |
| P8 task 1 — `ai_actions` columns | Manual `ALTER TABLE` still owed |
| P5 — five control items | Not measurable; register §3b |
| P11 — voice partials | Five modules, no caller; register §3k |
| P12 — screen-pop | 1 done-but-unreachable, 1 partial, 5 missing |
| P13 — ops hardening | Scripts written, never exercised; register §3c-4 |
| P10 — data-scoped RBAC | Logic shipped, enforcement did not; register §3j |

---

## 4. If something does not appear

In this order:

1. **Hard-refresh.** Cached bundle.
2. **Check the feature reached the browser**, not just the env file:
   ```sh
   sudo docker exec proton-chatwoot-rails sh -c \
     'wget -q -O - http://127.0.0.1:3000/app/login' | grep -o 'features[^<]*'
   ```
   Should read
   `ai_assist,nav_menu,copilot,knowledge,inbound_alerts,faq_suggestion_popup`.
   A flag can be `true` in `proton.env` and still not reach Rails if the VM's
   `docker-compose.tenant.yml` lacks the passthrough — that exact gap was found
   and fixed on 2026-08-11.
3. **Check the bundle contains the patch:**
   `sudo docker exec proton-chatwoot-rails sh -c "grep -rl 'Translating' /app/public/vite/assets | wc -l"`
   — non-zero means patch 0055 is in the image.
4. **Confirm the image:** `sudo docker exec proton-chatwoot-rails cat /app/.git_sha`
   → `3006906`.

## 5. Rollback

`CHATWOOT_IMAGE` in `tenants/proton.env` back to `:v4.15.1-custom`, then
recreate. The previous image is untouched under that tag. Backups on the VM:
`proton.env.bak-20260811`, `docker-compose.tenant.yml.bak-20260811`,
`/tmp/platform-src-backup-20260811.tgz`.
