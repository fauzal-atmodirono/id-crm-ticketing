# Bahana — Apa yang Sudah Kita Punya

**Bekal internal untuk tim bisnis.** Dibaca sebelum bertemu klien. Sekitar
sepuluh menit.

Tidak untuk dibagikan ke luar Devoteam. Panduan untuk klien adalah dokumen
terpisah.

---

## Ringkasnya

Kita sudah membangun asisten AI yang berfungsi untuk Bahana Sekuritas dan
berbicara dengan investor lewat WhatsApp. Ia mengenali siapa yang mengirim
pesan, membaca portofolionya, menjawab pertanyaannya, lalu memperkenalkan produk
yang sesuai — dan menolak menyarankan produk yang tidak sesuai. Petugas bisa
mengambil alih percakapan kapan saja. Semuanya berjalan di atas data nasabah
fiktif yang kita buat sendiri, karena Bahana belum memberikan data apa pun.

---

## Tiga klaim yang bisa kita pertanggungjawabkan

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
Inilah argumen terkuat yang kita punya untuk pembeli di industri yang diatur
ketat.

**3. Kendali tetap di tangan manusia.**
Setiap percakapan tersimpan di CRM, tempat petugas membaca profil nasabah yang
sama dengan yang dibaca AI, dan bisa mengambil alih di tengah percakapan —
begitu itu terjadi, AI langsung berhenti. Setiap keputusan AI tercatat lengkap
dengan model dan biayanya.

---

## Data yang kita punya

Semuanya sintetis, dibuat oleh kita sendiri, dan tersimpan dalam data warehouse
yang tertata — bukan spreadsheet. Struktur itulah yang membuat kita bisa
menjawab "bagaimana mencegahnya menyarankan produk yang salah" dengan satu
kueri, bukan satu paragraf.

| Apa | Jumlah | Mengapa penting secara komersial |
|---|---|---|
| Data induk nasabah | 25 nasabah | Profil risiko, rentang AUM, saldo kas, lama sejak transaksi terakhir — justru inilah kolom yang dibutuhkan personalisasi |
| Katalog produk | 7 produk | Pasar uang, obligasi, reksa dana campuran, reksa dana saham, IPO, saham langsung — masing-masing dengan tingkat risikonya |
| **Aturan kelayakan** | 6 aturan | **Produk mana yang boleh ditawarkan ke profil risiko mana.** Argumen kepatuhan kita, dalam bentuk tabel |
| Instrumen | 10 emiten IDX | Emiten sungguhan lengkap dengan sektornya, agar kepemilikannya terlihat wajar |
| Kepemilikan | per nasabah | Apa yang benar-benar dimiliki setiap investor |
| Penawaran berikutnya | per nasabah | Satu produk yang dipilih untuk tiap nasabah, beserta alasannya |

### Dua angka yang layak disebut dalam rapat

**Nol.** Penawaran yang keluar dari aturan kelayakan. Bisa dibuktikan langsung
di depan mereka.

**Empat.** Investor konservatif dalam data kita yang memegang saham langsung —
lebih berisiko daripada profil yang mereka nyatakan. Itu temuan advisory yang
sungguhan, dan jadi pembuka alami untuk membicarakan data mereka: *"ini kami
temukan pada 25 nasabah fiktif — kira-kira apa yang akan kami temukan di
portofolio Anda?"*

---

## Apa yang kita uji, dan hasilnya

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

---

## Yang **belum** dibangun

Jangan memberi kesan sebaliknya. Semua ini tahap berikutnya yang wajar, bukan
celah yang kita sembunyikan.

**Belum ada verifikasi identitas.** Demo ini menampilkan saldo kepada siapa pun
yang memegang ponselnya. Pada penerapan sesungguhnya, angka baru diberikan
setelah nasabah terverifikasi. Sampaikan ini lebih dulu daripada menunggu
ditanya — justru itu terbaca sebagai penguasaan masalah.

