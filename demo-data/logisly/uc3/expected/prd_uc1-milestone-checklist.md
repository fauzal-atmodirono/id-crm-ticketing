# PRD: Checklist follow-up driver per milestone

| | |
|---|---|
| Product Manager | Nadia Putri |
| User | Allocation, CT, MM, Warehouse, Driver |
| UAT | Sprint 26.24 (7-18 Desember 2026) |
| Target Release | 18 Desember 2026 (akhir Sprint 26.24), pilot shipper FMCG |

## Table of Content

1. Background
2. Impact
3. Requirements & Timeline
4. Flow
5. Timeline
6. Mock Up
7. Stories
8. Pertanyaan terbuka

## 1. Background

1. Bot follow-up WhatsApp menjalankan checklist SOP sebagai skrip: bot bertanya hal yang sudah ada di sistem (plat, titik muat, status driver app) dan driver rata-rata ditanya 18 kali per trip.
2. Foto dari driver (foto unit, surat jalan, POD) diabaikan, sehingga bot bertanya ulang walaupun bukti sudah dikirim.
3. Bot lama (LOG-3398) mengirim "Posisi di mana pak?" tiap 30 menit sepanjang malam, tanpa jam tenang.
4. Item yang tidak OK tidak otomatis sampai ke tim yang tepat, jadi Allocation dan CT follow-up manual. Hanya 40% kasus terlambat terdeteksi sebelum muat.
5. Biaya WhatsApp sekitar Rp 38 juta/bulan, sebagian besar pesan follow-up tracking.

## 2. Impact

Goals:
1. Kurangi pertanyaan ke driver per trip dari 18 menjadi 6 (turun 67%). Rumus: Number of pertanyaan agent ke driver / number of trip.
2. Naikkan kasus terlambat yang terdeteksi sebelum muat dari 40% menjadi 80%. Rumus: Number of kasus terlambat terdeteksi sebelum muat / number of kasus terlambat.
3. Setiap item tidak OK menjadi tiket ke tim yang tepat. Rumus: Number of item tidak OK dengan tiket bertim / number of item tidak OK.

| Group | Current | Proposed |
|---|---|---|
| Allocation | Cek plat, titik muat, foto unit dan surat jalan manual per trip | Item sistem terisi otomatis; hanya item tidak OK (plat, lokasi, foto unit, surat jalan, DO) masuk sebagai tiket |
| CT | Telepon dan chat ke driver untuk posisi, ETA dan kendala | Tiket hanya untuk trip terlambat atau butuh unit pengganti |
| Sales | Tidak tahu alamat tujuan berbeda dari PF | Drop address yang tidak cocok dengan PF diteruskan lewat tiket Allocation (shipper/sales/TS diberi tahu) |
| MM | Menagih POD lewat chat | Tiket ke MM saat POD / surat jalan bertanda tangan tidak terlihat di foto |
| Warehouse | Tidak tahu kapan truk selesai muat dan bongkar | Tiket ke Warehouse saat driver tidak memberi waktu muat atau bongkar |
| Driver | Ditanya berulang, foto diabaikan, pesan masuk malam hari | Maksimal 2 item per pesan, foto dibaca, pesan ditahan 22:00–05:00 WIB |

Metric per tim:
- Allocation: jumlah tiket per trip; waktu dari item tidak OK sampai tiket dibuat.
- CT: jumlah follow-up manual per hari.
- MM: jumlah POD yang lengkap per trip.
- Warehouse: jumlah trip dengan waktu muat dan bongkar tercatat.
- Driver: jumlah pertanyaan per trip (target 6); jumlah pesan yang dikirim di jam tenang (target 0).
- Data: biaya WhatsApp per bulan (baseline Rp 38 juta, target belum ditetapkan).

## 3. Requirements & Timeline

