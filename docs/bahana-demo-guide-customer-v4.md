# Demo Personalisasi AI — Coba Sendiri (v4)

**Disiapkan oleh Devoteam untuk Bahana Sekuritas**

Demo yang bisa Anda coba langsung dari ponsel sendiri, lewat WhatsApp. Sekitar
tujuh menit per nasabah, atau sekitar dua puluh lima menit untuk ketiga
percakapan lengkap di §6.

> **Apa yang baru di v4.**
>
> **Percakapan kini utuh sampai selesai — termasuk bagian petugas manusia.**
> Pada v3, setiap percakapan berhenti di kalimat *"percakapan diserahkan ke
> petugas manusia"*, dan pembaca tidak pernah melihat apa yang terjadi
> sesudahnya. Itu justru separuh yang menjual produknya. Di v4 setiap
> percakapan berlanjut: apa yang dilihat petugas ketika percakapan mendarat di
> mejanya, apa yang ia katakan, dan bagaimana percakapan itu ditutup.
>
> **§5 baru — apa yang dilihat petugas.** Sidebar, label, dan status
> percakapan, yaitu bukti bahwa petugas mewarisi konteks yang sama dengan yang
> dipakai asisten, bukan sekadar transkrip.
>
> **§8 baru — mengembalikan percakapan ke asisten.** Setiap percakapan di §6
> berakhir dipegang manusia, jadi asisten **tidak akan** menjawab percakapan
> berikutnya dari ponsel yang sama sampai direset. Ini penyebab nomor satu
> "kok bot-nya tidak membalas" saat demo.
>
> **§2 baru — pemeriksaan sepuluh menit sebelum demo.**
>
> Panduan v1–v3 tetap berlaku dan tidak dibatalkan.

---

## 1. Sebelum mulai — tiga hal yang perlu diketahui

**Seluruh data nasabah bersifat fiktif.** Setiap nasabah dalam demo ini dibuat
secara sintetis oleh kami. Tidak ada data Bahana apa pun yang digunakan.
Portofolio, saldo, dan kepemilikan saham yang Anda lihat adalah milik orang
yang tidak pernah ada.

**Ini demo, bukan produk jadi.** Tujuannya menunjukkan bagaimana
personalisasinya bekerja, agar Anda bisa menilai pendekatannya sebelum ada yang
dibangun untuk sungguhan.

**Tidak ada rekomendasi investasi di sini.** Asisten ini sengaja dirancang agar
tidak dapat merekomendasikan efek tertentu. Jika Anda menanyakan saham atau IPO
apa yang sebaiknya dibeli, ia akan menolak dan menyerahkan percakapan kepada
petugas — dan justru perilaku itulah salah satu hal yang paling layak Anda uji.

---

## 2. Pemeriksaan sepuluh menit sebelum demo

Jalankan ini **sebelum** ada penonton. Tiga dari empat kegagalan demo yang
pernah kami alami tertangkap di sini.

| # | Periksa | Cara | Yang benar |
|---|---|---|---|
| 1 | Percakapan sebelumnya sudah direset | §8 | status `pending`, tanpa assignee |
| 2 | Asisten membalas sama sekali | kirim `halo [moderat]` | ada balasan dalam ±10 detik |
| 3 | Profil terbaca | kirim `bagaimana profile saya?` | menyebut Rp 46.000.000 **dan** sektor Keuangan/Infrastruktur |
| 4 | Sidebar terisi di CRM | buka percakapannya | 11 atribut, termasuk *Holdings by Sector* |

**Kalau langkah 3 tidak menyebut sektor**, kontaknya basi — jalankan ulang
`bahana_bq_to_crm_sync.py`. **Kalau langkah 2 diam saja**, hampir pasti
percakapannya masih berstatus `open` dari sesi sebelumnya; lihat §8 sebelum
menduga hal lain. Asisten yang benar-benar berhenti karena aturan tidak bisa
dibedakan dari asisten yang rusak, jadi periksa statusnya lebih dulu.

---

## 3. Cara memulai

**1. Kirim pesan WhatsApp ke nomor ini:**

# +1 629 284 3510

**2. Pesan pertama Anda harus diakhiri dengan tanda di dalam kurung siku.**

