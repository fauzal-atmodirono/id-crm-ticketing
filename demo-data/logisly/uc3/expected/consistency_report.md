# Laporan konsistensi: checklist follow-up driver per milestone (UC1)

Kunci jawaban demo. Ini temuan yang seharusnya dilaporkan agent setelah membaca keempat sumber di `uc3/sources/`, snapshot Jira, roster dan kalender. Setiap temuan menyebut sumbernya dan siapa yang memutuskan.

## Konflik yang perlu diputuskan

| # | Temuan | Sumber | Usulan | Yang memutuskan |
|---|---|---|---|---|
| 1 | **Tanggal release bertabrakan.** Request Ops: pilot live 4 Desember 2026. Product sync: 18 Desember 2026 (akhir Sprint 26.24). 4 Desember adalah akhir Sprint 26.23 dan LOG-3405 belum selesai. | 01 request, 02 notes, calendar | Ikut 18 Desember 2026, sesuai product sync dan kapasitas. | Hadi, Nadia |
| 2 | **18 Desember adalah hari terakhir sebelum code freeze** (21 Desember 2026 s.d. 3 Januari 2027, hanya hotfix P0). Tidak ada ruang perbaikan setelahnya. | 02 notes, calendar | Selesaikan UAT di Sprint 26.24 dan siapkan hotfix P0 saja setelah rilis. | Nadia |
| 3 | **Tenggat shipper 31 Desember 2026 jatuh di masa freeze.** PT Sinar Pangan Nusantara minta update bongkar dan POD rutin sebelum 31 Desember 2026. Fitur ini hanya membuat tiket MM dan pengingat ke driver, tidak mengirim notifikasi ke shipper. | 03 email, 02 notes | Pastikan live 18 Desember. Tanyakan apakah tiket MM dan POD di LOG-3412 cukup. | Sari, Nadia |
| 4 | **Jam tenang berbeda.** Request Ops: 21:00–06:00. Product sync (dan fitur UC1 yang sudah dibangun): 22:00–05:00 WIB. | 01 request, 02 notes | 22:00–05:00 WIB. | Hadi |
| 5 | **Trigger milestone otomatis bergantung ke LOG-3405** (geofence, In Progress, Sprint 26.22). | 02 notes, LOG-3405 | Pilot jalan dengan milestone manual atau dari app; story trigger GPS diberi link `is blocked by` ke LOG-3405. | Agung |
| 6 | **SOP Shopee: fase 1 atau 2?** Request menyebut Shopee sebagai catatan, product sync memutuskan fase 2 (Sprint 27.01, setelah freeze). | 01 request, 02 notes | Fase 2, story dengan label `phase-2`. | Hadi |

## Duplikat dan issue yang perlu diperbarui

| # | Temuan | Usulan |
|---|---|---|
| 7 | LOG-3417 (dibuat 5 Nov) duplikat LOG-3412 (dibuat 20 Okt), sama-sama upload foto POD di driver app. | Transisi LOG-3417 ke Won't Do, resolusi Duplicate, link `duplicates` ke LOG-3412. |
| 8 | LOG-3412 hanya punya acceptance minimal dan tidak punya sprint, assignee atau label. | Perkaya acceptance dari email shipper, set Sprint 26.23, assignee Rizky Ramadhan, label `uc1-milestone`. |
| 9 | **LOG-3398 (bot mengingatkan tiap 30 menit) digantikan fitur ini.** Chat CT: pengingat jalan jam 2 pagi tanpa jam tenang, dan foto driver diabaikan. | Tambah komentar "superseded" di LOG-3398; matikan bot lama setelah pilot. Tidak dibuka ulang. |

## Hal yang belum lengkap

| # | Temuan | Yang perlu ditanyakan |
|---|---|---|
| 10 | Biaya WhatsApp Rp 38 juta/bulan punya baseline total, tapi tidak ada target penurunan. | Wulan: baseline tiga bulan per kategori. Hadi: target. |
| 11 | KPI 18 menjadi 6 pertanyaan per trip dan 40% menjadi 80% kasus terlambat belum punya cara ukur. | Wulan: sumber data untuk rumus di PRD. |
| 12 | Notifikasi bongkar ke shipper tidak ada di cakupan UC1. | Sari dan Nadia: perlu story terpisah atau tidak. |

## Kapasitas (Sprint 26.22 s.d. 26.24, sebelum freeze)

Kapasitas per tim = jumlah capacity_sp per sprint x 3 sprint. Terpakai = `committed` di roster, yang sama dengan jumlah SP issue snapshot per orang per sprint (Sprint 26.22: LOG-3405 8 SP Agung; LOG-3403 5 SP + LOG-3409 3 SP Laras).

| Tim | Kapasitas | Sudah terpakai | Sisa | Usulan pekerjaan baru | Utilisasi |
|---|---|---|---|---|---|
| Mobile (Rizky, Bayu) | 69 SP | 0 SP | 69 SP | LOG-3412 = 3 SP | 4% |
| Backend (Agung, Fikri) | 69 SP | 8 SP | 61 SP | 5+5+5+5+3+2+2+3 = 30 SP (fase 1) | 55% |
| Frontend (Laras) | 30 SP | 8 SP | 22 SP | 5+2 = 7 SP | 50% |

Utilisasi = (terpakai + baru) / kapasitas, jadi mobile (0+3)/69, backend (8+30)/69, frontend (8+7)/30. Semua muat. Story SOP Shopee (S11, 5 SP, Sprint 27.01, fase 2) di luar jendela ini dan sengaja tidak masuk tabel Stories di PRD. Rizky cuti 21-31 Desember, jatuh di masa freeze, jadi tidak mengurangi kapasitas sebelum rilis.