**Belum terhubung ke sistem Bahana.** Tidak satu pun kolom datanya berasal dari
mereka.

**Belum ada tampilan untuk relationship manager.** Daftar harian nasabah yang
layak dihubungi, lengkap dengan draf pesannya, sudah dirancang dan
dispesifikasikan tetapi belum dibangun. Ini kemungkinan besar yang paling mereka
inginkan, jadi hati-hati saat menjelaskannya.

**Belum ada pengiriman pesan massal.** WhatsApp tidak mengizinkan pesan keluar
secara bebas, dan kita memang tidak mengusulkan cara untuk menyiasatinya.

---

## Dua poin komersial yang paling menentukan

### 1. Kualitas personalisasi dibatasi oleh data mereka

Qontak hanya menyimpan kontak percakapan — nama, nomor, mungkin label.
Personalisasi yang sungguhan untuk perusahaan sekuritas membutuhkan profil
risiko, kepemilikan, kebaruan transaksi, dan produk yang belum dimiliki —
semuanya ada di sistem back office mereka.

**Bersedia atau tidaknya Bahana memberi pasokan data menentukan apakah ini
produk next-best-action, atau sekadar pengiriman pesan berbasis label dengan
kalimat yang lebih rapi.** Susun lingkup dan harganya sesuai itu. Sampaikan
sebagai dua tingkat hasil, bukan sebagai syarat — mereka bisa mulai tanpa data
itu, dan nilai tambahnya akan terlihat sendiri.

### 2. Kita tidak mengusulkan blast

Mereka meminta "suggestion atau blast". WhatsApp hanya mengizinkan pesan bebas
di dalam jendela 24 jam yang terbuka ketika *nasabah* lebih dulu mengirim pesan.
Di luar itu, setiap pesan harus berupa template berbayar yang sudah disetujui.

Jawaban kita disengaja, dan sebenarnya produk yang lebih baik: melakukan
personalisasi di dalam percakapan yang dimulai nasabah, dan menyiapkan penawaran
agar tersampaikan pada saat mereka menghubungi lagi untuk alasan apa pun. Tanpa
template, tanpa biaya pemasaran, dan jauh lebih mudah dipertanggungjawabkan ke
regulator dibanding mendorong penawaran ke investor ritel.

**Konsekuensi yang harus kita sampaikan terus terang:** karena kita tidak pernah
mengirim pesan lebih dulu, harus ada yang menarik nasabah masuk ke WhatsApp —
tautan di aplikasi mereka, QR di laporan, iklan click-to-WhatsApp. Itu pekerjaan
bersama, dan tempatnya di dalam proposal, bukan ditemukan belakangan.

---

## Yang jangan disampaikan

- **Jangan sebut harga, tarif, atau tenggat** yang belum disepakati internal.
- **Jangan sebut angka biaya pesan WhatsApp** tanpa mengecek daftar tarif
  terbaru. Meta berkali-kali mengubah harganya sepanjang 2025.
- **Jangan sebut nomor peraturan** kecuali sudah diverifikasi. Bicarakan
  kewajibannya — kesesuaian produk, rekomendasi oleh pihak berizin, persetujuan
  nasabah — bukan nomor pasalnya.
- **Jangan pernah menjelekkan Qontak**, dan jangan berspekulasi soal kontrak
  mereka. Kita menawarkan lapisan yang belum mereka punya, bukan penggantinya.
- **Demo ini membuktikan mekanismenya, bukan kualitas model pada portofolio
  mereka.** Tidak ada yang bisa membuktikan itu tanpa data mereka.
  Menyampaikannya lebih dulu justru melindungi semua klaim kita yang lain.

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

---

## Kalau hanya satu hal yang diingat

*Kita bisa menunjukkan AI yang mengenal nasabahnya, menyarankan produk yang
tepat, dan secara struktural tidak mampu menyarankan yang salah — dan yang
menentukan seberapa baik hasilnya adalah apakah mereka mau memberikan datanya.*
