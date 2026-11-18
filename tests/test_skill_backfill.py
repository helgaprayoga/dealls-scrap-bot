"""Uji pengisian skill: termasuk lowongan tertunda dari run sebelumnya.

Regresi yang dijaga: lowongan yang melewati batas DETAIL_MAX_PER_RUN harus
tetap mendapat skill pada run berikutnya, dan lowongan yang memang tanpa
skill tidak boleh diambil berulang kali.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import db  # noqa: E402
import scraper  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def basis(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "uji.db")
    db.init_db()
    return tmp_path


def simpan(*slugs, skills=None):
    """Menyimpan lowongan tanpa membuka halaman detail."""
    postings = [
        scraper.JobPosting(
            slug=s,
            title=f"Judul {s}",
            company="PT Contoh",
            url=f"https://dealls.com/loker/{s}",
            source="IT",
            skills=skills or [],
            detail_dicek=bool(skills),
        )
        for s in slugs
    ]
    db.save_outcome(
        scraper.ScrapeOutcome(
            sources=[scraper.SourceResult(source="IT", pages_scraped=1, postings=postings)]
        ),
        config.now_iso(),
    )


# ---------------------------------------------------------------------------
# Skema dan migrasi
# ---------------------------------------------------------------------------
def test_kolom_detail_checked_ada(basis):
    kolom = {r["name"] for r in db.fetch_all("PRAGMA table_info(jobs)")}
    assert "detail_checked" in kolom


def test_migrasi_database_lama(tmp_path, monkeypatch):
    """Database tanpa kolom detail_checked tetap bisa dipakai."""
    import sqlite3

    jalur = tmp_path / "lama.db"
    conn = sqlite3.connect(jalur)
    conn.executescript(
        """
        CREATE TABLE jobs (
            slug TEXT PRIMARY KEY, title TEXT NOT NULL,
            company TEXT DEFAULT '', job_type TEXT DEFAULT '',
            location TEXT DEFAULT '', work_mode TEXT DEFAULT '',
            experience_level TEXT DEFAULT '', salary_text TEXT DEFAULT '',
            url TEXT DEFAULT '', source TEXT DEFAULT '',
            skills_text TEXT DEFAULT '',
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1, miss_count INTEGER DEFAULT 0
        );
        INSERT INTO jobs (slug, title, url, first_seen_at, last_seen_at)
        VALUES ('a~b', 'Judul', 'https://x/a~b', '2026-01-01T00:00:00', '2026-01-01T00:00:00');
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(config, "DB_PATH", jalur)
    db.init_db()  # menjalankan migrasi

    kolom = {r["name"] for r in db.fetch_all("PRAGMA table_info(jobs)")}
    assert "detail_checked" in kolom
    assert db.count_jobs() == 1  # data lama tidak hilang


# ---------------------------------------------------------------------------
# Pemilihan lowongan yang butuh detail
# ---------------------------------------------------------------------------
def test_lowongan_tanpa_skill_masuk_daftar_tertunda(basis):
    simpan("a~x", "b~y")
    tertunda = db.jobs_needing_detail(10)
    assert {t["slug"] for t in tertunda} == {"a~x", "b~y"}


def test_lowongan_sudah_punya_skill_tidak_diambil_ulang(basis):
    simpan("a~x", skills=["Python"])
    assert db.jobs_needing_detail(10) == []


def test_menghormati_batas_limit(basis):
    simpan(*[f"s{i}~x" for i in range(10)])
    assert len(db.jobs_needing_detail(3)) == 3


def test_exclude_tidak_diambil(basis):
    simpan("a~x", "b~y")
    hasil = db.jobs_needing_detail(10, exclude={"a~x"})
    assert [t["slug"] for t in hasil] == ["b~y"]


def test_lowongan_nonaktif_dilewati(basis):
    simpan("a~x")
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET is_active = 0")
        conn.commit()
    assert db.jobs_needing_detail(10) == []


# ---------------------------------------------------------------------------
# Penandaan setelah dicek
# ---------------------------------------------------------------------------
def test_mark_detail_checked_menyimpan_skill(basis):
    simpan("a~x")
    db.mark_detail_checked("a~x", ["Python", "SQL"])
    row = db.fetch_one("SELECT skills_text, detail_checked FROM jobs WHERE slug='a~x'")
    assert row["detail_checked"] == 1
    assert db.skills_from_text(row["skills_text"]) == ["Python", "SQL"]