| Category | Details | PIC | Timeline |
|---|---|---|---|
| Release | Pilot shipper FMCG dengan trigger milestone manual dan dari app | Nadia Putri | 18 Desember 2026 |
| Release | Trigger milestone otomatis dari GPS, menunggu LOG-3405 | Agung Setiawan | Setelah LOG-3405 Done |
| Sosialisasi | Briefing tim Allocation, CT, MM dan Warehouse soal tiket bertim | Hadi Pranoto | Sprint 26.24 |
| Data Prep | Master driver dan nomor WhatsApp driver FMCG diperbarui | Dimas Ops | Sebelum 14 Desember 2026 |
| Data Prep | Baseline biaya WhatsApp 3 bulan | Wulan Sari | Sprint 26.23 |
| Product | Checklist per milestone sesuai SOP, 6 milestone, item sumber system / photo / driver, jam tenang 22:00–05:00 WIB, tiket bertim, ringkasan checklist selesai | Nadia Putri | Sprint 26.23 - 26.24 |

## 4. Flow

1. Milestone trip berubah (assigned, menuju muat, di lokasi muat, dalam perjalanan, di lokasi bongkar, selesai bongkar) lewat GPS (fase berikutnya), driver app atau operator di console.
2. Sistem mengisi item checklist yang sumbernya `system` dari data trip.
3. Foto dari driver dibaca sebagai bukti untuk item `photo`.
4. Driver menerima satu pesan pembuka yang hanya memuat item `photo` / `driver` yang masih terbuka dan perbaikan yang harus dilakukan driver (mis. link install driver app). Tidak ada yang terbuka: tidak ada pesan.
5. Agent menjawab pertanyaan driver lebih dulu dari data trip, mencentang beberapa item dari satu balasan, tidak menanyakan ulang item yang sudah dijawab, dan paling banyak 2 item per pesan.
6. Item tidak OK menjadi tiket dengan label `logisly-checklist` + `team-<tim>`, prioritas tinggi, berisi item, bukti dan trip.
7. Saat checklist satu milestone lengkap, percakapan mendapat catatan ringkasan.
8. Antara 22:00 dan 05:00 WIB pesan ke driver ditahan; checklist tetap dicatat dan operator bisa memaksa kirim dari console.

Checklist per milestone (dari SOP; tim = tujuan tiket kalau tidak OK):

| Milestone | Item | Sumber | Tim kalau tidak OK |
|---|---|---|---|
| assigned | Plat driver | system | Allocation |
| assigned | Titik muat dan bongkar di driver app | system | Allocation |
| assigned | Unit kosong | driver | dicatat saja |
| at_pickup | Foto unit (plat, jam, lokasi) | photo | Allocation |
| at_pickup | Jam muat | driver | Warehouse |
| at_pickup | Driver app aktif | system | driver diberi link install |
| at_pickup | DO di driver app | system | Allocation |
| in_transit | Foto surat jalan | photo | Allocation |
| in_transit | Alamat bongkar sesuai PF | system | Allocation |
| in_transit | ETA bongkar | driver | dicatat saja |
| in_transit | Trip OK (GPS dan ETA) | system | CT |
| in_transit | Kendala | driver | CT |
| at_drop | Foto unit di titik bongkar | photo | Allocation |
| at_drop | Jam bongkar | driver | Warehouse |
| unloaded | Bongkar selesai | driver | dicatat saja |
| unloaded | POD / surat jalan bertanda tangan | photo | MM |
| unloaded | POD diupload di driver app | system | driver diingatkan |

Item khusus Shopee (ID driver Shopee, status onsite, bongkar terdaftar di aplikasi Shopee) masuk fase 2 (Sprint 27.01, setelah code freeze).

## 5. Timeline

| Category | Items | PIC | Timeline | Status |
|---|---|---|---|---|
| Dependency | GPS geofence event (LOG-3405) | Agung Setiawan | Sprint 26.22 | In Progress |
| Product | Model milestone, pengisian item system, tiket bertim, jam tenang | Agung Setiawan | Sprint 26.23 | To Do |
| Product | Agent baca foto dan pesan pembuka | Fikri Hakim | Sprint 26.23 - 26.24 | To Do |
| Product | Stepper checklist di ops console | Laras Ayu | Sprint 26.24 | To Do |
| Product | Upload POD di driver app (LOG-3412) | Rizky Ramadhan | Sprint 26.23 | To Do |
| UAT | UAT dengan Allocation dan CT | Tegar Wijaya | Sprint 26.24 | To Do |
| Release | Pilot FMCG | Nadia Putri | 18 Desember 2026 | To Do |
| Release | Shopee SOP (fase 2) | Nadia Putri | Sprint 27.01 | To Do |

