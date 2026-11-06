# Template issue GitHub: user story

**Judul:** `[<area>] <kalimat pendek, kata kerja di depan>`

**Body:**

```
### User story
Sebagai <user>, saya ingin <kemampuan>, supaya <manfaat>.

### Acceptance criteria
- [ ] Given <kondisi>, when <aksi>, then <hasil>
- [ ] ...

### Catatan teknis
<endpoint, model data, library, batasan>

### Dependensi
- Blocked by #<nomor> (kalau ada)

### Referensi
- PRD: <link>
- Sumber requirement: <dokumen / meeting / email>
```

**Field wajib:**
- Label: satu `type:*`, satu `area:*`, satu `priority:*`
- Milestone: sprint tujuan (lihat github/conventions.yaml)
- Estimate: story point di label `sp:1|2|3|5|8` (tidak ada 13; pecah story-nya)
- Assignee: boleh kosong saat draft, wajib saat sprint planning
