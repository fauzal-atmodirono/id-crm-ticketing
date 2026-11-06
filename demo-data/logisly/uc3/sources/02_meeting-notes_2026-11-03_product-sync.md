# Product Sync Mingguan, Selasa 3 November 2026

Hadir: Nadia Putri (Product Manager), Hadi Pranoto (Head of Operations), Rizky Ramadhan (Mobile Lead), Agung Setiawan (Backend Lead), Wulan Sari (Data Analyst), Sari Indah (Customer Success)

## 1. AI assistant di driver app

- Hadi presentasi request dari Ops (dokumen "Request: AI Assistant di Driver App"). Tujuan utama: kurangi ketergantungan ke WhatsApp dan kurangi tiket follow-up manual.
- Disepakati MVP = chat + voice note + kirim foto di driver app, dijawab AI. **Telepon otomatis dari AI masuk fase 2**, belum di MVP.
- Hadi minta MVP sudah live **18 Desember 2026**, sebelum puncak pengiriman akhir tahun. Nadia catat, tapi belum dicek ke kapasitas tim.
- Rizky: chat in-app butuh push notification. Service push notif (issue #131 di platform-api) masih blocked karena credential FCM production belum ada. Tanpa itu driver tidak tahu ada pesan baru.
- Rizky: tim mobile cuma 2 orang (Rizky + Bayu), dan Rizky cuti 21-31 Desember.
- Agung: backend AI agent bisa pakai Gemini di Google Cloud, sedang dibahas dengan partner. Endpoint chat butuh kira-kira 1 sprint.
- Action: Nadia bikin PRD + breakdown task minggu ini, masukkan ke GitHub.

## 2. e-POD (bukti terima digital)

- Sari: shipper PT Sumber Pangan Nusantara minta foto POD dan tanda tangan penerima kelihatan di shipper portal, untuk audit akhir tahun. Mereka minta sebelum 31 Desember.
- Nadia: rasanya sudah ada issue upload foto POD di driver-app. Perlu dicek supaya tidak dobel.
- Action: Sari kirim email detail request shipper ke Nadia.

## 3. Biaya WhatsApp

- Wulan: tagihan WhatsApp Oktober naik lagi. Belum ada dashboard yang memecah biaya per kategori pesan atau per driver.
- Hadi: begitu chat in-app jalan, pesan follow-up rutin harus pindah dari WhatsApp ke push notif di app.
- Action: Wulan kumpulkan data biaya 3 bulan terakhir. Belum diputuskan masuk sprint kapan.

## 4. Lain-lain

- Code freeze production akhir tahun tetap 21 Desember 2026 s.d. 3 Januari 2027 (hanya hotfix).
- Sprint planning berikutnya Senin 9 November.
