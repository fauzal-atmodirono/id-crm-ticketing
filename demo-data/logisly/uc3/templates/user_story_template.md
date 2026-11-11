# Template issue Jira: user story

**Summary:** `[Driver App|BE|FE|Ops Console] As <role>, I want <kemampuan> so that <manfaat>`
(prefix boleh kosong; lihat jira/conventions.yaml)

**Description:** satu-dua kalimat konteks, plus link PRD dan sumber requirement.

**Acceptance:** daftar bernomor, tiap kriteria bisa dicek.
1. <kriteria 1>
2. <kriteria 2>

**Field wajib:**
- Type: Story
- Component: satu dari Driver App, Platform, Ops Console, Shipper Portal, Data, AI Agent
- Story points: 1, 2, 3, 5 atau 8 (tidak ada 13; pecah story-nya)
- Sprint: sprint tujuan (lihat team/calendar.yaml), boleh kosong saat draft
- Assignee: boleh kosong saat draft, wajib saat sprint planning
- Epic: link ke Epic kalau bagian dari fitur besar
- Links: `blocks` / `relates to` / `duplicates` kalau ada dependensi
- Labels: bebas (mis. `uc1-milestone`, `pilot`, `phase-2`)
