# PRD: AI Assistant di Driver App

| | |
|---|---|
| Product Manager | Nadia Putri |
| Status | Draft (disusun AI, menunggu review) |
| Repo terkait | logisly/driver-app, logisly/platform-api, logisly/ops-console |
| Target rilis | MVP siap di staging 18 Des 2026 (Sprint 26.24); pilot 50 driver mulai 11 Jan 2027 (Sprint 27.01) |
| Terakhir diupdate | 6 November 2026 |

## 1. Latar belakang
Follow-up driver saat ini lewat agent teks di WhatsApp dan hanya 20-30% kasus selesai tanpa tim ops. Agent tidak bisa membaca voice note dan foto, padahal laporan driver sebagian besar dalam bentuk itu. Biaya WhatsApp naik sekitar 5 kali sejak tarif per pesan, dan naik 18% lagi di Oktober. Telepon ke driver masih manual lewat PBX. *(01 request, 04 chat)*

## 2. Masalah
- Driver: harus mengetik saat menyetir; pertanyaan agent berulang dan kaku.
- Ops: sekitar 140 tiket follow-up manual per hari; voice note diputar satu per satu (ops-console#64).
- Bisnis: biaya WhatsApp terus naik dan nomor driver sering berganti.

## 3. Tujuan dan non-tujuan
- Tujuan: driver bisa melapor ke AI assistant di app lewat teks, voice note, foto atau video, dan sebagian besar kasus selesai tanpa ops.
- Non-tujuan MVP: telepon otomatis dari AI (fase 2, keputusan product sync 3 Nov); mematikan WhatsApp sepenuhnya.

## 4. User dan skenario
| User | Skenario utama |
|---|---|
| Driver | Ditanya AI karena titik tidak bergerak, membalas dengan voice note "ban pecah, lagi ganti" |
| Ops | Menerima tiket yang sudah berisi ringkasan, transkrip dan penjelasan foto |
| Head of Ops | Melihat tiket manual dan biaya WhatsApp turun |

## 5. Requirement
### 5.1 Fungsional
| ID | Requirement | Prioritas | Sumber |
|---|---|---|---|
| FR-1 | Chat dengan AI assistant per order di driver app | P1 | 01, 02 |
| FR-2 | Kirim voice note, foto dan video pendek dengan keterangan | P1 | 01, 02 |
| FR-3 | AI memahami voice note dan foto, membalas dengan bahasa natural | P1 | 01 |
| FR-4 | AI menyapa driver duluan saat ada anomali tracking | P1 | 01, platform-api#135 |
| FR-5 | Maksimal 2 follow-up tanpa balasan, lalu tiket | P1 | 04 |
| FR-6 | Tiket eskalasi berisi ringkasan, transkrip dan penjelasan foto | P1 | 01, ops-console#64 |
| FR-7 | Telepon otomatis saat driver tidak membalas | P3 (fase 2) | 01, 02 |

### 5.2 Non-fungsional
| ID | Requirement | Catatan |
|---|---|---|
| NFR-1 | Jalan di Android 8 ke atas | 9% driver aktif masih Android 8 (driver-app#150) |
| NFR-2 | Hemat data: voice note 16 kbps, foto maks 300 KB, antrian kirim offline | Sinyal lemah di Pantura (01) |
| NFR-3 | Bahasa Indonesia informal | Jawa dan Sunda: pertanyaan terbuka #2 |

## 6. Metrik keberhasilan
| Metrik | Baseline | Target | Cara ukur |
|---|---|---|---|
| Kasus selesai tanpa ops | 20-30% | 60% | Tiket per follow-up, ops console |
| Tiket follow-up manual per hari | sekitar 140 | di bawah 60 | Ops console |
| Biaya WhatsApp per bulan | belum ada angka | turun 50% setelah 3 bulan | Dashboard biaya (data-pipelines#22) |

## 7. Dependensi
- platform-api#131 push notification: **blocked** menunggu approval security. Fallback: cek pesan tiap 60 detik saat app terbuka.
- platform-api#135 webhook anomali tracking.

## 8. Risiko
| Risiko | Dampak | Mitigasi |
|---|---|---|
| #131 tidak selesai sebelum 18 Des | Driver tidak tahu ada pesan baru | Eskalasi minggu ini; fallback polling |
| Rilis tepat sebelum code freeze | Tidak bisa perbaiki bug selain P0 | Pilot setelah freeze, 11 Jan |
| Driver tetap memilih WhatsApp | Adopsi rendah | Pilot di satu koridor, ukur adopsi mingguan |

## 9. Rencana rilis
- Sprint 26.23: layar chat, voice note, endpoint chat, pemahaman voice note dan foto.
- Sprint 26.24: foto/video, notifikasi, mode hemat data, follow-up proaktif, tiket eskalasi, rencana uji pilot. Siap di staging 18 Des.
- Sprint 27.01: pilot 50 driver koridor Cikarang - Semarang mulai 11 Jan.
- Sprint 27.02: fase 2, telepon otomatis.

## 10. Pertanyaan terbuka
1. Tanggal: setuju staging 18 Des dan pilot 11 Jan? (Hadi, Nadia)
2. Perlu dukungan bahasa Jawa dan Sunda di MVP? Butuh contoh percakapan nyata. (Hadi)
3. Baseline biaya WhatsApp Agu-Okt 2026. (Wulan)
