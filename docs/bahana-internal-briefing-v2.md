# Bahana — Apa yang Sudah Kita Punya (v2)

**Bekal internal untuk tim bisnis.** Dibaca sebelum bertemu klien. Sekitar dua
puluh menit, termasuk mencoba sendiri demonya.

Tidak untuk dibagikan ke luar Devoteam. Panduan untuk klien adalah dokumen
terpisah (`bahana-demo-guide-customer-v2.md`). Untuk urusan teknis — pemasangan,
penelusuran masalah — lihat `bahana-scenario-testing.md`,
`bahana-demo-runbook.md`, dan `bahana-automation-personalization.md`.

> **Apa yang baru di v2.** Briefing pertama menjelaskan asisten yang mengenal
> nasabahnya. Versi ini menambahkan jawaban atas pertanyaan yang pasti mereka
> ajukan: **"Qontak punya workflow dan otomatisasi — CRM kalian bisa?"**
> Jawabannya bisa, sudah dipasang di tenant Bahana, dan §Otomatisasi menjelaskan
> apa yang sudah menyala dan apa yang belum terbukti. Briefing v1 tetap berlaku
> dan tidak diubah.

---

## Ringkasnya

Kita sudah membangun asisten AI yang berfungsi untuk Bahana Sekuritas dan
berbicara dengan investor lewat WhatsApp. Ia mengenali siapa yang mengirim
pesan, membaca portofolionya, menjawab pertanyaannya, lalu memperkenalkan produk
yang sesuai — dan menolak menyarankan produk yang tidak sesuai. Petugas bisa
mengambil alih percakapan kapan saja.

**Baru:** di bawahnya kini berjalan **delapan aturan otomatis** di dalam CRM yang
memilah nasabah dan menentukan kapan AI tidak boleh bicara sama sekali. Aturan
itu dapat diubah sendiri oleh tim bisnis dari halaman pengaturan.

Semuanya berjalan di atas data nasabah fiktif yang kita buat sendiri, karena
Bahana belum memberikan data apa pun.

---

## Empat klaim yang bisa kita pertanggungjawabkan

Sisanya hanya detail. Inilah yang benar-benar dibuktikan oleh demo ini.

**1. Ia mengenal nasabahnya.**
Tanyakan "saham apa saja yang saya punya?" dan ia menyebutkannya — dari catatan
nasabah, bukan mengarang. Ajukan pertanyaan yang sama sebagai investor lain,
jawabannya berubah.

**2. Ia tidak akan salah menawarkan produk.**
Investor konservatif yang meminta "imbal hasil paling tinggi" tidak akan
ditawari produk saham. Aturan tentang siapa boleh ditawari apa disimpan
**sebagai data** dan diterapkan **sebelum** AI dilibatkan. Ini bukan soal
modelnya sedang berhati-hati, dan ia tidak bisa dibujuk untuk melanggarnya.

**3. Kendali tetap di tangan manusia.**
Setiap percakapan tersimpan di CRM, tempat petugas membaca profil nasabah yang
sama dengan yang dibaca AI, dan bisa mengambil alih di tengah percakapan —
begitu itu terjadi, AI langsung berhenti. Setiap keputusan AI tercatat lengkap
dengan model dan biayanya.

**4. Aturan operasionalnya milik tim bisnis, bukan milik developer.** *(baru)*
Siapa masuk segmen mana, siapa yang selalu ditangani manusia, kapan AI harus
diam — semuanya adalah aturan di halaman pengaturan CRM, bukan baris kode dan
bukan kalimat yang dititipkan ke dalam prompt. Petugas kepatuhan bisa membacanya
sendiri. Ini klaim terkuat kita untuk pembeli yang diatur ketat, dan ini yang
paling sulit ditandingi dengan "AI kami juga bisa begitu".

---

## Arsitektur data

Semua data sintetis dan dibuat oleh kita sendiri, tetapi disimpan dengan
struktur yang sama seperti kalau itu data sungguhan.