def test_mark_detail_checked_tanpa_skill_tetap_ditandai(basis):
    """Lowongan yang memang tanpa skill tidak boleh diambil berulang kali."""
    simpan("a~x")
    db.mark_detail_checked("a~x", [])
    row = db.fetch_one("SELECT skills_text, detail_checked FROM jobs WHERE slug='a~x'")
    assert row["detail_checked"] == 1
    assert row["skills_text"] == ""
    # Tidak muncul lagi di daftar tertunda.
    assert db.jobs_needing_detail(10) == []


def test_mark_detail_checked_tidak_menimpa_skill_lama(basis):
    simpan("a~x", skills=["Python"])
    db.mark_detail_checked("a~x", [])
    row = db.fetch_one("SELECT skills_text FROM jobs WHERE slug='a~x'")
    assert db.skills_from_text(row["skills_text"]) == ["Python"]


def test_lowongan_baru_yang_sudah_dicek_langsung_bertanda(basis):
    """Regresi: INSERT harus menulis detail_checked=1 bila sudah diambil."""
    simpan("a~x", skills=["Go"])
    row = db.fetch_one("SELECT detail_checked FROM jobs WHERE slug='a~x'")
    assert row["detail_checked"] == 1


# ---------------------------------------------------------------------------
# Perilaku run
# ---------------------------------------------------------------------------
def test_run_mengisi_lowongan_tertunda(basis):
    """Lowongan lama yang tertinggal tetap dilengkapi pada run berikutnya."""
    simpan("lama~x")
    assert db.fetch_one("SELECT detail_checked FROM jobs WHERE slug='lama~x'")[0] == 0

    html_detail = (FIXTURES / "detail.html").read_text(encoding="utf-8")

    class Palsu(scraper.DeallsScraper):
        def load_robots(self):
            from urllib.robotparser import RobotFileParser

            rp = RobotFileParser()
            rp.parse(["User-agent: *", "Allow: /"])
            self._robots = rp
            return rp

        def fetch(self, url):
            return html_detail

        def scrape_source(self, source):
            # Tidak ada lowongan baru pada run ini.
            return scraper.SourceResult(source=source["name"], pages_scraped=1)

    tertunda = db.jobs_needing_detail(10)
    assert len(tertunda) == 1

    Palsu(detail_enabled=True).run(known_slugs=db.known_slugs(), tertunda=tertunda)

    row = db.fetch_one("SELECT skills_text, detail_checked FROM jobs WHERE slug='lama~x'")
    assert row["detail_checked"] == 1
    assert "Dashboard" in row["skills_text"]


def test_enrich_mendahulukan_lowongan_baru(basis):
    """Kuota dipakai lowongan baru lebih dulu, sisanya untuk yang tertunda."""
    html_detail = (FIXTURES / "detail.html").read_text(encoding="utf-8")
    dipanggil: list[str] = []

    class Palsu(scraper.DeallsScraper):
        def load_robots(self):
            from urllib.robotparser import RobotFileParser

            rp = RobotFileParser()
            rp.parse(["User-agent: *", "Allow: /"])
            self._robots = rp
            return rp

        def fetch(self, url):
            dipanggil.append(url)
            return html_detail

    baru = [scraper.JobPosting(slug=f"baru{i}~x", title="T", company="PT A",
                               url=f"https://x/baru{i}~x", source="IT")
            for i in range(5)]
    tertunda = [{"slug": f"lama{i}~x", "url": f"https://x/lama{i}~x"} for i in range(5)]

    p = Palsu(detail_enabled=True, detail_max=3)
    p.enrich_with_skills(baru, tertunda)

    assert len(dipanggil) == 3
    assert all("baru" in u for u in dipanggil)


def test_enrich_tidak_jalan_bila_detail_dimatikan(basis):
    class Palsu(scraper.DeallsScraper):
        def fetch(self, url):
            raise AssertionError("tidak boleh mengambil halaman")

    p = Palsu(detail_enabled=False)
    p.enrich_with_skills([scraper.JobPosting(slug="a~x", title="T", company="PT A",
                                            url="https://x/a~x", source="IT")])