Tanda itu menentukan Anda berperan sebagai nasabah fiktif yang mana. Contoh:

```
bagaimana profile saya? [moderat]
```

Hanya itu aturannya. Setelah pesan pertama yang bertanda, Anda bisa mengobrol
seperti biasa — Anda tetap menjadi nasabah tersebut sampai mengirim tanda yang
berbeda.

Anda boleh menulis dalam Bahasa Indonesia maupun Inggris; asisten akan menjawab
dalam bahasa yang Anda pakai.

> **Jika tandanya lupa dicantumkan**, asisten akan menganggap Anda nasabah baru
> yang belum ia kenal sama sekali, sehingga jawabannya menjadi umum. Kirim
> pertanyaan apa pun dengan tanda di akhir, dan profilnya langsung terbaca.

---

## 4. Tiga nasabah yang bisa Anda perankan

| Tanda | Anda menjadi | Saldo RDN | Kepemilikan | Transaksi terakhir |
|---|---|---|---|---|
| `[konservatif]` | Sari Wijaya — hati-hati, dananya mengendap | Rp 82.500.000 | belum ada | 312 hari lalu |
| `[moderat]` | Budi Santoso — investor seimbang | Rp 46.000.000 | BBCA, BBRI, TLKM | 190 hari lalu |
| `[agresif]` | Rizki Pratama — trader aktif | Rp 240.000.000 | ANTM, BBRI, ICBP, PGAS | 3 hari lalu |

Ganti kapan saja dengan menaruh tanda yang berbeda di akhir pesan — **tetapi
baca §8 dulu**, karena percakapan yang sudah diserahkan ke manusia tidak akan
dijawab lagi sampai direset.

---

## 5. Apa yang dilihat petugas ketika percakapan mendarat

Bagian ini yang membuat separuh kedua §6 masuk akal. Buka CRM di layar kedua.

| Di mana | Yang seharusnya ada |
|---|---|
| Status percakapan | `open` (sebelumnya `pending`) — dan asisten sudah berhenti |
| Label | `segmen-<profil>`, `offer-staged`, dan `nasabah-prioritas` untuk dua pita AUM teratas — dipasang oleh aturan otomatis CRM, tanpa AI |
| Sidebar kontak | 11 atribut, termasuk **Holdings by Sector** |
| Pesan terakhir ke nasabah | kalimat serah terima dari asisten |

**Sidebar itulah poin yang layak ditahan sebentar di layar.** Petugas mewarisi
profil yang sama persis dengan yang dipakai asisten untuk menyusun jawabannya.
Ia tidak mulai dari nol, dan tidak perlu meminta nasabah mengulang apa pun.

**Yang membuat asisten berhenti bukanlah instruksi.** Asisten hanya bekerja
pada percakapan berstatus `pending`. Saat serah terima, statusnya berubah
menjadi `open` — dan sejak detik itu ia secara struktural tidak lagi memenuhi
syarat untuk menjawab. Bukan karena ia diminta sopan, melainkan karena ia tidak
lagi dipanggil.

---

## 6. Tiga percakapan lengkap, dari "halo" sampai selesai

### 6.0 Cara membaca bagian ini

Setiap percakapan melewati enam babak:

1. **Sapaan** — apakah ia langsung berjualan? (Seharusnya tidak.)
2. **Pertanyaan profil** — apakah ia benar-benar mengenal nasabah ini?
3. **Keluhan** — apakah penawarannya muncul secara wajar?
4. **Penolakan** — nasabah menginginkan sesuatu yang lain. Babak terpenting.
5. **Penyerahan ke manusia** — dengan alasan berbeda di tiap percakapan.
6. **Petugas mengambil alih** — babak yang baru di v4.

> **Catatan kejujuran, dan ini penting.**
>
> **Baris nasabah dan asisten adalah keluaran asli**, direkam 26 Agustus 2026.
> Kalimat yang Anda terima **tidak akan sama persis** — model menyusun jawaban
> dari awal setiap kali. Yang harus konsisten adalah **perilakunya**: fakta yang
> dikutip, produk yang boleh dan tidak boleh disebut, dan titik ia menyerah.
>
> **Baris petugas adalah naskah untuk Anda, bukan keluaran mesin.** Tidak ada
> yang menghasilkannya. Naskah itu ditulis agar demo punya akhir yang utuh dan
> agar penguji tahu seperti apa "ditangani dengan benar" dari sisi CRM.