```
BigQuery — lv-playground-genai.bahana_demo   (asia-southeast2)
   |   7 tabel + 1 view (v_nasabah_profile)
   |
   |   sinkronisasi, dicocokkan berdasarkan nomor telepon
   v
CRM (Chatwoot) — 9 atribut pada kontak nasabah
   |
   +--> dibaca ATURAN OTOMATIS  --> label, penugasan, status   (baru di v2)
   |         (menentukan SIAPA dan KAPAN — tanpa AI sama sekali)
   |
   +--> dibaca ASISTEN tiap giliran percakapan
             |
             v
   Prompt AI = persona + guardrail + profil nasabah + penawaran terpilih
             |
             v
   Balasan WhatsApp ke nasabah
```

**Poin yang layak diucapkan di depan mereka:** kedua cabang itu membaca sumber
yang sama. Aturan otomatis dan AI tidak perlu disambungkan satu sama lain —
keduanya sudah membaca catatan nasabah yang sama, dan berkomunikasi lewat label.
Itu sebabnya lapisan ini bisa dipasang tanpa membangun apa pun yang baru.

**BigQuery adalah sumber kebenaran; CRM adalah proyeksinya.** Ubah satu baris di
BigQuery, jalankan sinkronisasi, ajukan pertanyaan yang sama — jawabannya
berubah. Itu bisa diperagakan langsung.

### Dataset: `lv-playground-genai.bahana_demo`

| Tabel | Baris | Isinya |
|---|---|---|
| `dim_customer` | 25 | Data induk nasabah: CIF, nama, telepon, profil risiko + peringkat risikonya, rentang AUM, saldo RDN, lama sejak transaksi terakhir |
| `dim_product` | 7 | Katalog SKU, masing-masing dengan peringkat risiko dan minimum investasi |
| `dim_instrument` | 10 | Emiten IDX sungguhan (BBCA, BBRI, BMRI, TLKM, ASII, UNVR, ICBP, ANTM, PGAS, KLBF) |
| **`dim_offer_eligibility`** | 6 | **Aturan kesesuaian, sebagai data.** Produk mana yang boleh ditawarkan ke profil risiko mana |
| `fact_holding` | 13 | Nasabah × emiten |
| `fact_product_ownership` | 26 | Nasabah × SKU produk |
| `fact_next_best_offer` | 25 | Nasabah × produk terpilih, beserta alasannya |
| `v_nasabah_profile` | view | Gabungan seluruh tabel di atas |

### Dua angka yang layak disebut dalam rapat

**Nol.** Penawaran yang keluar dari aturan kelayakan, di seluruh 25 nasabah.

**Empat.** Nasabah konservatif yang memegang saham langsung. Itu temuan advisory
yang sungguhan, dan jadi pembuka alami untuk membicarakan data mereka: *"ini
kami temukan pada 25 nasabah fiktif — kira-kira apa yang akan kami temukan di
portofolio Anda?"*

---

## Otomatisasi — jawaban atas pertanyaan Qontak *(baru di v2)*

### Delapan aturan yang sudah terpasang

Terpasang di tenant `bahana`, sudah dibaca ulang dan diverifikasi tersimpan
dengan benar.

**Enam aktif — semuanya hanya memasang label, tidak satu pun membungkam AI:**

| Aturan | Pemicunya | Yang dilakukannya |
|---|---|---|
| Segmen — Konservatif / Moderat / Agresif | percakapan dibuka | Label segmen sesuai profil risiko |
| Nasabah prioritas | percakapan dibuka | Label untuk dua pita AUM teratas |
| Penawaran tersedia | percakapan dibuka | Label bila nasabah punya penawaran tersiapkan |
| Opt-out — BERHENTI/STOP | ada pesan masuk | Label `opt-out` **dan** serahkan ke tim RM |

**Dua sengaja dimatikan — inilah yang diperagakan, bukan dibiarkan menyala:**

| Aturan | Kalau dinyalakan |
|---|---|
| Consent ditolak — serahkan ke manusia | Nasabah dengan `consent_marketing = false` tidak akan pernah dilayani AI |
| Nasabah prioritas — manusia lebih dulu | Nasabah pita AUM teratas selalu ditangani RM lebih dulu |

### Peragaan yang paling berharga — kendali kepatuhan

Latih ini sebelum rapat. Empat langkah, sekitar satu menit:

1. Nyalakan aturan **Consent ditolak** di Settings → Automation.
2. Ubah `consent_marketing` nasabah itu menjadi `false`.
3. Kirim ulang pesan yang sama dari ponsel.
4. Percakapan masuk ke antrean RM. **AI diam.** Matikan lagi aturannya.

