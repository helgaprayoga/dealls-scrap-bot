# Bot Rekap Lowongan Dealls

Bot Telegram yang mengambil lowongan kerja dari [dealls.com](https://dealls.com)
setiap hari, menyimpannya secara **akumulatif**, lalu mengirim ringkasan
lowongan baru ke setiap pelanggan.

Seluruh komentar, docstring, dan pesan bot ditulis dalam Bahasa Indonesia.

---

## Fitur

| Kategori | Keterangan |
|---|---|
| Scraping terjadwal | Berjalan otomatis tiap hari pukul **07:00 WIB** (dapat diubah) |
| Data akumulatif | Data baru **ditambahkan**, tidak menimpa data lama |
| Tanpa duplikat | Kunci unik berupa slug lowongan; `INSERT OR IGNORE` + transaksi |
| Penonaktifan | Lowongan yang tak terlihat 3 run berturut-turut ditandai tidak aktif, **tidak dihapus** |
| Notifikasi | Ringkasan lowongan baru dikirim ke setiap pelanggan |
| Menu bot | Daftar perintah muncul sebagai tombol — cukup satu klik, tanpa mengetik |
| Analisis | Statistik teks, skill teratas, dan grafik matplotlib |
| Ekspor | CSV, XLSX (2 sheet), DOCX |
| Dashboard web | Flask: pencarian, filter, pagination, grafik, dan unduhan |
| Sopan | Patuh `robots.txt`, jeda acak, retry dengan backoff |

---

## Struktur Proyek

```
.
├── bot.py               # Handler Telegram + penjadwal harian + menu perintah
├── pesan.py             # Seluruh teks & format pesan bot (fungsi murni)
├── scraper.py           # Fetch, parser, dan mode --dry-run
├── db.py                # Skema SQLite dan seluruh kueri
├── analytics.py         # Statistik teks dan grafik
├── exporters.py         # Ekspor CSV / XLSX / DOCX
├── app.py               # Dashboard web Flask
├── config.py            # Konfigurasi, selector HTML, zona waktu
├── templates/
│   ├── base.html        # Kerangka halaman
│   ├── index.html       # Daftar lowongan, grafik, peringkat
│   └── detail.html      # Detail satu lowongan
├── static/
│   └── style.css        # Tampilan dashboard (tanpa dependensi)
├── requirements.txt
├── .env.example
├── .gitignore
├── tests/
│   ├── test_parser.py   # Uji parser dengan fixture HTML (tanpa internet)
│   ├── test_pesan.py    # Uji teks bot & menu (tanpa Telegram)
│   ├── test_bot_logic.py# Uji notifikasi, escaping, jadwal
│   ├── test_web.py      # Uji dashboard Flask via test client
│   └── fixtures/
│       ├── listing.html
│       └── detail.html
├── data.db              # Dibuat otomatis
└── logs/bot.log         # Dibuat otomatis
```

---

## Instalasi

Butuh **Python 3.11+**.

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

> **Penting:** `python-telegram-bot[job-queue]` wajib memakai ekstra
> `[job-queue]`. Tanpa itu penjadwal harian tidak bisa dipasang karena
> APScheduler tidak ikut terpasang.
>
> **Windows:** paket `tzdata` wajib ada. Windows tidak punya basis data zona
> waktu, sehingga `ZoneInfo("Asia/Jakarta")` gagal tanpa paket tersebut.

### Konfigurasi

```bash
cp .env.example .env    # Windows: copy .env.example .env
```

Isi `.env`:

| Variabel | Wajib | Default | Keterangan |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ya | — | Token dari @BotFather |
| `ADMIN_CHAT_ID` | ya | — | Chat ID admin penerima peringatan |
| `SCRAPE_HOUR` | tidak | `7` | Jam scraping (WIB) |
| `SCRAPE_MINUTE` | tidak | `0` | Menit scraping (WIB) |
| `PAGES_PER_SOURCE` | tidak | `3` | Halaman per sumber per hari |
| `REQUEST_DELAY_MIN` | tidak | `2` | Jeda minimum antar request (detik) |
| `REQUEST_DELAY_MAX` | tidak | `4` | Jeda maksimum antar request (detik) |
| `DETAIL_SCRAPING_ENABLED` | tidak | `1` | `1` untuk mengambil skill dari halaman detail |
| `DETAIL_MAX_PER_RUN` | tidak | `30` | Batas halaman detail per run |
| `TELEGRAM_CONNECT_TIMEOUT` | tidak | `20` | Batas waktu menyambung ke Telegram (detik) |
| `TELEGRAM_READ_TIMEOUT` | tidak | `20` | Batas waktu menunggu balasan Telegram (detik) |
| `TELEGRAM_MAX_RETRY` | tidak | `3` | Percobaan ulang saat jaringan bermasalah |
| `TELEGRAM_RETRY_DELAY` | tidak | `1.5` | Jeda dasar antar percobaan (detik) |

Daftar sumber ada di `config.py` pada variabel `SOURCES`, sehingga mudah
ditambah:

```python
SOURCES = [
    {
        "name": "IT & Software Engineering",
        "url": "https://dealls.com/loker/posisi/loker-it-and-software-engineering",
        "category": "it",
    },
    # tambahkan sumber baru di sini
]
```

---

## Menjalankan

```bash
# Bot lengkap (scraping otomatis tiap hari + semua perintah)
python bot.py
```

### Dashboard web

```bash
python app.py
```

Lalu buka **http://127.0.0.1:5000**.

Dashboard menampilkan:

- 5 kartu ringkasan: total lowongan, aktif, baru hari ini, pelanggan, total run
- Pencarian judul / perusahaan / skill, filter status, dan pagination
- 4 grafik (harian, perusahaan, lokasi, skill)
- Peringkat skill, perusahaan, dan lokasi teratas
- Riwayat scraping terakhir beserta statusnya
- Halaman detail per lowongan, plus unduhan CSV / XLSX / DOCX

Endpoint JSON tersedia untuk pemakaian lain: `/api/statistik` dan
`/api/lowongan?q=...&status=...&halaman=...`.

Dashboard hanya **membaca** database. Scraping dan notifikasi tetap
dijalankan oleh `bot.py`.

Variabel opsional:

| Variabel | Default | Keterangan |
|---|---|---|
| `DASHBOARD_HOST` | `127.0.0.1` | Isi `0.0.0.0` agar bisa diakses dari perangkat lain |
| `DASHBOARD_PORT` | `5000` | Port server |
| `DASHBOARD_DEBUG` | `0` | Isi `1` untuk mode debug Flask |

> Peringatan: `app.py` memakai server pengembangan Flask dan tanpa login.
> Untuk pemakaian di jaringan publik, pakai `waitress`/`gunicorn` dan
> tambahkan autentikasi lebih dulu.

### Mode uji tanpa Telegram dan tanpa database

```bash
# Cetak hasil parse saja
python scraper.py --dry-run

# Batasi 1 halaman saja
python scraper.py --dry-run --pages 1

# Sekaligus ekstrak skill dari halaman detail (lebih lambat)
python scraper.py --dry-run --pages 1 --detail

# Pilih sumber tertentu (nama atau kategori). Saat ini hanya "it".
python scraper.py --dry-run --source it

# Hasil dalam bentuk JSON
python scraper.py --dry-run --json
```

Opsi `--dry-run` **tidak** menulis ke database dan **tidak** menghubungi
Telegram. Tanpa `--dry-run`, hasil scraping langsung disimpan ke `data.db`.

### Menjalankan pengujian

```bash
pytest tests -q
```

Seluruh uji berjalan **tanpa internet**: HTML asli disimpan sebagai fixture
di `tests/fixtures/`.

---

## Perintah Bot

| Perintah | Fungsi |
|---|---|
| `/start` | Sapaan, daftar perintah, sekaligus berlangganan |
| `/help` | Daftar perintah |
| `/baru` | Lowongan baru hari ini (10 per pesan, tombol **Berikutnya**) |
| `/cari <kata>` | Cari di judul, perusahaan, dan skill |
| `/berhenti` | Matikan notifikasi harian |
| `/langganan` | Nyalakan notifikasi harian |
| `/skill` | 10 skill paling sering muncul |
| `/grafik harian` | Lowongan baru per hari |
| `/grafik skill` | Skill teratas |
| `/grafik perusahaan` | Perusahaan teratas |
| `/grafik lokasi` | Lokasi teratas |
| `/export csv` | Unduh CSV |
| `/export xlsx` | Unduh XLSX (sheet Lowongan + Statistik) |
| `/export docx` | Unduh DOCX (ringkasan + tabel + skill) |
| `/scrape` | Jalankan scraping sekarang (**khusus admin**) |

Setiap perintah dicatat ke tabel `activity_log`.

### Menu perintah (tombol)

Saat bot mulai, `bot.py` memanggil `set_my_commands()` sehingga perintah
muncul sebagai **tombol menu di sisi kiri kolom pesan** Telegram. Anda bisa
menjalankan perintah dengan satu klik tanpa mengetiknya.

Perintah yang punya argumen (`/cari`, `/grafik`, `/export`) tetap
perlu dilanjutkan dengan teks setelah menekan tombolnya, karena Telegram
hanya menyisipkan `/perintah` ke kolom input. Sisanya langsung berjalan.

Menu didaftarkan pada tiga cakupan:

| Cakupan | Isi |
|---|---|
| `BotCommandScopeAllPrivateChats` | 8 perintah untuk chat pribadi |
| Default (tanpa scope) | 8 perintah yang sama untuk grup dan chat lain |
| `BotCommandScopeChat` admin | 9 perintah, termasuk `/scrape` |

`/scrape` sengaja hanya muncul di menu admin agar pengguna biasa tidak
memanggilnya. Perintah yang tidak dikenal akan dijawab dengan saran membuka
`/help` atau memilih dari menu.

> Bila menu tidak muncul, buka chat dengan bot lalu ketik `/start`. Telegram
> menyimpan cache menu per chat; memulai ulang chat memaksanya memuat ulang.
> Pastikan juga `ADMIN_CHAT_ID` benar dan Anda sudah pernah memulai chat
> dengan bot, karena menu admin didaftarkan per chat.

---

## Cara Kerja

### 1. Scraping

1. `robots.txt` dibaca lebih dulu dengan `urllib.robotparser`.
   Jika jalur dilarang, scraping **dihentikan** dan dilaporkan — tidak
   ditembus.
2. Setiap halaman diambil dengan jeda acak 2–4 detik, timeout 15 detik,
   dan maksimal 3 percobaan dengan *exponential backoff*.
3. Status **403** atau **429** menghentikan scraping hari itu dan mengirim
   peringatan ke admin.
4. Halaman detail hanya diambil untuk lowongan **baru**, maksimal 30 per run,
   untuk mengekstrak daftar skill.
5. Scraping berjalan sinkron dan dipanggil lewat `asyncio.to_thread`, sehingga
   event loop bot tidak terblokir.

### 2. Penyimpanan

- Lowongan baru → `INSERT` dengan `first_seen_at`.
- Lowongan lama → hanya `last_seen_at` yang diperbarui.
- Tidak terlihat 3 run berturut-turut → `is_active = 0`, **tidak dihapus**.
- Seluruh penulisan berada dalam satu transaksi.

### 3. Notifikasi

Setelah run harian, setiap pelanggan menerima jumlah lowongan baru beserta
10 lowongan terbaru (lengkap dengan tautan). Isinya sama untuk semua
pelanggan karena filter kata kunci sudah dihapus, sehingga pesan cukup
disusun sekali lalu dikirim ke seluruh pelanggan.
Pesan panjang dipecah agar tidak melewati batas 4096 karakter Telegram.

---

## Catatan Teknis

**Mengapa skill disimpan sebagai JSON?**
Nama skill di Dealls dapat mengandung koma, misalnya
`Excel (VLOOKUP, HLOOKUP, PIVOT, IF, SUM, AVERAGE, etc)`. Bila disimpan
sebagai teks dipisah koma, nama tersebut akan terpotong menjadi beberapa
skill palsu. Karena itu `skills_text` menyimpan JSON, dan pembacaan lama
(format teks dipisah koma) tetap didukung agar data lama tidak rusak.

**Mengapa halaman detail memakai `__NEXT_DATA__`?**
Halaman detail Dealls menanam data lowongan sebagai JSON di dalam
`<script id="__NEXT_DATA__">`. Mengambil skill dari sana jauh lebih stabil
daripada bergantung pada struktur DOM yang bisa berubah kapan saja.

**Bagaimana lowongan yang terlewat tetap mendapat skill?**
Halaman detail hanya diambil maksimal `DETAIL_MAX_PER_RUN` per run. Bila
lowongan baru melebihi batas itu, sisanya akan terlewat. Karena run
berikutnya menganggapnya "sudah ada" dan tidak membuka detailnya lagi,
lowongan itu bisa berakhir tanpa skill selamanya.

Kolom `detail_checked` mencegah hal itu:

- `0` → halaman detail belum pernah dibuka; lowongan masuk daftar
  `jobs_needing_detail()` dan dilengkapi pada run berikutnya.
- `1` → sudah dibuka. Ditandai `1` **apa pun hasilnya**, supaya lowongan
  yang memang tidak punya skill tidak diambil berulang kali setiap run.

Dengan begitu, lowongan yang tertinggal otomatis menyusul tanpa perlu
menaikkan `DETAIL_MAX_PER_RUN`.

**Mengapa zona waktu harus eksplisit?**
`JobQueue.run_daily` memakai UTC bila `tzinfo` tidak diberikan, sehingga
07:00 akan berjalan pukul 14:00 WIB. Karena itu jadwal selalu dibuat dengan
`tzinfo=config.WIB`.

**Mengapa bot memakai kelas `BotTahanGangguan`?**
python-telegram-bot **tidak** mengulang request saat terjadi `TimedOut`
maupun `NetworkError`, dan batas waktu bawaannya hanya 5 detik. Pada
jaringan yang tidak stabil, satu gangguan sesaat langsung menggagalkan
perintah dan menulis traceback panjang ke log. `BotTahanGangguan` mengulang
request yang gagal karena jaringan dengan jeda bertambah, dan memakai batas
waktu 20 detik.

Dua pengecualian penting:

- `getUpdates` tidak diulang di lapisan ini, karena `run_polling` sudah
  menanganinya sendiri.
- `BadRequest` di PTB adalah **turunan** `NetworkError`, jadi harus
  dikecualikan lebih dulu. Tanpa itu, permintaan yang ditolak karena
  kesalahan data (mis. pesan terlalu panjang) akan diulang percuma.

---

## Batasan

- Struktur HTML dealls.com dapat berubah. Semua selector dikumpulkan di
  `config.py` agar perbaikan cukup dilakukan di satu tempat.
- Bot memakai mode *polling*, sehingga hanya boleh dijalankan satu proses.
  Bila dijalankan ganda, keduanya akan berebut update yang sama.

---

## Pemecahan Masalah

| Gejala | Penyebab dan solusi |
|---|---|
| `ZoneInfoNotFoundError` | Pasang `tzdata` (wajib di Windows) |
| `JobQueue tidak tersedia` | Pasang `pip install "python-telegram-bot[job-queue]"` |
| `TELEGRAM_BOT_TOKEN belum diisi` | Salin `.env.example` menjadi `.env` lalu isi token |
| Menu perintah tidak muncul | Ketik `/start` di chat; Telegram meng-cache menu per chat |
| `/scrape` tidak ada di menu | `ADMIN_CHAT_ID` belum benar, atau belum pernah chat bot dari akun admin |
| Dashboard kosong | Belum ada data; jalankan `python scraper.py` dulu |
| Port 5000 dipakai aplikasi lain | Set `DASHBOARD_PORT=5055` (di macOS port 5000 sering dipakai AirPlay) |
| Scraping status `dilarang` | `robots.txt` melarang jalur; bot berhenti dengan sengaja |
| Scraping status `diblokir` | Server menolak (403/429); tunggu dan coba lagi besok |
| Grafik kosong | Belum ada data; jalankan `/scrape` atau tunggu jadwal harian |
| `telegram.error.TimedOut` di log | Jaringan ke Telegram lambat/putus. Bot sudah mengulang otomatis; naikkan `TELEGRAM_READ_TIMEOUT` bila sering terjadi |
| `httpx.ConnectTimeout` berulang | Koneksi ke `api.telegram.org` terputus. Cek jaringan/VPN; bot akan mencoba lagi pada update berikutnya |
| Bot mati setelah `Conflict` | Ada dua proses bot dengan token sama; hentikan salah satu |

Log lengkap tersedia di `logs/bot.log`.