---

### 6.1 `[konservatif]` — Sari Wijaya · batas kesesuaian produk

Dananya mengendap Rp 82,5 juta selama 312 hari, belum memiliki produk apa pun.
Berakhir dengan **nasabah sendiri yang meminta bertemu relationship manager**.

> **Anda:** halo
>
> **Asisten:** Halo juga! Ada yang bisa saya bantu?

*Tidak ada penawaran produk. Sapaan dijawab sebagai sapaan.*

> **Anda:** bagaimana profil investasi saya?
>
> **Asisten:** Halo! Berdasarkan catatan kami, profil investasi Anda adalah
> konservatif. Dengan dana sebesar Rp 82.500.000 yang saat ini belum
> ditempatkan, Reksa Dana Pasar Uang bisa menjadi pilihan yang menarik untuk
> Anda. Apakah Anda tertarik untuk mengetahui lebih lanjut?

> **Anda:** dana saya kok mengendap terus di RDN ya, sayang tidak berkembang
>
> **Asisten:** Saya memahami kekhawatiran Anda. Dana yang mengendap di RDN
> memang kurang optimal. Reksa Dana Pasar Uang cocok untuk nasabah dengan profil
> konservatif seperti Anda, agar dana bisa lebih berkembang sambil tetap menjaga
> risiko.

> **Anda:** reksa dana pasar uang itu apa sih?
>
> **Asisten:** Reksa Dana Pasar Uang adalah jenis reksa dana yang berinvestasi
> pada instrumen pasar uang dan obligasi jangka pendek. Tujuannya menjaga
> stabilitas modal dan memberikan likuiditas yang tinggi, sehingga cocok untuk
> Anda yang memiliki profil risiko konservatif.

> **Anda:** sebenarnya saya mau yang return-nya tinggi, saham gitu
>
> **Asisten:** Untuk investasi dengan return tinggi seperti saham, itu tidak
> sesuai dengan profil risiko konservatif Anda. Jika Anda ingin meninjau ulang
> profil risiko Anda, saya bisa bantu agar manajer hubungan kami dapat
> menghubungi Anda.

**← periksa di sini.** Saham diminta dan **tidak** ditawarkan. Ini bukan sikap
hati-hati: tidak ada satu pun produk saham di baris miliknya pada tabel
kesesuaian produk. Perhatikan juga ia **tidak** buntu — ia menawarkan
peninjauan profil, satu-satunya jalan yang benar menuju apa yang ia minta.

> **Anda:** kalau begitu saya mau bicara dengan relationship manager, bisa
> dijadwalkan?
>
> **Asisten:** Baik, saya hubungkan Anda dengan tim Bahana Sekuritas ya. Mohon
> tunggu sebentar.
>
> **→ Diserahkan ke petugas.** *Alasan yang tercatat di CRM: "Customer requests
> to speak with a relationship manager to review their risk profile."*

**Petugas mengambil alih** — naskah yang disarankan:

> **Agen:** Selamat siang Ibu Sari, saya Andi dari tim Relationship Management
> Bahana Sekuritas. Saya sudah membaca percakapan Ibu dengan asisten kami.
>
> **Agen:** Betul bahwa profil risiko Ibu saat ini tercatat Konservatif, dan
> itulah sebabnya produk saham belum kami tawarkan. Profil ini bisa ditinjau
> ulang — prosesnya lewat kuesioner profil risiko, dan hasilnya yang menentukan
> produk apa saja yang bisa Ibu akses.
>
> **Agen:** Boleh saya jadwalkan sesi 30 menit minggu ini untuk membahas tujuan
> investasi Ibu sekaligus meninjau profil risikonya? Sekalian kita bahas dana
> Rp 82.500.000 di RDN yang belum ditempatkan.

*Mengapa naskahnya begitu:* petugas memakai sidebar — profil, dana mengendap,
312 hari — alih-alih meminta nasabah mengulang ceritanya. Itulah seluruh
argumen untuk menyerahkan **konteks**, bukan sekadar transkrip.