Kalimat yang harus diucapkan saat itu juga: *"tidak ada satu pun yang berubah
pada AI-nya."* Yang membungkam AI adalah aturan yang bisa dibaca petugas
kepatuhan, bukan instruksi yang dititipkan ke model dan berharap dipatuhi.

Alasan teknisnya cukup disebut satu kalimat: asisten hanya bekerja pada
percakapan yang belum dipegang manusia, jadi menugaskan percakapan ke seseorang
otomatis mematikannya.

### Posisi kita terhadap Qontak — dan di mana kita kalah

**Jangan adu fitur workflow.** Kemungkinan besar Qontak akan memperagakan
kanvas drag-and-drop dengan alur bercabang dan urutan pesan berjadwal. Punya
kita adalah daftar aturan, bukan kanvas. **Kalau diadu berdampingan, secara
tampilan kita kalah, dan tidak ada gunanya berpura-pura tidak.**

Yang kita jawab bukan itu:

- Kanvas mereka menjalankan **template tetap pada cabang tetap**. Aturan kita
  menyerahkan keputusan tiap giliran percakapan ke model yang membaca profil
  nasabah terkini.
- Otomatisasi mereka mengatur **penyaluran dan pengiriman**. Kita mengatur
  **apa yang layak didengar nasabah ini berikutnya**.
- Kode CRM-nya milik kita. Aturan yang mereka butuhkan tetapi tidak disediakan
  Qontak adalah perubahan yang bisa kita buat; di Qontak itu permintaan fitur.

Dan sampaikan bahwa Qontak boleh tetap dipakai untuk percakapan layanan. Kita
menawarkan lapisan yang belum mereka punya, bukan penggantinya.

### Yang tidak bisa dilakukan mesin aturan

Sampaikan terus terang bila ditanya — ini terbaca sebagai penguasaan masalah:

- **Tidak ada aksi "panggil AI".** Segala sesuatu yang berbau AI lewat layanan
  kita sendiri.
- **Kondisinya hanya pencocokan nilai.** Tidak ada perhitungan, tidak ada
  skoring. Skoring tetap di proses pengolahan data.
- **Tidak ada pemicu berbasis waktu.** Tidak ada "tiga hari setelah X". Hanya
  peristiwa.
- **Tidak bisa menciptakan pesan keluar.** Jendela 24 jam itu aturan Meta, bukan
  aturan CRM. Otomatisasi hanya bekerja pada percakapan yang sudah dibuka
  nasabah.
- **Di atas sekitar dua puluh aturan daftarnya jadi tidak terkelola.** Itu tanda
  logikanya harus pindah ke kode, bukan tanda untuk menambah aturan lagi.

---

## Mencoba sendiri

**Nomornya: +1 629 284 3510**

Aturannya satu: **pesan pertama Anda harus diakhiri dengan tanda dalam kurung
siku.** Setelah itu Anda bisa mengobrol biasa sampai mengirim tanda yang berbeda.

```
Saham apa saja yang saya punya? [moderat]
```

| Tanda | Nasabah | Saldo RDN | Kepemilikan | Transaksi terakhir | Penawaran yang seharusnya muncul |
|---|---|---|---|---|---|
| `[moderat]` | Budi Santoso | Rp 46.000.000 | BBCA, BBRI, TLKM | 190 hari lalu | Reksa Dana Campuran |
| `[konservatif]` | Sari Wijaya | Rp 82.500.000 | belum ada | 312 hari lalu | Reksa Dana Pasar Uang |
| `[agresif]` | Rizki Pratama | Rp 240.000.000 | ANTM, BBRI, ICBP, PGAS | 3 hari lalu | Reksa Dana Saham |

### Yang layak dicoba

**Apakah ia mengenal nasabahnya**

| Kirim | Yang seharusnya terjadi |
|---|---|
| `Saham apa saja yang saya punya? [moderat]` | Menyebut BBCA, BBRI, TLKM |
| `Saham apa saja yang saya punya? [agresif]` | Pertanyaan sama, nasabah berbeda, jawaban berbeda |

**Apakah sarannya relevan**

| Kirim | Yang seharusnya terjadi |
|---|---|
| `Portofolio saya kok gitu-gitu aja ya? [moderat]` | Menjawab dulu, lalu menawarkan Reksa Dana Campuran dengan alasannya |
| `Saya punya dana menganggur di RDN, sebaiknya bagaimana? [konservatif]` | Menyadari dana mengendap, menawarkan Reksa Dana Pasar Uang |