## 6. Mock Up

TBD. Console menampilkan stepper milestone dan checklist per trip dengan sumber dan status tiap item.

## 7. Stories

| Jira # | Description | Acceptance |
|---|---|---|
| LOG-TBD | [BE] As Logisly, I want each trip to move through six milestones set by gps, app or manual so that follow-up starts at the right step | 1. Enam milestone tersedia 2. Pengubah milestone tercatat (gps / app / manual) |
| LOG-TBD | [BE] As Allocation, I want system checklist items filled from trip data so that drivers are not asked what the system knows | 1. Item sumber system terisi saat milestone berubah 2. Item Shopee dilewati untuk trip non-Shopee |
| LOG-TBD | [AI Agent] As Driver, I want my photos read as evidence so that I am not asked again for what I already sent | 1. Foto unit, surat jalan dan POD dibaca 2. Item yang sudah dijawab tidak ditanya ulang |
| LOG-TBD | [AI Agent] As Driver, I want one short message with at most two open items so that I am not flooded with questions | 1. Pesan pembuka hanya memuat item terbuka 2. Maksimal 2 item per pesan 3. Pertanyaan driver dijawab dulu dari data trip |
| LOG-TBD | [BE] As Allocation, I want every not-OK item to become a ticket to the right team so that nobody follows up manually | 1. Tiket berlabel logisly-checklist dan team-tim 2. Prioritas tinggi 3. Berisi item, bukti dan trip |
| LOG-TBD | [BE] As Driver, I want no message between 22:00 and 05:00 WIB so that I can rest | 1. Pesan ditahan di jam tenang 2. Checklist tetap tercatat |
| LOG-TBD | [BE] As CT, I want a summary note when a milestone checklist is complete so that I can see the result at a glance | 1. Catatan ringkasan dibuat sekali per milestone |
| LOG-TBD | [Ops Console] As Allocation, I want to see each trip's milestone stepper and checklist so that I can check status per item | 1. Stepper enam milestone 2. Tiap item menampilkan sumber dan status |
| LOG-TBD | [Ops Console] As CT, I want to set the milestone manually and force-send the driver message so that I can work when GPS is not ready | 1. Pilihan gps / app / manual 2. Tombol kirim paksa di jam tenang |
| LOG-3412 | [Driver App] As Driver, I want to upload the POD photo after unloading so that the shipper gets proof of delivery faster | 1. Tombol upload POD setelah bongkar selesai 2. Foto terkirim dengan jam dan plat |
| LOG-TBD | [BE] As Logisly, I want milestone to change automatically from GPS geofence events so that nobody sets it by hand | 1. Milestone titik muat dan bongkar berubah dari event geofence 2. Berjalan setelah LOG-3405 Done |

## 8. Pertanyaan terbuka

1. Target pilot: 4 Desember 2026 (request Ops) atau 18 Desember 2026 (product sync)? Usulan: 18 Desember. Yang memutuskan: Hadi Pranoto dan Nadia Putri.
2. Jam tenang: 21:00–06:00 (request Ops) atau 22:00–05:00 (product sync dan fitur yang sudah dibangun)? Usulan: 22:00–05:00. Yang memutuskan: Hadi Pranoto.
3. Apakah shipper (PT Sinar Pangan Nusantara) akan menerima notifikasi bongkar, atau cukup tiket ke MM? Fitur ini tidak mengirim pesan ke shipper. Yang memutuskan: Sari Indah dan Nadia Putri.
4. Target penurunan biaya WhatsApp belum ada; baseline Rp 38 juta/bulan. Yang memutuskan: Hadi Pranoto, data dari Wulan Sari.
5. Tenggat 31 Desember 2026 jatuh di masa code freeze (21 Desember 2026 s.d. 3 Januari 2027). Apakah cukup kalau live 18 Desember? Yang memutuskan: Sari Indah.
