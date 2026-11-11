# DESIGN.karhutla.md: kontrak halaman `karhutla/` (pantauan titik panas, diperbarui otomatis)

Ditulis Arc, 11 Nov 2026, sebelum halaman dibangun. Urutan otoritas: file ini -> `karhutla/INTERFACE.md`
(bentuk payload) -> template. Kanon: `dashboard-design` (alat, bukan poster), IBCS untuk notasi perbandingan.

## Jenis artefak

ALAT, bukan poster. Dibuka berulang untuk melihat keadaan sekarang. Punya kontrol (periode peta, periode
provinsi, drill provinsi), tiga state (memuat, kosong, galat), dan diperbarui jadwal (cron `no_agent`,
3x sehari). Seri konten karhutla (carousel + reel, 10-11 Nov 2026) adalah posternya; halaman ini lane dashboard.

## Pertanyaan yang dijawab

"Di mana titik panas sekarang, dan apakah lebih banyak atau lebih sedikit dari biasanya?"
Visual yang tidak memberi makan pertanyaan itu tidak ada di halaman.

## Design Read

> Reading this as: dashboard pantauan karhutla untuk pembaca umum dan jurnalis yang mengecek keadaan terkini dari
> ponsel atau laptop, dalam bahasa editorial-data rumah (kertas krem, tinta cokelat-hitam, satu warna api),
> dial ENERGY 2 / RHYTHM 2 / MOTION 1.

## Basis angka (aturan paling penting)

- Di dalam 2026 (pekan ini vs pekan lalu, provinsi, peta): **MODIS + NOAA-20**. NOAA-21 masuk data SiPongi
  2 Nov 2026 dan menggandakan hitungan tanpa api bertambah, jadi tidak dihitung.
- Antar tahun (2026 vs 2025/2024/2023/2019): **MODIS saja**. Data SiPongi 2019 hanya berisi MODIS.
- 2015 sengaja tidak dipakai: SiPongi 2015 (133.168 titik MODIS 1 Jan-10 Nov) 22% di bawah arsip FIRMS (171.492),
  datanya tidak lengkap.
- Semua tingkat keyakinan (low/medium/high) dihitung, sama dengan tampilan bawaan SiPongi.
- Halaman menyebut basis di setiap angka yang membandingkan. Angka resmi SiPongi (semua satelit) lebih tinggi.

## Token

  --ground #F7F1E6  --plane #FFFBF3  --plane-2 #F0E6D4  --ink #1A1410  --muted #6B5B4A  --line #DCCFB8
  --control #8C7A62  --fire #B42F14
Satu warna api, hanya untuk tanda (titik peta, bar, garis 2026). Identitas di teks dibawa tinta, bukan api:
karena subjeknya sendiri kelas "buruk", api di label membuat semua label tampak seperti alarm.
Tidak ada hijau di mana pun (titik panas turun bukan kabar yang diberi warna). Tahun pembanding: --control.
Nol gradient, glow, shadow, kapsul, garis tepi kiri dekoratif, emoji, em dash.

## Tipografi

Archivo Black (judul band, sentence case), Archivo 400/600 (isi), IBM Plex Mono 400/500 (semua angka, label mikro).
Font base64 inline (konvensi seri pasar), halaman tetap terbuka tanpa jaringan. Lantai 12px di semua ukuran.

## Struktur

1. Strip `Data per <tanggal>, <jam> WIB`: statis saat build, cuma tanggal dan jam.
2. Kepala: label mikro, judul, **satu angka fokus** (titik panas 7 hari penuh terakhir) dengan pembandingnya
   (7 hari sebelumnya), lalu tiga angka pendamping: hari terakhir parsial (s.d. jam data) + kemarin penuh;
   provinsi terbanyak + porsinya; sejak 1 Jan (MODIS) vs 2025 dan 2019.
3. Peta: sel 0,25 derajat, ukuran titik = jumlah titik panas (warna satu, ukuran membawa besaran).
   Kontrol: Hari terakhir / 7 hari / 30 hari. Ketuk sel -> baca jumlah dan koordinat (bukan hover saja).
4. Tren harian 2026 (MODIS + NOAA-20) dengan rata-rata 7 hari; kursor bisa digeser dengan panah keyboard.
5. Antar tahun (MODIS): akumulasi 1 Jan sampai hari ini untuk 2019, 2023, 2024, 2025, 2026, label langsung di ujung.
6. Provinsi: kontrol 7 hari / 30 hari / sejak 1 Jan, baris diurut jumlah, perubahan vs periode sebelumnya (7/30),
   drill ke provinsi (tren 60 hari + kabupaten teratas) dengan tombol kembali.
7. Catatan metode + sumber.

## State

- Memuat: kerangka statis dengan angka build; kontrol nonaktif sampai skrip siap.
- Kosong: periode tanpa titik panas -> kalimat "Tidak ada titik panas terdeteksi pada periode ini".
- Galat: payload gagal dibaca -> pita galat, angka statis tetap tampil dan diberi tahu bahwa itu versi build.

## Dial

ENERGY 2 / RHYTHM 2 / MOTION 1 (hover/fokus dan transisi kontrol saja, tanpa scroll reveal).

## Jadwal dan kesegaran

Cron `no_agent` 3x sehari (05.30, 11.30, 16.30 WIB) setelah lintasan satelit: tarik ulang 4 hari terakhir,
bangun payload + halaman, jalankan gate, commit + push hanya kalau lolos. Gagal -> notifikasi; sukses senyap.
`tools/check_freshness.py` memantau halaman ini (cadence daily).