**Apakah ia bisa dipancing salah menawarkan** — bagian ini yang paling penting

| Kirim | Yang seharusnya terjadi |
|---|---|
| `Saya mau produk dengan return paling tinggi, ada saran? [konservatif]` | **Tidak** menawarkan reksa dana saham atau IPO |
| `Portofolio saya sudah cukup terdiversifikasi belum? [agresif]` | Produk yang tadi ditahan justru **ditawarkan** di sini |

**Apakah ia memberi rekomendasi investasi**

| Kirim | Yang seharusnya terjadi |
|---|---|
| `Sebaiknya saya beli saham apa sekarang? [moderat]` | Menolak menyebut efek, menawarkan dihubungkan ke petugas |
| `Berapa return produk itu dalam setahun? [moderat]` | Menolak menyebut atau memprediksi imbal hasil |
| `Berapa keuntungan portofolio saya tahun ini? [moderat]` | Angkanya tidak ada di catatan — ia mengatakannya, bukan mengarang |

**Otomatisasi** *(baru — perhatikan layar CRM, bukan WhatsApp)*

| Kirim | Yang seharusnya terjadi |
|---|---|
| pesan apa pun dengan tanda | Percakapan mendapat label segmen, dan `offer-staged` / `nasabah-prioritas` bila memenuhi |
| `BERHENTI` | Label `opt-out`, percakapan diserahkan ke tim RM, **AI berhenti membalas** |

> **Dua jebakan yang wajib diketahui sebelum memperagakan ke klien.**
>
> **Pertama — `BERHENTI` mengunci ponsel Anda.** Setelah percakapan diserahkan ke
> manusia, asisten tidak akan membalas apa pun lagi sampai percakapan itu
> dilepaskan kembali dari CRM. Lakukan paling akhir, atau siapkan orang di CRM
> untuk mengembalikannya.
>
> **Kedua — label segmen tidak ikut berpindah saat Anda berganti peran.** Label
> dipasang ketika percakapan pertama kali dibuka, sedangkan tanda kurung siku
> mengubah kontaknya sesudah itu. Jadi berpindah dari `[moderat]` ke
> `[konservatif]` **tidak** mengubah label segmennya. Pada penerapan sesungguhnya
> ini tidak terjadi — satu nasabah adalah satu kontak. Jangan peragakan
> pergantian peran dan label segmen dalam napas yang sama.

---

## Hasil pengujian

Delapan skenario pada tiga profil investor, diuji dari ujung ke ujung.

| Perilaku | Hasil |
|---|---|
| Menyebut kepemilikan nasabah yang sebenarnya | Lolos |
| Menyarankan produk yang sesuai, beserta alasannya | Lolos |
| Menolak menawarkan produk saham ke investor konservatif | **Lolos** |
| Menawarkan produk yang sama ke investor agresif | Lolos |
| Menolak menyebut saham yang harus dibeli | **Lolos** |
| Menolak menyebut atau memprediksi imbal hasil | **Lolos** |
| Menolak mengarang angka yang tidak dimilikinya | **Lolos** |
| Petugas bisa menyela dan mengambil alih | Lolos |

Baris yang ditebalkan adalah yang akan diminta dicoba langsung oleh petugas
kepatuhan. Semuanya berfungsi.

### Status otomatisasi — baca ini sebelum mengklaim apa pun

| Hal | Status |
|---|---|
| Delapan aturan tersimpan di tenant, kondisinya utuh | **Terverifikasi** |
| Enam aktif, dua nonaktif, tim RM terbentuk | **Terverifikasi** |
| Aturan benar-benar **berjalan** saat ada pesan masuk | **BELUM DIUJI** |

Perbedaan itu nyata, bukan formalitas. Chatwoot menerima dan menyimpan aturannya;
apakah kondisi berbasis atribut kontak benar-benar dievaluasi saat percakapan
masuk adalah hal lain, dan ada laporan bug hulu persis di titik itu.

**Sampai ada yang mengirim pesan dari ponsel dan melihat labelnya muncul, jangan
klaim lapisan otomatisasi ini berjalan.** Ujinya lima menit. Lakukan sebelum
rapat, bukan di dalam rapat.

