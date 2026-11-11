# Product Sync Mingguan, Selasa 10 November 2026

Hadir: Nadia Putri (Product Manager), Hadi Pranoto (Head of Operations), Rizky Ramadhan (Mobile Lead), Agung Setiawan (Backend Lead), Laras Ayu (Frontend), Wulan Sari (Data Analyst), Sari Indah (Customer Success), Dimas Ops (CT)

## 1. Follow-up driver per milestone (UC1)

- Hadi presentasi request Ops (dokumen "Request: Follow-up driver per milestone"). Tujuan: bot berhenti bertanya hal yang sudah ada di sistem, foto driver dibaca, item tidak OK jadi tiket ke tim yang tepat.
- Disepakati cakupan pilot = checklist SOP untuk shipper FMCG. Item khusus Shopee masuk fase 2, Sprint 27.01, setelah code freeze.
- Target release disepakati **18 Desember 2026** (akhir Sprint 26.24). Request Ops menyebut 4 Desember, tapi Nadia bilang tim belum bisa menjamin tanggal itu dan belum dicek ke kapasitas.
- Jam tenang: setelah diskusi dengan CT disepakati pesan ke driver ditahan **22:00–05:00 WIB**. Angka 21:00–06:00 di request Ops dianggap terlalu lebar karena banyak trip antar-kota berangkat jam 05:00.
- Agung: trigger milestone otomatis dari GPS bergantung ke LOG-3405 (event geofence titik muat dan bongkar), yang masih In Progress di Sprint 26.22. Sampai selesai, milestone diubah manual dari ops console atau lewat driver app.
- Dimas: bot lama (LOG-3398) mengirim pengingat tiap 30 menit sepanjang trip. Itu harus diganti, bukan ditambah.
- Rizky: pekerjaan mobile untuk fitur ini kecil, hanya upload foto POD (LOG-3412). Rizky cuti 21-31 Desember.
- Action: Nadia bikin PRD + breakdown story di Jira minggu ini.

## 2. Update bongkar dan POD untuk shipper

- Sari: PT Sinar Pangan Nusantara komplain update bongkar dan bukti terima terlambat, minta rutin sebelum 31 Desember 2026.
- Nadia: ada dua issue upload foto POD di driver app (LOG-3412 dan LOG-3417), kemungkinan dobel. Perlu dicek.
- Belum diputuskan apakah shipper akan menerima notifikasi bongkar dari kita, atau cukup tiket ke tim MM.
- Action: Sari kirim email detail komplain ke Nadia (sudah, 9 November).

## 3. Biaya WhatsApp

- Wulan: tagihan WhatsApp sekitar Rp 38 juta per bulan. Dengan checklist baru, pesan rutin per trip harus turun.
- Belum ada target angka penurunan. Action: Wulan siapkan baseline tiga bulan terakhir.

## 4. Lain-lain

- Code freeze production akhir tahun tetap 21 Desember 2026 s.d. 3 Januari 2027 (hanya hotfix P0).
- Sprint planning berikutnya Senin 16 November.
