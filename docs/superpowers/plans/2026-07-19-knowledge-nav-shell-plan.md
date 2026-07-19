# Knowledge Nav Shell (patch 0009) — Implementation Plan

> Nav-shell-first: a left-nav **"Knowledge"** menu that mirrors Captain's menu+sub-menu
> (FAQs, Documents, Playground, Inboxes, Tools, Settings). **FAQs is live** (embeds our
> working Knowledge Manager); the other five are real routes with clean placeholders,
> fleshed out in follow-ups. Gated by `hasFeature('knowledge')`.

**Repo:** `id-crm-ticketing` (fork patch). **Branch:** `dev-yuda`. Ships as
`deploy/chatwoot-fork/patches/0009-knowledge-nav.patch`, authored in a clone with 0001–0008
applied, exported via `git diff`, then image rebuild + redeploy.

## Reference — Captain's structure (verified in `components-next/sidebar/Sidebar.vue`)
A primary menu item with a `children` sub-menu:
```js
{ name: 'Captain', icon: 'i-woot-captain', label: t('SIDEBAR.CAPTAIN'), activeOn: [...],
  children: [ { name: 'FAQs', label: t('...'), activeOn: [...], to: accountScopedRoute('captain_assistants_index', { navigationPath: 'captain_assistants_responses_index' }) }, ... ] }
```
`menuItems` is a `computed(() => [ ... ])` at `Sidebar.vue:316`; `accountScopedRoute` is
imported at line 45 (`const { accountScopedRoute, isOnChatwootCloud } = useAccount()`); items
like `Contacts` (with `children`) are the insertion neighbours.

## Files
| Path | Action | Responsibility |
|---|---|---|
| `app/javascript/dashboard/views/ProtonKnowledgeHost.vue` | Create | Route view: reads route param `section`; for `faqs` iframes the Knowledge Manager, else a placeholder card |
| `app/javascript/dashboard/components-next/sidebar/Sidebar.vue` | Modify | Add the `Knowledge` menu item + 6 children (gated by `hasFeature('knowledge')`, which patch 0002 already wired via `useProtonConfig`) |
| `app/javascript/dashboard/routes/dashboard/dashboard.routes.js` | Modify | Add named routes `proton_knowledge_{faqs,documents,playground,inboxes,tools,settings}` → `ProtonKnowledgeHost` (param `section`) |
| `app/javascript/dashboard/i18n/locale/en/*.json` (+ `id`) | Modify | `PROTON_KNOWLEDGE.{TITLE,FAQS,DOCUMENTS,PLAYGROUND,INBOXES,TOOLS,SETTINGS,COMING_SOON}` |

## Tasks

### Task 1 — `ProtonKnowledgeHost.vue`
`<script setup>`: `const route = useRoute(); const { backendUrl, backendKey } = useProtonConfig();`
- `section = computed(() => route.meta?.section || route.params.section || 'faqs')`.
- **FAQs iframe URL:** the Knowledge Manager is served by the agent host, and it takes
  `?backend=&key=`. Derive the agent host from the current origin (crm→agent) so no new config:
  `const agentBase = window.location.origin.replace('://crm.', '://agent.');`
  `const faqsSrc = computed(() => \`${agentBase}/apps/knowledge-manager?backend=${encodeURIComponent(backendUrl)}&key=${encodeURIComponent(backendKey)}\`)`.
- Template: full-height panel with a header (`Knowledge / {{ sectionLabel }}`). If `section === 'faqs'`, render `<iframe :src="faqsSrc" style="width:100%;height:100%;border:none" />`; else a centered placeholder card: "**{{ sectionLabel }}** — coming soon" (use `PROTON_KNOWLEDGE.COMING_SOON`).
- Import via alias: `import { useProtonConfig } from 'dashboard/composables/useProtonConfig';`.

### Task 2 — routes (`dashboard.routes.js`)
Add six child routes under the account-scoped dashboard tree (siblings of patch 0003's
`proton-app`), each `{ path: 'proton/knowledge/<section>', name: 'proton_knowledge_<section>', component: () => import('../../views/ProtonKnowledgeHost.vue'), meta: { permissions: [...admin+agent...], section: '<section>' } }`. Use the same `meta.permissions` shape the neighbouring dashboard routes use so it shows for admins+agents. (Confirm the exact nesting/`frontendURL` wrapper from the file — patch 0003's route is the reference.)

### Task 3 — Sidebar "Knowledge" menu
In the `menuItems` computed array (insert before `Contacts`), add — gated so it only appears when our feature is on:
```js
...(protonHasFeature('knowledge') ? [{
  name: 'Knowledge', icon: 'i-lucide-book-open', label: t('SIDEBAR.PROTON_KNOWLEDGE'),
  children: [
    { name: 'FAQs',      label: t('...FAQS'),      to: accountScopedRoute('proton_knowledge_faqs') },
    { name: 'Documents', label: t('...DOCUMENTS'), to: accountScopedRoute('proton_knowledge_documents') },
    { name: 'Playground',label: t('...PLAYGROUND'),to: accountScopedRoute('proton_knowledge_playground') },
    { name: 'Inboxes',   label: t('...INBOXES'),   to: accountScopedRoute('proton_knowledge_inboxes') },
    { name: 'Tools',     label: t('...TOOLS'),     to: accountScopedRoute('proton_knowledge_tools') },
    { name: 'Settings',  label: t('...SETTINGS'),  to: accountScopedRoute('proton_knowledge_settings') },
  ],
}] : []),
```
`protonHasFeature`/`hasFeature` is already available in Sidebar.vue from patch 0002
(`const { hasFeature: protonHasFeature } = useProtonConfig()` — confirm the exact identifier
patch 0002 introduced and reuse it). Icon: pick a valid `i-lucide-*` (e.g. `i-lucide-book-open`).

### Task 4 — i18n
Add `PROTON_KNOWLEDGE` + `SIDEBAR.PROTON_KNOWLEDGE` keys to `en` (and `id`) locale files
(`sidebar.json` / a suitable existing file), matching how patch 0003 added its nav i18n.

### Task 5 — export, build, deploy, verify
- Author in a clone (0001–0008 applied) → `git diff` the 4 files → `0009-knowledge-nav.patch`.
- `git apply --check` cumulative 0001–0009; image rebuild (vite compile gate); redeploy
  `chatwoot-rails`/`sidekiq`.
- Browser smoke (`crm.localhost`, hard-refresh): left nav shows **Knowledge** with the 6
  sub-items; **FAQs** loads the Knowledge Manager (add/list works); the other five show the
  placeholder. No console errors.

## Notes / risks
- **Icon name** must be a real `i-lucide-*` or the item renders without an icon (harmless).
- **Route nesting**: `dashboard.routes.js` is deeply nested; the six routes must sit where
  `accountScopedRoute('proton_knowledge_faqs')` resolves — patch 0003's `proton-app` route is
  the working reference for correct placement.
- **FAQs iframe host derivation** (`crm.`→`agent.`) works for `*.localhost` and `*.nip.io`;
  for a real production domain, thread the apps base URL through the injected config instead.
- Follow-ups (each its own patch + backend): Documents (Vertex KB CRUD), Playground (copilot
  test harness), Tools (custom-tools CRUD), Inboxes (assistant↔inbox map), Settings (assistant config).