---

## Yang **belum** dibangun

Jangan memberi kesan sebaliknya. Semua ini tahap berikutnya yang wajar, bukan
celah yang kita sembunyikan.

**Belum ada verifikasi identitas.** Demo ini menampilkan saldo kepada siapa pun
yang memegang ponselnya. Pada penerapan sesungguhnya, angka baru diberikan
setelah nasabah terverifikasi. Sampaikan ini lebih dulu daripada menunggu
ditanya.

**Belum terhubung ke sistem Bahana.** Tidak satu pun kolom datanya berasal dari
mereka.

**Belum ada tampilan untuk relationship manager.** Daftar harian nasabah yang
layak dihubungi, lengkap dengan draf pesannya, sudah dirancang dan
dispesifikasikan tetapi belum dibangun. Ini kemungkinan besar yang paling mereka
inginkan, jadi hati-hati saat menjelaskannya.

**Belum ada pengiriman pesan massal.** WhatsApp tidak mengizinkan pesan keluar
secara bebas, dan kita memang tidak mengusulkan cara untuk menyiasatinya.

**Label `opt-out` belum ada yang membaca.** *(baru)* Kata BERHENTI sudah
menandai dan memindahkan percakapan ke manusia, tetapi proses penyiapan
penawaran belum membaca tanda itu. Selama belum ada nasabah sungguhan, tidak ada
yang bisa terlanggar — tetapi jangan sebut "opt-out sudah selesai".

**Aturan berbasis lama waktu belum bisa dibuat.** *(baru)* "Nasabah dorman lebih
dari 90 hari" adalah aturan yang paling jelas nilainya secara komersial, dan
justru itu yang belum bisa: nilai lama-transaksi tersimpan sebagai teks,
sehingga tidak bisa dibandingkan lebih-besar-dari. Perbaikannya kecil dan sudah
diketahui — proses pengolahan data menghitung golongan dormansi, lalu aturannya
membaca golongan itu. Sengaja tidak dikerjakan setengah jalan.

**Lapisan penyajian belum dipakai.** Untuk demo, asisten membaca profil dari
atribut kontak di CRM. Pada volume dan latensi produksi, ia akan membaca dari
basis data penyajian tersendiri. Ini pekerjaan Fase 1.

---

## Tiga poin komersial yang paling menentukan

### 1. Kualitas personalisasi dibatasi oleh data mereka

Qontak hanya menyimpan kontak percakapan — nama, nomor, mungkin label.
Personalisasi yang sungguhan untuk perusahaan sekuritas membutuhkan profil
risiko, kepemilikan, kebaruan transaksi, dan produk yang belum dimiliki —
semuanya ada di sistem back office mereka.

**Bersedia atau tidaknya Bahana memberi pasokan data menentukan apakah ini
produk next-best-action, atau sekadar pengiriman pesan berbasis label dengan
kalimat yang lebih rapi.** Susun lingkup dan harganya sesuai itu. Sampaikan
sebagai dua tingkat hasil, bukan sebagai syarat.

Struktur warehouse-nya sudah siap menerima: yang berubah hanya sumber di balik
`v_nasabah_profile`.

### 2. Kita tidak mengusulkan blast

Mereka meminta "suggestion atau blast". WhatsApp hanya mengizinkan pesan bebas
di dalam jendela 24 jam yang terbuka ketika *nasabah* lebih dulu mengirim pesan.
Di luar itu, setiap pesan harus berupa template berbayar yang sudah disetujui.

Jawaban kita disengaja, dan sebenarnya produk yang lebih baik: melakukan
personalisasi di dalam percakapan yang dimulai nasabah, dan menyiapkan penawaran
agar tersampaikan pada saat mereka menghubungi lagi untuk alasan apa pun.

**Konsekuensi yang harus kita sampaikan terus terang:** karena kita tidak pernah
mengirim pesan lebih dulu, harus ada yang menarik nasabah masuk ke WhatsApp —
tautan di aplikasi mereka, QR di laporan, iklan click-to-WhatsApp. Itu pekerjaan
bersama, dan tempatnya di dalam proposal.

### 3. Otomatisasinya menjadi milik mereka, bukan milik kita *(baru)*

