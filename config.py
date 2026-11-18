"""Konfigurasi terpusat untuk bot rekap lowongan Dealls.

Semua nilai yang bisa berubah ada di sini agar tidak tersebar di modul lain.
Rahasia (token, admin) dibaca dari berkas .env dan tidak pernah di-hardcode.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Zona waktu
# ---------------------------------------------------------------------------
# Seluruh penjadwalan dan penulisan waktu memakai WIB agar konsisten.
WIB = ZoneInfo("Asia/Jakarta")

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_wib() -> datetime:
    """Mengembalikan waktu sekarang dalam zona Asia/Jakarta."""
    return datetime.now(WIB)


def now_iso() -> str:
    """Waktu sekarang sebagai ISO-8601 dengan offset, mis. 2026-11-15T07:00:00+07:00.

    Format ini dipakai di database sehingga 10 karakter pertama selalu
    merupakan tanggal lokal WIB dan bisa dibandingkan langsung sebagai teks.
    """
    return now_wib().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Pembaca environment dengan nilai default
# ---------------------------------------------------------------------------
def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env_str(name, "1" if default else "0").strip().lower() in {
        "1", "true", "ya", "yes", "on",
    }


# ---------------------------------------------------------------------------
# Kredensial dan identitas bot
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = _env_str("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = _env_int("ADMIN_CHAT_ID", 0)

# ---------------------------------------------------------------------------
# Penjadwalan harian (WIB)
# ---------------------------------------------------------------------------
SCRAPE_HOUR = _env_int("SCRAPE_HOUR", 7)
SCRAPE_MINUTE = _env_int("SCRAPE_MINUTE", 0)

# ---------------------------------------------------------------------------
# Perilaku scraping yang sopan
# ---------------------------------------------------------------------------
# Jumlah halaman daftar yang diambil per sumber per hari.
PAGES_PER_SOURCE = _env_int("PAGES_PER_SOURCE", 3)
# Jeda acak antar request demi tidak membebani server.
REQUEST_DELAY_MIN = _env_float("REQUEST_DELAY_MIN", 2.0)
REQUEST_DELAY_MAX = _env_float("REQUEST_DELAY_MAX", 4.0)
REQUEST_TIMEOUT = _env_int("REQUEST_TIMEOUT", 15)
MAX_RETRIES = _env_int("MAX_RETRIES", 3)
BACKOFF_BASE = _env_float("BACKOFF_BASE", 2.0)

# Halaman detail hanya diambil untuk lowongan baru agar beban tetap kecil.
DETAIL_SCRAPING_ENABLED = _env_bool("DETAIL_SCRAPING_ENABLED", True)
DETAIL_MAX_PER_RUN = _env_int("DETAIL_MAX_PER_RUN", 30)

# Status HTTP yang membuat scraping hari itu dihentikan total.
STOP_STATUS_CODES = {403, 429}

# Lowongan dianggap tidak aktif setelah tidak terlihat pada N run berurutan.
INACTIVE_AFTER_MISSES = _env_int("INACTIVE_AFTER_MISSES", 3)

# ---------------------------------------------------------------------------
# Koneksi ke Telegram
# ---------------------------------------------------------------------------
# Default bawaan python-telegram-bot hanya 5 detik untuk connect dan read.
# Nilai itu terlalu ketat untuk jaringan yang tidak stabil, sehingga perintah
# bisa gagal hanya karena gangguan sesaat.
TELEGRAM_CONNECT_TIMEOUT = _env_float("TELEGRAM_CONNECT_TIMEOUT", 20.0)
TELEGRAM_READ_TIMEOUT = _env_float("TELEGRAM_READ_TIMEOUT", 20.0)
TELEGRAM_WRITE_TIMEOUT = _env_float("TELEGRAM_WRITE_TIMEOUT", 20.0)
TELEGRAM_POOL_TIMEOUT = _env_float("TELEGRAM_POOL_TIMEOUT", 5.0)

# Berapa kali request yang gagal karena jaringan diulang oleh bot sendiri.
# PTB tidak me-retry otomatis saat TimedOut/NetworkError.
TELEGRAM_MAX_RETRY = _env_int("TELEGRAM_MAX_RETRY", 3)
TELEGRAM_RETRY_DELAY = _env_float("TELEGRAM_RETRY_DELAY", 1.5)

# Batas pengiriman pesan Telegram.
TELEGRAM_MESSAGE_LIMIT = 4096
JOBS_PER_PAGE = 10

# ---------------------------------------------------------------------------
# Sumber data
# ---------------------------------------------------------------------------
BASE_URL = "https://dealls.com"

# Daftar sumber mudah ditambah: cukup tambahkan entri baru di list ini.
# "name" dipakai sebagai kolom jobs.source dan kolom scrape_runs.source.
#
# Sumber "Magang" (https://dealls.com/loker/tipe/loker-magang) sengaja tidak
# dipakai. Lowongan magang dari kategori IT tetap ikut terbaca selama muncul
# di halaman posisi IT di bawah.
SOURCES = [
    {
        "name": "IT & Software Engineering",
        "url": f"{BASE_URL}/loker/posisi/loker-it-and-software-engineering",
        "category": "it",
    },
]

ROBOTS_URL = f"{BASE_URL}/robots.txt"

# User-Agent jelas agar pemilik situs tahu siapa yang mengakses.
USER_AGENT = (
    "DeallsJobDigestBot/1.0 (bot rekap lowongan pribadi; "
    "menghormati robots.txt; kontak melalui Telegram)"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ---------------------------------------------------------------------------
# Selector HTML
# ---------------------------------------------------------------------------
# Selector dikumpulkan di sini dan sudah diverifikasi terhadap HTML asli
# dealls.com. Jangan menulis selector di modul lain.
#
# Setiap lowongan pada halaman daftar berupa satu <a> ke halaman detail dengan
# pola /loker/<slug-lowongan>~<slug-perusahaan>. Tautan navigasi seperti
# /loker/posisi/... juga cocok dengan prefix ini, sehingga hasilnya wajib
# disaring dengan syarat mengandung "~".
SELECTOR_JOB_CARD = 'a[href^="/loker/"]'
CARD_SLUG_SEPARATOR = "~"

# Judul lowongan.
SELECTOR_TITLE = "h2"

# Nama perusahaan berada pada <div> tepat setelah <h2>. Di dalamnya ada <svg>
# lencana terverifikasi yang tidak menghasilkan teks, jadi aman diambil teksnya.
SELECTOR_COMPANY = "h2 + div"

# Baris-baris meta (jenis pekerjaan, lokasi, pengalaman, gaji) adalah anak
# langsung dari <div class="mb-1.5 flex flex-col gap-1 lg:mb-3">.
# Titik pada "mb-1.5" harus di-escape di dalam selector CSS.
SELECTOR_META_ROWS = "div.mb-1\\.5 > div"

# Pemisah antara mode kerja dan kota pada baris lokasi, mis. "On-site • Jakarta".
LOCATION_SEPARATOR = "•"

# Label jenis pekerjaan yang dikenal. Dipakai untuk mengklasifikasikan baris
# meta berdasarkan isi, bukan berdasarkan urutan, karena jumlah baris bisa
# berbeda antar kartu (3 sampai 5 baris).
JOB_TYPE_LABELS = {
    "penuh waktu", "paruh waktu", "magang", "kontrak", "freelance",
    "full time", "part time", "internship", "contract", "temporary",
}

# Awalan baris tingkat pengalaman, mis. "Min. Freshgrad".
EXPERIENCE_PREFIX = "min."

# Penanda teks gaji.
SALARY_NEGOTIABLE = "negotiable"
SALARY_CURRENCY_PREFIX = "rp"

# ID <script> berisi JSON data halaman detail. Struktur ini memuat daftar
# keahlian pada props.pageProps.dehydratedState.queries[*].state.data.
NEXT_DATA_SCRIPT_ID = "__NEXT_DATA__"

# ---------------------------------------------------------------------------
# Lokasi berkas
# ---------------------------------------------------------------------------
DB_PATH = Path(_env_str("DB_PATH", str(BASE_DIR / "data.db")))
LOG_DIR = Path(_env_str("LOG_DIR", str(BASE_DIR / "logs")))
LOG_FILE = LOG_DIR / "bot.log"
LOG_MAX_BYTES = _env_int("LOG_MAX_BYTES", 2_000_000)
LOG_BACKUP_COUNT = _env_int("LOG_BACKUP_COUNT", 5)
LOG_LEVEL = _env_str("LOG_LEVEL", "INFO").upper()

# Direktori sementara untuk grafik dan berkas ekspor.
EXPORT_DIR = Path(_env_str("EXPORT_DIR", str(LOG_DIR / "exports")))
