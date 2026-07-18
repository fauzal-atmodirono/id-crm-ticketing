# Phase 1 — Self-service Categorization & Filtering: Ops Runbook

**Audience:** Proton CRM operators who need to create labels or saved filter views for a
tenant, either via the automated provisioning script or manually in the Chatwoot UI.

---

## Option A — Automated provisioning script (recommended)

### Prerequisites

1. SSH to the GCE VM (or any machine with access to the tenant's Chatwoot URL).
2. Python 3.12 installed.
3. The tenant is already provisioned (`deploy/tenants/<tenant>.env` exists with
   `CHATWOOT_API_TOKEN` and `CHATWOOT_ACCOUNT_ID` filled in).

### Install dependencies (once per machine)

```bash
cd /opt/id-crm-ticketing/chatwoot-config
pip install -r requirements.txt
```

### Dry-run first (see what will change)

```bash
python provision_labels.py --tenant default --dry-run
```

Sample output:
```
[DRY RUN] Provisioning tenant 'default' (account 1)
  [CREATE] label 'category_complaint' (#e74c3c)
  [CREATE] label 'category_inquiry' (#3498db)
  ... (all labels and filters listed)

[DRY RUN] Done.
Labels  — created: 25, updated: 0, unchanged: 0
Filters — created: 8, unchanged: 0
```

### Apply

```bash
python provision_labels.py --tenant default
```

### Replicate to other tenants

```bash
python provision_labels.py --tenant proton
python provision_labels.py --tenant wahchan
```

### Adding a new label

1. Add an entry to `chatwoot-config/labels.yaml` following the existing format:
   ```yaml
   - name: category_my_new_type
     color: "#2C3E50"
     description: "AI-classified: my new category"
     group: category
   ```
2. Re-run `provision_labels.py --tenant <tenant>`. The script is additive and
   idempotent — existing labels are untouched unless color/description changed.

### Adding a new saved filter view

1. Add an entry to `chatwoot-config/filters.yaml`:
   ```yaml
   - name: "My New Filter"
     filter_type: account
     query:
       payload:
         - attribute_key: labels
           filter_operator: contains
           query_operator: null
           values:
             - category_my_new_type
   ```
2. Re-run `provision_labels.py --tenant <tenant>`.
3. Note: saved filters cannot be updated via the API (Chatwoot Community does not
   expose a PATCH endpoint for saved filters). To update an existing filter, delete
   it manually in Chatwoot Settings → Custom Views, then re-run the script.

---

## Option B — Manual via Chatwoot Settings UI

Use this if you do not have terminal access or prefer the browser.

### Create labels manually

1. Log in to Chatwoot as a tenant admin.
2. Go to **Settings → Labels → Add Label**.
3. Set:
   - **Label name:** use the exact snake_case name from `chatwoot-config/labels.yaml`
     (e.g. `category_complaint`)
   - **Color:** the hex value from the YAML (e.g. `#E74C3C`)
   - **Description:** copy from the YAML
4. Click **Create**.
5. Repeat for all labels in the taxonomy.

Label prefix conventions:
| Prefix | Used for |
|---|---|
| `category_` | Top-level complaint/contact type (AI-emitted) |
| `subcat_` | Sub-category (AI-emitted) |
| `division_` | Business division: Apps / Sales / Aftersales / Charging |
| `dept_` | Department routing target |
| `sla_` | SLA priority markers (written by the SLA engine) |
| `pic_` | PIC assignment state (written by escalation engine) |
| `escalate` / `escalated_l2` | Escalation trigger / state |

### Create saved filter views manually

1. Go to **Conversations**.
2. In the left sidebar, click **+ New View** (under "Views").
3. Add filter conditions matching the definitions in `chatwoot-config/filters.yaml`:
   - For "All Complaints": Filter → Label → Contains → `category_complaint`
   - For "SLA Breach — Open": Label Contains `sla_breach` AND Status Equals `open`
   - (etc. — one view per entry in `filters.yaml`)
4. Name the view exactly as in `filters.yaml` so the provisioning script recognises
   it as already-existing on future runs.
5. Set **Visibility** to **Everyone** (team-wide / account scope).

---

## Verifying correct setup

After provisioning (either method):

1. Open any conversation in Chatwoot.
2. In the right panel under **Conversation Labels**, you should see the taxonomy
   labels available to add.
3. In the left sidebar under **Views**, you should see all saved filter views
   (e.g. "All Complaints", "SLA Breach — Open", etc.).
4. Click "All Complaints" — it should filter the conversation list to conversations
   labelled `category_complaint`.
5. The AI engine (`proton-conversational-ai`) already writes `category_*`,
   `subcat_*`, `division_*`, `dept_*` labels to every inbound conversation
   automatically. After an inbound contact, verify the labels appear on that
   conversation and that clicking the corresponding saved view shows the conversation.

---

## Label lifecycle — important notes

- **Do not delete labels** that the AI emitter uses — the AI backend references
  label names verbatim. To retire a label, remove it from `labels.yaml` and stop
  the AI from emitting it (backend change), then archive in Chatwoot.
- **Saved filter updates:** Chatwoot Community does not expose a PATCH endpoint for
  saved filters. To modify a saved view: delete it in the UI, update `filters.yaml`,
  re-run the provisioning script.
- **Multi-tenant:** labels and saved views are per-account (per-tenant). Running
  `provision_labels.py --tenant proton` creates the taxonomy in the `proton` tenant;
  it does not touch the `default` tenant.

---

## Serving the Taxonomy Manager app (requires Phase 0)

The `apps/taxonomy-manager/index.html` file must be served by Caddy.
Add the following block to the tenant's Caddy snippet at
`deploy/caddy/tenants/<tenant>.caddy`, inside the `agent.<ip>.nip.io` vhost block,
**before** the reverse_proxy directive (Caddy matches first-matching):

```caddyfile
handle /apps/taxonomy-manager* {
    root * /opt/id-crm-ticketing/apps/taxonomy-manager
    file_server
}
```

Then reload Caddy:
```bash
docker exec platform-infra-caddy-1 caddy reload --config /etc/caddy/Caddyfile
```

Register the app as a Chatwoot dashboard app:
```bash
python chatwoot-config/register_taxonomy_app.py \
    --tenant default \
    --app-url http://agent.<PUBLIC_IP>.nip.io/apps/taxonomy-manager
```

The app will appear in Chatwoot under **Settings → Integrations → Dashboard Apps**
and can be pinned to conversations via the right-panel "+" button.