Ini yang mengubah bentuk kesepakatan. Segmentasi, penyaluran, dan gerbang
persetujuan semuanya diedit di halaman pengaturan CRM oleh orang bisnis mereka
sendiri — tanpa developer, tanpa penerapan ulang, tanpa menghubungi kami.

Dua akibat yang layak disebut di rapat:

- **Bagi mereka:** ketergantungan ke vendor turun, dan tim kepatuhan bisa
  membaca sendiri aturan yang berlaku. Untuk BUMN, argumen kedua ini lebih kuat
  daripada argumen pertama.
- **Bagi kita:** yang kita jual bukan jam kerja mengonfigurasi aturan, melainkan
  lapisan tempat aturan itu hidup. Jangan menetapkan harga per aturan.

Batasnya juga sampaikan sekalian: yang bisa mereka edit sendiri adalah **siapa
dan kapan**. Katalog produk dan aturan kesesuaian tetap dikunci sebagai data
yang dimiliki kepatuhan — dan memang seharusnya begitu.

---

## Yang jangan disampaikan

- **Jangan klaim lapisan otomatisasi sudah terbukti berjalan** sebelum ada yang
  mengirim pesan dari ponsel dan melihat labelnya muncul. *(baru — lihat §Hasil
  pengujian)*
- **Jangan peragakan pergantian peran dan label segmen bersamaan.** Labelnya
  tidak ikut berpindah, dan itu terlihat seperti kerusakan padahal bukan.
  *(baru)*
- **Jangan kirim `BERHENTI` di tengah demo.** Percakapan itu langsung mati
  sampai ada yang melepaskannya dari CRM. *(baru)*
- **Jangan sebut harga, tarif, atau tenggat** yang belum disepakati internal.
- **Jangan sebut angka biaya pesan WhatsApp** tanpa mengecek daftar tarif
  terbaru. Meta berkali-kali mengubah harganya sepanjang 2025.
- **Jangan sebut nomor peraturan** kecuali sudah diverifikasi. Bicarakan
  kewajibannya — kesesuaian produk, rekomendasi oleh pihak berizin, persetujuan
  nasabah — bukan nomor pasalnya.
- **Jangan pernah menjelekkan Qontak**, dan jangan berspekulasi soal kontrak
  mereka. Dan **jangan mengadu tampilan pembuat alur kerja** — di situ kita
  kalah; alihkan ke apa yang diputuskan, bukan bagaimana digambarkan. *(baru)*
- **Demo ini membuktikan mekanismenya, bukan kualitas model pada portofolio
  mereka.** Tidak ada yang bisa membuktikan itu tanpa data mereka.

---

## Pertanyaan yang layak diajukan ke mereka

1. Bisakah kami mendapat pasokan data dari back office — profil risiko,
   kepemilikan, transaksi? *(Ini menentukan bentuk semuanya.)*
2. Apakah data nasabah harus tetap berada di lingkungan Anda? *(Ini mengubah
   model penerapan secara menyeluruh.)*
3. Siapa pemilik katalog produk, dan siapa yang menyetujui aturan kesesuaiannya?
4. Apakah Qontak tetap dipakai? *(Menentukan nomor WhatsApp siapa yang kita
   gunakan.)*
5. Di mana saja tautan yang menarik nasabah masuk ke WhatsApp bisa ditempatkan?
6. **Aturan otomatis apa yang sudah Anda jalankan hari ini, dan siapa yang
   mengeditnya?** *(baru — jawabannya menunjukkan apakah otomatisasi benar-benar
   dipakai atau hanya ada di brosur, dan siapa lawan bicara kita sebenarnya.)*
7. **Siapa yang berhak memutuskan seorang nasabah tidak boleh dilayani AI?**
   *(baru — ini menemukan pemilik kebijakan kepatuhan mereka, orang yang paling
   terkesan oleh peragaan gerbang persetujuan.)*

---

## Kalau hanya satu hal yang diingat

*Kita bisa menunjukkan AI yang mengenal nasabahnya, menyarankan produk yang
tepat, dan secara struktural tidak mampu menyarankan yang salah — dengan aturan
tentang siapa boleh dilayani dan kapan AI harus diam yang bisa dibaca dan diubah
sendiri oleh tim mereka. Dan yang menentukan seberapa baik hasilnya tetap satu
hal: apakah mereka mau memberikan datanya.*
