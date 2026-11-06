# Laporan konsistensi: AI Assistant di Driver App dan e-POD

Kunci jawaban demo. Ini temuan yang seharusnya dilaporkan agent setelah membaca keempat sumber di `uc3/sources/`, snapshot GitHub, roster dan kalender. Setiap temuan menyebut sumbernya dan siapa yang memutuskan.

## Konflik yang perlu diputuskan

| # | Temuan | Sumber | Usulan | Yang memutuskan |
|---|---|---|---|---|
| 1 | **Tanggal rilis bertabrakan.** Request Ops: pilot 50 driver mulai minggu kedua Januari 2027. Product sync: MVP live 18 Desember 2026. | 01 request, 02 notes | MVP siap di staging akhir Sprint 26.24 (18 Des). Pilot ke 50 driver mulai Sprint 27.01 (11 Jan), sesuai request Ops dan setelah code freeze. | Hadi, Nadia |
| 2 | **18 Desember adalah hari terakhir sebelum code freeze** (21 Des s.d. 3 Jan, hanya hotfix P0). Rilis ke production di tanggal itu tidak menyisakan waktu perbaikan. | 02 notes, calendar | Ikut usulan #1: jangan rilis ke driver sebelum freeze. | Nadia |
| 3 | **Telepon otomatis: fase 1 atau 2?** Request Ops memasukkan telepon otomatis (setelah 5 menit tanpa balasan) di fase 1. Product sync menyepakati fase 2. | 01 request, 02 notes | Fase 2, sesuai keputusan product sync. Story dibuat di backlog (milestone Sprint 27.02). | Hadi |
| 4 | **Dependensi blocked tanpa ETA.** Chat in-app butuh push notification (platform-api#131), yang blocked menunggu approval security untuk service account FCM sejak 6 Okt. | 02 notes, #131 | Eskalasi approval ke security minggu ini. Fallback MVP: app cek pesan baru tiap 60 detik saat terbuka. | Agung, Nadia |
| 5 | **Aturan follow-up berbeda.** Request Ops: telepon setelah 5 menit tanpa balasan. Chat Ops: maksimal 2 follow-up tanpa balasan lalu langsung tiket. | 01 request, 04 chat | Satukan jadi satu urutan: chat, follow-up ke-2, (fase 2: telepon), lalu tiket. Batas waktu tiap langkah dikonfigurasi. | Hadi |

## Duplikat dan issue yang perlu diperbarui

| # | Temuan | Usulan |
|---|---|---|
| 6 | driver-app#148 "Foto bukti terima di app driver" (dibuat CS, 20 Okt) adalah duplikat driver-app#142 "Upload foto POD setelah bongkar muat" (14 Agu). | Tutup #148 dengan label `status:duplicate`, tautkan ke #142. |
| 7 | Acceptance criteria #142 hanya foto. Shipper (email 03) juga minta nama penerima, jam terima, tanda tangan, dan download per bulan. | Tambah AC di #142, naikkan ke P1, milestone Sprint 26.23. |
| 8 | Laras sudah menulis di #142 bahwa shipper portal butuh endpoint untuk menampilkan foto, tapi issue-nya belum ada. | Buat issue baru di shipper-portal dan platform-api (lihat backlog.json). |
| 9 | ops-console#64 (transkrip voice note di tiket) masih P3 tanpa milestone, padahal masuk lingkup AI assistant. | Naikkan ke P1, gabungkan ke epic AI assistant. |
| 10 | Data billing WhatsApp: chat Ops bilang BSP hanya memberi total harian, tapi data-pipelines#22 mencatat export CSV per template dan penerima bisa diminta lewat tiket support. | Minta export CSV ke BSP; ini membuka dashboard biaya per kategori. |

## Hal yang belum lengkap

| # | Temuan | Yang perlu ditanyakan |
|---|---|---|
| 11 | Target "biaya WhatsApp turun 50% setelah 3 bulan" belum punya baseline angka. | Wulan: total biaya Agu-Okt 2026 per bulan. |
| 12 | Dukungan bahasa Jawa dan Sunda diminta di request tapi tidak dibahas di product sync. | Hadi: perlu contoh percakapan nyata untuk uji. |
| 13 | Request e-POD dari PT Sumber Pangan Nusantara punya tenggat 31 Desember, yang jatuh di masa code freeze. | Sari: apakah cukup kalau fitur live 18 Des, atau ada data lama 2026 yang juga harus diunggah? |

## Kapasitas (Sprint 26.22 s.d. 26.24, sebelum freeze)

| Tim | Kapasitas | Sudah terpakai | Sisa | Usulan pekerjaan baru | Utilisasi |
|---|---|---|---|---|---|
| Mobile (Rizky, Bayu) | 69 SP | 24 SP | 45 SP | MVP 26 + e-POD 5 = 31 SP | 69% |
| Backend (Agung, Fikri) | 69 SP | 18 SP | 51 SP | MVP 25 + e-POD 3 = 28 SP | 55% |
| Frontend (Laras) | 30 SP | 13 SP | 17 SP | e-POD 8 + #64 3 = 11 SP | 65% |

Muat, selama telepon otomatis tetap di fase 2. Kalau masuk fase 1 (+8 SP mobile, +8 SP backend), mobile naik ke 87% dan tidak ada ruang untuk bug dari pilot. Rizky cuti 21-31 Des, jatuh di masa freeze, jadi tidak mengurangi kapasitas sebelum rilis.
