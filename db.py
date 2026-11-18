"""Lapisan database SQLite untuk bot rekap lowongan Dealls.

Prinsip penyimpanan bersifat akumulatif:

* Lowongan yang belum pernah ada dimasukkan dengan ``first_seen_at``.
* Lowongan yang sudah ada hanya diperbarui ``last_seen_at``-nya.
* Lowongan tidak pernah dihapus. Jika tidak terlihat pada beberapa run
  berturut-turut, ``is_active`` diubah menjadi 0.

Semua penulisan dilakukan di dalam satu transaksi agar data tidak setengah
tersimpan ketika terjadi kegagalan.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

import config

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    slug             TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    company          TEXT NOT NULL DEFAULT '',
    job_type         TEXT NOT NULL DEFAULT '',
    location         TEXT NOT NULL DEFAULT '',
    work_mode        TEXT NOT NULL DEFAULT '',
    experience_level TEXT NOT NULL DEFAULT '',
    salary_text      TEXT NOT NULL DEFAULT '',
    url              TEXT NOT NULL DEFAULT '',
    source           TEXT NOT NULL DEFAULT '',
    skills_text      TEXT NOT NULL DEFAULT '',
    -- 1 bila halaman detail lowongan ini sudah pernah dibuka untuk
    -- mengambil skill. Halaman yang sudah dicek tidak diambil ulang,
    -- meskipun ternyata tidak punya skill sama sekali.
    detail_checked   INTEGER NOT NULL DEFAULT 0,
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    is_active        INTEGER NOT NULL DEFAULT 1,
    miss_count       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs (first_seen_at);
CREATE INDEX IF NOT EXISTS idx_jobs_active     ON jobs (is_active);
CREATE INDEX IF NOT EXISTS idx_jobs_company    ON jobs (company);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    source        TEXT NOT NULL,
    pages_scraped INTEGER NOT NULL DEFAULT 0,
    jobs_found    INTEGER NOT NULL DEFAULT 0,
    jobs_new      INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_started ON scrape_runs (started_at);

CREATE TABLE IF NOT EXISTS users (
    chat_id    INTEGER PRIMARY KEY,
    subscribed INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER,
    command    TEXT NOT NULL,
    args       TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log (created_at);
"""


# ---------------------------------------------------------------------------
# Koneksi
# ---------------------------------------------------------------------------
@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Membuka koneksi SQLite dan menutupnya otomatis."""
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Membuat seluruh tabel dan indeks bila belum ada."""
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrasi(conn)
        conn.commit()
    logger.info("Database siap di %s", config.DB_PATH)


def _migrasi(conn: sqlite3.Connection) -> None:
    """Menambahkan kolom baru pada database lama.

    ``CREATE TABLE IF NOT EXISTS`` tidak mengubah tabel yang sudah ada,
    sehingga kolom baru perlu ditambahkan terpisah. Database yang dibuat
    sebelum ada kolom ``detail_checked`` tetap bisa dipakai.
    """
    kolom = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "detail_checked" not in kolom:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN detail_checked INTEGER NOT NULL DEFAULT 0"
        )
        logger.info("Migrasi: kolom detail_checked ditambahkan ke tabel jobs.")