**Perhatikan juga:** sejak serah terima, asisten tidak menyela sama sekali,
bahkan ketika nasabah mengirim pertanyaan biasa.

---

### 6.2 `[moderat]` — Budi Santoso · permintaan operasional

Portofolionya terkonsentrasi, 190 hari tidak bertransaksi. Berakhir pada
sesuatu yang **tidak boleh** dikerjakan mesin.

> **Anda:** haloo admin
>
> **Asisten:** Halo juga! Ada yang bisa saya bantu?

> **Anda:** bagaimana profile saya?
>
> **Asisten:** Halo, Bapak Budi. Berdasarkan catatan kami, Anda memiliki profil
> risiko Moderat. Saldo kas RDN Anda saat ini Rp 46.000.000, dan Anda memiliki
> saham BBCA, BBRI, serta TLKM. **Portofolio Anda saat ini terkonsentrasi pada
> sektor Keuangan dan Infrastruktur.** Apakah ada hal lain yang ingin Anda
> ketahui?

**← periksa di sini.** Kalimat yang ditebalkan adalah pengetahuan sektor.
Sebelum ada ini, asisten bisa menyebut kode saham tetapi tidak tahu BBCA dan
BBRI sama-sama bank. Kalau kalimat ini hilang, kontaknya basi — jalankan ulang
sinkronisasi (§2).

> **Anda:** portofolio saya sudah cukup terdiversifikasi belum?
>
> **Asisten:** Bapak Budi, portofolio Anda saat ini terkonsentrasi pada sektor
> Keuangan dan Infrastruktur. Untuk membantu diversifikasi, kami merekomendasikan
> Reksa Dana Campuran yang sesuai dengan profil risiko moderat Anda.

> **Anda:** hmmm portfolio saya gitu gitu aja yaa
>
> **Asisten:** Kami memahami, Bapak Budi. Untuk membantu portofolio Anda agar
> tidak "gitu-gitu saja" dan lebih terdiversifikasi, kami merekomendasikan Reksa
> Dana Campuran. Produk ini cocok dengan profil risiko moderat Anda.

> **Anda:** tapi saya ingin fokusnya ke saham ajaa, gimana yaa?
>
> **Asisten:** Kami memahami Anda ingin fokus pada saham, Bapak Budi. Saat ini
> Anda sudah memiliki saham BBCA, BBRI, dan TLKM. Produk yang kami tawarkan
> sebelumnya, Reksa Dana Campuran, bertujuan untuk diversifikasi portofolio
> secara lebih luas dan bukan hanya saham. Jika Anda ingin berdiskusi lebih
> lanjut mengenai peluang atau produk investasi saham lainnya, kami bisa membantu
> menghubungkan Anda dengan manajer hubungan nasabah kami.

**← periksa di sini.** Dulu giliran ini mengakhiri percakapan: asisten hanya
menjawab *"ada lagi yang bisa saya bantu?"* dan berhenti. Kalau Anda melihat
kalimat itu lagi, prompt yang terpasang sudah basi.

> **Anda:** oke deh nanti saya pikirkan, tapi tolong ubah nomor rekening bank
> saya dulu ya
>
> **Asisten:** Baik, saya hubungkan Anda dengan tim Bahana Sekuritas ya. Mohon
> tunggu sebentar.
>
> **→ Diserahkan ke petugas.** *Alasan yang tercatat di CRM: "Customer is asking
> to change their bank account number, which requires human verification and
> processing."*

**Petugas mengambil alih** — naskah yang disarankan:

> **Agen:** Selamat siang Pak Budi, saya Rina dari tim Customer Care Bahana
> Sekuritas. Terkait perubahan nomor rekening bank, prosesnya memang harus lewat
> kami dan tidak bisa dilakukan otomatis.
>
> **Agen:** Untuk keamanan, saya perlu memverifikasi identitas Bapak dulu. Mohon
> kirimkan nomor SID/CIF dan foto KTP, lalu kami kirimkan formulir perubahan data
> rekening untuk ditandatangani.
>
> **Agen:** Sementara formulirnya kami siapkan — apakah Bapak ingin saya teruskan
> juga diskusi soal portofolio tadi ke Relationship Manager Bapak?

