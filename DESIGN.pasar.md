# DESIGN.pasar.md — kontrak arah untuk seri "Pasar" (dashboard IHSG)

Turunan dari `/root/raschlab-viz/DESIGN.data-story.md` (otoritas token dan tipografi).
Dibekukan 20 Okt 2026 oleh Arc. Urutan otoritas: `DESIGN.data-story.md` -> file ini -> rencana run.

## Design Read

> Reading this as: tiga halaman data-story tentang pasar Indonesia 2026, untuk pembaca umum yang
> memegang saham atau menabung rupiah, dalam bahasa editorial data-journalism (ground navy, headline
> ALL-CAPS, bar tanpa axis, satu aksen cyan), dial ENERGY 3 / RHYTHM 3 / MOTION 1.

Tiga halaman, satu arah, satu ground. Tiap halaman satu cerita:

1. `pasar/msci.html` — **struktur**: sembilan saham yang menentukan indeks.
2. `pasar/ihsg.html` — **jatuhnya**: 9.134,70 ke 4.918,39.
3. `pasar/rupiah.html` — **yang bergerak bareng**: 16.716 ke 18.043 dan korelasi -0,405.

## Token (diwarisi apa adanya dari DESIGN.data-story.md)

Ground `#F7F1E6` · plane `#FFFBF3` · plane-2 `#F0E6D4` · ink `#1A1410` · muted `#6B5B4A` ·
line `#DCCFB8` · control `#8C7A62` · accent `#2D4ECF` · cat-1..5 `#2D4ECF #1F7A8C #A15C1E #6B7A3A #8C3B4A`.

Palet navy + cyan dari `DESIGN.data-story.md` **ditolak pemilik** (20 Okt 2026, "aku gasuka warnanya").
Yang dipakai palet rumah dari hub: kertas krem, tinta cokelat-hitam, satu aksen biru. Peran warna tetap:
aksen untuk bank, lima warna kategorikal untuk lima sektor, semuanya >= 4:1 terhadap ground.

**Peran diturunkan ulang, bukan disalin:**

- `--accent` = bank (empat konstituen terbesar) dan angka yang jadi fokus band. Bukan "bar terpilih".
- `--cat-1..5` = lima sektor, urut menurut porsi: keuangan, komunikasi, barang industri, bahan baku,
  energi. Kategorinya tidak berurut, jadi lima rona terpisah (bukan ramp kecerahan) supaya segmen kecil
  tetap terbaca di atas kertas krem.
- `--misfit` **tidak boleh** muncul di seri ini. Tidak ada status baik/buruk di data pasar; memakai
  merah untuk "saham turun" berarti mengarang penilaian. Penurunan digambar dengan panjang bar dan angka.
- `--fit` dan `--warn` tidak dipakai.
- `--line` dan `--control` tidak pernah jadi warna teks.

## Aturan yang mengikat halaman ini

- **Nol donut.** Proporsi digambar sebagai stacked bar 100% (koreksi dari versi pertama halaman MSCI
  yang memakai donut; donut haram di arah ini).
- **Bar tanpa axis**: panjang bar = nilai, angka ditulis di ujung bar, gridline tipis di belakang.
  Tick sumbu penuh di kiri (0/10/20/30/35) yang dipakai versi pertama harus hilang.
- Judul band menyebut temuan, bukan jenis chart. Subtitle italic menyebut ruang lingkup + tanggal.
- Satu focal point per band. Nol band berbentuk "judul tengah + subjudul + grid kartu sama besar".
- Band berupa **sheet**, bukan kartu: nol border, nol radius, nol shadow. Pemisah = pergantian ground.
  Padding vertikal ditentukan peran band, bukan satu angka untuk semua.
- Nol gradient, nol glow, nol glass, nol emoji, nol em dash.
- Semua angka lahir dari `pasar/data/*.json` lewat generator. Nol angka diketik di template.
- Footer menyatu di ground: sumber, metode, tanggal data, dan sha256 berkas sumber.
- Motif: satu **pita skala** yang membawa angka nyata dengan satuan, dipakai tepat tiga tempat per
  halaman, dan tidak boleh menduplikasi chart yang sudah menggambar angka yang sama di band yang sama.

## Tipografi

`Archivo Black` untuk headline band ALL-CAPS dan wordmark saja; `Archivo` 400/600 untuk body;
`IBM Plex Mono` 400/500 untuk semua angka. Skala 56/34 display, 30/24 h1 band, 15/15 body, 12/12 label
mikro. Lantai 12px. Font base64, nol jaringan.

## Tagline yang harus konsisten di tiga halaman

`PASAR · 01` / `PASAR · 02` / `PASAR · 03` di label mikro, dan satu baris penutup yang menyebut
"MSCI Indonesia Index (USD, net), bukan IHSG" hanya di halaman 01.