def fetch_all(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    """Menjalankan SELECT dan mengembalikan semua baris."""
    with connect() as conn:
        return conn.execute(sql, tuple(params)).fetchall()


def fetch_one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    """Menjalankan SELECT dan mengembalikan satu baris pertama."""
    with connect() as conn:
        return conn.execute(sql, tuple(params)).fetchone()


# ---------------------------------------------------------------------------
# Penyimpanan hasil scraping
# ---------------------------------------------------------------------------
def save_outcome(outcome, run_started_at: str) -> dict:
    """Menyimpan hasil scraping secara akumulatif.

    Menerima objek ``ScrapeOutcome`` dari scraper. Mengembalikan ringkasan
    berisi jumlah run yang dicatat, lowongan terbaca, dan lowongan baru.
    """
    run_finished_at = config.now_iso()
    total_found = 0
    total_new = 0
    runs_logged = 0

    with connect() as conn:
        known = {
            row["slug"]
            for row in conn.execute("SELECT slug FROM jobs").fetchall()
        }

        for source_result in outcome.sources:
            # Sumber yang gagal total dilewati: ketiadaan data bukan bukti
            # bahwa lowongannya sudah tutup, sehingga miss_count tidak boleh
            # dinaikkan.
            source_ok = source_result.ok
            seen_slugs: list[str] = []
            new_count = 0

            for posting in source_result.postings:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO jobs (
                        slug, title, company, job_type, location, work_mode,
                        experience_level, salary_text, url, source, skills_text,
                        detail_checked,
                        first_seen_at, last_seen_at, is_active, miss_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
                    """,
                    (
                        posting.slug,
                        posting.title,
                        posting.company,
                        posting.job_type,
                        posting.location,
                        posting.work_mode,
                        posting.experience_level,
                        posting.salary_text,
                        posting.url,
                        posting.source,
                        posting.skills_text,
                        1 if getattr(posting, "detail_dicek", False) else 0,
                        run_finished_at,
                        run_finished_at,
                    ),
                )
                if cursor.rowcount == 1:
                    new_count += 1
                    known.add(posting.slug)
                seen_slugs.append(posting.slug)

            # Lowongan yang terlihat selalu diperbarui waktunya, termasuk dari
            # sumber yang hanya terbaca sebagian.
            if seen_slugs:
                placeholders = ",".join("?" for _ in seen_slugs)
                conn.execute(
                    f"""
                    UPDATE jobs
                       SET last_seen_at = ?, is_active = 1, miss_count = 0
                     WHERE slug IN ({placeholders})
                    """,
                    (run_finished_at, *seen_slugs),
                )

            if source_ok:
                # Hanya sumber yang terbaca penuh boleh dipakai menghitung
                # lowongan yang "tidak terlihat lagi". Sumber yang gagal atau
                # sebagian dilewati agar lowongan aktif tidak salah ditutup.
                if seen_slugs:
                    miss_clause = f"AND slug NOT IN ({placeholders})"
                    miss_params: tuple = (
                        config.INACTIVE_AFTER_MISSES,
                        source_result.source,
                        *seen_slugs,
                    )
                else:
                    miss_clause = ""
                    miss_params = (config.INACTIVE_AFTER_MISSES, source_result.source)

                # Setelah beberapa run berturut-turut tanpa kemunculan,
                # lowongan ditandai tidak aktif tetapi tidak dihapus.
                conn.execute(
                    f"""
                    UPDATE jobs
                       SET miss_count = miss_count + 1,
                           is_active  = CASE
                                          WHEN miss_count + 1 >= ? THEN 0
                                          ELSE is_active
                                        END
                     WHERE source = ?
                       AND is_active = 1
                       {miss_clause}
                    """,
                    miss_params,
                )

            status = source_result.status if not source_ok else "sukses"
            conn.execute(
                """
                INSERT INTO scrape_runs (
                    started_at, finished_at, source, pages_scraped,
                    jobs_found, jobs_new, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_started_at,
                    run_finished_at,
                    source_result.source,
                    source_result.pages_scraped,
                    source_result.found,
                    new_count,
                    status,
                    source_result.error_message or outcome.error_message,
                ),
            )
            runs_logged += 1
            total_found += source_result.found
            total_new += new_count

        # Kegagalan total (mis. diblokir robots.txt) tetap dicatat.
        if runs_logged == 0:
            conn.execute(
                """
                INSERT INTO scrape_runs (
                    started_at, finished_at, source, pages_scraped,
                    jobs_found, jobs_new, status, error_message
                ) VALUES (?, ?, ?, 0, 0, 0, ?, ?)
                """,
                (
                    run_started_at,
                    run_finished_at,
                    "semua sumber",
                    outcome.status,
                    outcome.error_message,
                ),
            )
            runs_logged = 1

        conn.commit()

    logger.info(
        "Run tersimpan: %d sumber, %d lowongan terbaca, %d lowongan baru.",
        runs_logged, total_found, total_new,
    )
    return {
        "runs": runs_logged,
        "found_jobs": total_found,
        "new_jobs": total_new,
        "finished_at": run_finished_at,
    }


def known_slugs() -> set[str]:
    """Seluruh slug yang sudah pernah tersimpan."""
    with connect() as conn:
        return {row["slug"] for row in conn.execute("SELECT slug FROM jobs")}


# ---------------------------------------------------------------------------
# Pengambilan skill dari halaman detail
# ---------------------------------------------------------------------------
def jobs_needing_detail(limit: int, exclude: set[str] | None = None) -> list[dict]:
    """Lowongan yang halaman detailnya belum pernah dibuka.

    Menyaring dengan ``detail_checked = 0`` supaya lowongan yang memang tidak
    punya skill tidak diambil berulang kali setiap run.

    ``exclude`` berisi slug yang sudah diambil pada run ini (tidak perlu
    diambil dua kali).
    """
    exclude = exclude or set()
    baris = fetch_all(
        """
        SELECT slug, url FROM jobs
         WHERE detail_checked = 0
           AND url <> ''
           AND is_active = 1
         ORDER BY first_seen_at DESC
        """
    )
    hasil = [
        {"slug": row["slug"], "url": row["url"]}
        for row in baris
        if row["slug"] not in exclude
    ]
    return hasil[:limit]


def mark_detail_checked(slug: str, skills: Iterable[str]) -> None:
    """Menandai halaman detail sudah dibuka, sekaligus menyimpan skillnya."""
    daftar = [s.strip() for s in skills if s and s.strip()]
    tersimpan = skills_to_json(daftar)
    with connect() as conn:
        conn.execute(
            """
            UPDATE jobs
               SET detail_checked = 1,
                   skills_text = CASE WHEN ? <> '' THEN ? ELSE skills_text END
             WHERE slug = ?
            """,
            (tersimpan, tersimpan, slug),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Kueri lowongan
# ---------------------------------------------------------------------------
JOB_COLUMNS = (
    "slug, title, company, job_type, location, work_mode, experience_level, "
    "salary_text, url, source, skills_text, first_seen_at, last_seen_at, is_active"
)


def _today_prefix() -> str:
    return config.now_iso()[:10]


def get_new_jobs_today(limit: int, offset: int = 0) -> list[sqlite3.Row]:
    """Lowongan yang pertama kali terlihat hari ini (WIB)."""
    return fetch_all(
        f"""
        SELECT {JOB_COLUMNS} FROM jobs
         WHERE substr(first_seen_at, 1, 10) = ?
         ORDER BY first_seen_at DESC, title ASC
         LIMIT ? OFFSET ?
        """,
        (_today_prefix(), limit, offset),
    )


def count_new_jobs_today() -> int:
    row = fetch_one(
        "SELECT COUNT(*) AS n FROM jobs WHERE substr(first_seen_at, 1, 10) = ?",
        (_today_prefix(),),
    )
    return int(row["n"]) if row else 0


def search_jobs(keyword: str, limit: int = 20) -> list[sqlite3.Row]:
    """Mencari lowongan di judul, perusahaan, dan skill."""
    pattern = f"%{keyword.strip()}%"
    return fetch_all(
        f"""
        SELECT {JOB_COLUMNS} FROM jobs
         WHERE title LIKE ? OR company LIKE ? OR skills_text LIKE ?
         ORDER BY is_active DESC, first_seen_at DESC
         LIMIT ?
        """,
        (pattern, pattern, pattern, limit),
    )


def get_jobs_by_slugs(slugs: Iterable[str]) -> list[sqlite3.Row]:
    """Mengambil beberapa lowongan sekaligus berdasarkan slug."""
    slug_list = list(slugs)
    if not slug_list:
        return []
    placeholders = ",".join("?" for _ in slug_list)
    return fetch_all(
        f"SELECT {JOB_COLUMNS} FROM jobs WHERE slug IN ({placeholders})",
        slug_list,
    )


def latest_jobs(limit: int = 200) -> list[sqlite3.Row]:
    """Lowongan terbaru untuk keperluan ekspor."""
    return fetch_all(
        f"""
        SELECT {JOB_COLUMNS} FROM jobs
         ORDER BY first_seen_at DESC, title ASC
         LIMIT ?
        """,
        (limit,),
    )


def skills_to_json(skills: Iterable[str]) -> str:
    """Menyimpan daftar skill sebagai JSON.

    Bukan dipisah koma, karena nama skill Dealls sendiri mengandung koma
    (mis. ``Excel (VLOOKUP, HLOOKUP, PIVOT...)``) sehingga pemisahan koma
    akan memotong nama skill menjadi beberapa bagian.
    """
    bersih = [s.strip() for s in skills if s and s.strip()]
    return json.dumps(bersih, ensure_ascii=False) if bersih else ""


def skills_from_text(text: str | None) -> list[str]:
    """Membaca kembali daftar skill dari kolom ``skills_text``.

    Mendukung format JSON (format sekarang) dan teks dipisah koma (format
    lama) agar data lama tetap terbaca.
    """
    if not text:
        return []
    teks = text.strip()
    if teks.startswith("["):
        try:
            data = json.loads(teks)
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
        return []
    return [bagian.strip() for bagian in teks.split(",") if bagian.strip()]


def skills_to_display(text: str | None, separator: str = ", ") -> str:
    """Menyajikan skill sebagai teks yang enak dibaca."""
    return separator.join(skills_from_text(text))


# ---------------------------------------------------------------------------
# Statistik
# ---------------------------------------------------------------------------
def get_overall_stats() -> dict:
    """Ringkasan angka untuk /statistik."""
    total = fetch_one("SELECT COUNT(*) AS n FROM jobs")
    active = fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE is_active = 1")
    today = count_new_jobs_today()
    subscribers = fetch_one("SELECT COUNT(*) AS n FROM users WHERE subscribed = 1")
    runs = fetch_one("SELECT COUNT(*) AS n FROM scrape_runs")
    last_run = fetch_one(
        "SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1"
    )
    return {
        "total_jobs": int(total["n"]) if total else 0,
        "active_jobs": int(active["n"]) if active else 0,
        "new_today": today,
        "subscribers": int(subscribers["n"]) if subscribers else 0,
        "total_runs": int(runs["n"]) if runs else 0,
        "last_run": dict(last_run) if last_run else None,
    }


def get_recent_runs(limit: int = 5) -> list[sqlite3.Row]:
    return fetch_all(
        "SELECT * FROM scrape_runs ORDER BY id DESC LIMIT ?", (limit,)
    )


def count_jobs() -> int:
    row = fetch_one("SELECT COUNT(*) AS n FROM jobs")
    return int(row["n"]) if row else 0


def count_active_jobs() -> int:
    row = fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE is_active = 1")
    return int(row["n"]) if row else 0


def daily_new_counts(days: int = 14) -> list[tuple[str, int]]:
    """Jumlah lowongan baru per tanggal, urut dari terlama."""
    rows = fetch_all(
        """
        SELECT substr(first_seen_at, 1, 10) AS tanggal, COUNT(*) AS jumlah
          FROM jobs
         GROUP BY tanggal
         ORDER BY tanggal DESC
         LIMIT ?
        """,
        (days,),
    )
    return [(row["tanggal"], int(row["jumlah"])) for row in reversed(rows)]


def top_skills(limit: int = 10) -> list[tuple[str, int]]:
    """Skill yang paling sering muncul.

    skills_text disimpan sebagai JSON, sehingga penghitungan dilakukan di
    sisi Python. Ini juga menjaga nama skill yang mengandung koma tetap utuh.
    """
    rows = fetch_all("SELECT skills_text FROM jobs WHERE skills_text <> ''")
    counter: Counter[str] = Counter()
    for row in rows:
        for skill in skills_from_text(row["skills_text"]):
            counter[skill] += 1
    return counter.most_common(limit)


def top_companies(limit: int = 10) -> list[tuple[str, int]]:
    rows = fetch_all(
        """
        SELECT company, COUNT(*) AS jumlah
          FROM jobs
         WHERE company <> ''
         GROUP BY company
         ORDER BY jumlah DESC, company ASC
         LIMIT ?
        """,
        (limit,),
    )
    return [(row["company"], int(row["jumlah"])) for row in rows]


def top_locations(limit: int = 10) -> list[tuple[str, int]]:
    rows = fetch_all(
        """
        SELECT location, COUNT(*) AS jumlah
          FROM jobs
         WHERE location <> ''
         GROUP BY location
         ORDER BY jumlah DESC, location ASC
         LIMIT ?
        """,
        (limit,),
    )
    return [(row["location"], int(row["jumlah"])) for row in rows]


def newest_first_seen_date() -> str | None:
    row = fetch_one("SELECT MIN(first_seen_at) AS d FROM jobs")
    if row and row["d"]:
        return row["d"][:10]
    return None


# ---------------------------------------------------------------------------
# Pengguna dan langganan
# ---------------------------------------------------------------------------
def ensure_user(chat_id: int) -> None:
    """Mendaftarkan pengguna bila belum ada."""
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (chat_id, subscribed, created_at) VALUES (?, 1, ?)",
            (chat_id, config.now_iso()),
        )
        conn.commit()


def set_subscription(chat_id: int, subscribed: bool) -> None:
    """Menyalakan atau mematikan notifikasi harian."""
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (chat_id, subscribed, created_at) VALUES (?, ?, ?)",
            (chat_id, 1 if subscribed else 0, config.now_iso()),
        )
        conn.execute(
            "UPDATE users SET subscribed = ? WHERE chat_id = ?",
            (1 if subscribed else 0, chat_id),
        )
        conn.commit()


def get_subscribers() -> list[int]:
    return [int(row["chat_id"]) for row in fetch_all(
        "SELECT chat_id FROM users WHERE subscribed = 1 ORDER BY chat_id"
    )]


def is_subscribed(chat_id: int) -> bool:
    row = fetch_one("SELECT subscribed FROM users WHERE chat_id = ?", (chat_id,))
    return bool(row and row["subscribed"])


# ---------------------------------------------------------------------------
# Log aktivitas
# ---------------------------------------------------------------------------
def log_activity(chat_id: int | None, command: str, args: str = "") -> None:
    """Mencatat setiap perintah yang dipakai pengguna."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO activity_log (chat_id, command, args, created_at) VALUES (?, ?, ?, ?)",
            (chat_id, command, args, config.now_iso()),
        )
        conn.commit()


def count_activity() -> int:
    row = fetch_one("SELECT COUNT(*) AS n FROM activity_log")
    return int(row["n"]) if row else 0