*Mengapa naskahnya begitu:* petugas mengerjakan persis hal yang ditolak mesin,
lalu **mengambil kembali benang penjualannya**. Penolakan tadi bukan akhir dari
percakapan komersial, hanya akhir dari bagian yang boleh dikerjakan asisten.

---

### 6.3 `[agresif]` — Rizki Pratama · cross-sell dan garis kepatuhan

Trader aktif, transaksi terakhir 3 hari lalu.

> **Anda:** halo
>
> **Asisten:** Halo juga, ada yang bisa saya bantu?

**← periksa di sini.** Tidak ada penawaran produk pada sapaan kosong.

> **Anda:** bagaimana profil saya?
>
> **Asisten:** Berdasarkan profil Anda, Anda memiliki profil risiko Agresif
> dengan aset yang dikelola lebih dari Rp 1 miliar. Portofolio Anda saat ini
> mencakup saham-saham seperti ANTM, BBRI, ICBP, dan PGAS, yang tersebar di
> sektor Barang Baku, Keuangan, Barang Konsumen, dan Energi.

> **Anda:** portofolio saya sudah cukup terdiversifikasi belum?
>
> **Asisten:** Meskipun portofolio Anda saat ini mencakup saham dari empat sektor
> berbeda, diversifikasinya masih dapat ditingkatkan. Saat ini kepemilikan Anda
> terkonsentrasi pada saham individu. Reksa Dana Saham dapat menjadi pilihan yang
> baik untuk menambah diversifikasi investasi Anda.

> **Anda:** saya ingin fokus ke saham saja
>
> **Asisten:** Baik, jika Anda ingin fokus pada saham, kami memiliki penawaran
> langganan IPO yang mungkin menarik bagi Anda untuk menambah koleksi saham di
> portofolio Anda. Apakah Anda ingin tahu lebih banyak tentang langganan IPO?

**← babak yang paling layak didemokan.** Kalimat yang sama persis dengan yang
dikirim Sari di §6.1 — *"saya hanya mau saham"* — dan hasilnya berlawanan. Rizki
dipindahkan ke IPO Subscription; Sari mendapat tawaran peninjauan profil. Bukan
karena model sedang cerdas atau sedang berhati-hati: daftar produknya ditentukan
sebagai data, sebelum asisten dilibatkan. **Tunjukkan keduanya berdampingan.**

> **Anda:** IPO itu menarik, ada IPO apa yang bagus minggu ini?
>
> **Asisten:** Baik, saya hubungkan Anda dengan tim Bahana Sekuritas ya. Mohon
> tunggu sebentar.
>
> **→ Diserahkan ke petugas.** *Alasan yang tercatat di CRM: "Customer is asking
> for specific IPO recommendations, which is investment advice that the AI cannot
> provide."*

**← periksa di sini.** Satu pesan sebelumnya ia sendiri yang menawarkan **produk**
IPO; di sini ia menolak menyebut IPO mana. Menawarkan produk adalah pemasaran;
menyebut IPO mana yang *bagus* adalah rekomendasi berizin. Ini artefak kepatuhan
paling kuat dalam demo ini.

**Petugas mengambil alih** — naskah yang disarankan:

> **Agen:** Selamat siang Pak Rizki, saya Dimas, Relationship Manager Bahana
> Sekuritas. Asisten kami tidak bisa memberikan rekomendasi efek tertentu — itu
> memang harus lewat kami yang berizin.
>
> **Agen:** Untuk IPO yang sedang dalam masa penawaran, saya kirimkan prospektus
> dan jadwalnya ya, supaya Bapak bisa menilai sendiri. Saya juga siap membahas
> pertimbangannya lewat telepon.
>
> **Agen:** Sekadar catatan, keputusan investasi sepenuhnya ada pada Bapak, dan
> kinerja masa lalu tidak menjamin hasil di masa depan.

*Mengapa naskahnya begitu:* petugas menyediakan dokumen dan konteks, bukan
vonis — persis yang dilakukan orang berizin. Kalau audiens Anda menyertakan tim
kepatuhan, inilah pertukaran yang layak ditelusuri bersama mereka.

---

## 7. Empat cara percakapan berpindah ke manusia

| # | Pemicu | Contoh | Siapa yang memutuskan |
|---|---|---|---|
| 1 | **Nasabah memintanya** | "saya mau bicara dengan relationship manager" (§6.1) | Asisten |
| 2 | **Permintaan operasional** — ubah rekening, transfer, pesan efek | "tolong ubah nomor rekening bank saya" (§6.2) | Asisten |
| 3 | **Batas kepatuhan** — minta rekomendasi efek tertentu | "ada IPO apa yang bagus minggu ini?" (§6.3) | Asisten |
| 4 | **Aturan otomatis CRM** — kata BERHENTI/STOP | ketik `BERHENTI` | **Aturan, tanpa AI** |

Tiga yang pertama adalah keputusan asisten. Yang keempat berbeda secara mendasar
dan itulah yang paling relevan bagi tim kepatuhan Anda: **aturannya berjalan
sebelum asisten dilibatkan.**

Artinya persetujuan pemasaran dan penyerahan ke manusia adalah aturan yang bisa
dibaca dan diubah oleh tim Anda, bukan kalimat yang dititipkan ke model.

> **Peringatan — `BERHENTI` mengunci percakapan Anda.** Lakukan paling akhir,
> dan lihat §8 untuk mengembalikannya.

---

## 8. Mengembalikan percakapan ke asisten

**Baca ini sebelum mencoba profil kedua.** Setiap percakapan di §6 berakhir
berstatus `open` dan dipegang manusia, sehingga asisten **tidak akan** menjawab
pesan berikutnya dari ponsel yang sama — termasuk kalau Anda langsung mengirim
tanda profil yang berbeda.

Ini penyebab nomor satu laporan "kok bot-nya tidak membalas". Bot yang berhenti
dengan benar tidak bisa dibedakan dari bot yang rusak, jadi **periksa statusnya
lebih dulu** sebelum menduga hal lain.

Minta tim Devoteam menjalankan reset — prosesnya beberapa detik:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a --project=lv-playground-genai \
  --command='sudo docker exec bahana-chatwoot-rails bundle exec rails runner "
c = Contact.find_by(phone_number: %q(+6281112117038))
v = Conversation.where(contact_id: c.id).order(:last_activity_at).last
v.update!(status: :pending, assignee_id: nil)
puts %Q(conversation #{v.display_id} -> #{v.status})
"'
```

**Reset berdasarkan kontak, bukan berdasarkan nomor percakapan.** Ponsel demo
sudah memiliki lebih dari satu percakapan, sehingga mereset berdasarkan id tetap
akan menyentuh percakapan yang salah dan membiarkan yang aktif tetap macet.
Perintah di atas selalu mengambil yang paling terakhir aktif.

---

## 9. Yang **sengaja belum** ada dalam demo ini

Disebutkan terbuka, karena demo yang berpura-pura masalahnya tidak ada lebih
buruk daripada demo yang menamainya.

- **Tidak ada autentikasi.** Asisten menyebutkan angka portofolio kepada
  siapa pun yang memegang ponsel itu. Pada produksi, angka spesifik hanya boleh
  muncul setelah verifikasi (OTP atau tautan dari aplikasi yang sudah login).
- **Tidak ada data Bahana.** Semua sintetis. Demo ini membuktikan
  **mekanismenya**, bukan mutu model pada portofolio Bahana yang sebenarnya.
- **Belum ada antrean saran untuk relationship manager.** Di §6 petugas mengetik
  sendiri. Rencananya, petugas menerima daftar saran berperingkat beserta
  alasannya, dan menyetujuinya sebelum terkirim.
- **Tidak ada blast.** Asisten tidak pernah memulai percakapan. Semua yang Anda
  lihat terjadi di dalam percakapan yang **nasabah** buka lebih dulu.

---

## 10. Masukan yang kami harapkan

1. Di titik mana jawabannya terdengar seperti mesin?
2. Adakah fakta yang dikutip keliru? (Sebutkan nasabah dan pesannya.)
3. Apakah asisten pernah menyebut produk yang menurut Anda tidak pantas untuk
   profil risiko itu? **Ini temuan paling penting yang bisa Anda berikan.**
4. Apakah titik serah terimanya terasa tepat — terlalu cepat, atau terlalu lama?
5. Apakah naskah petugas di §6 mencerminkan cara tim Anda benar-benar bekerja?
