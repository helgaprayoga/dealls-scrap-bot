"""Uji dashboard Flask memakai test client, tanpa server dan tanpa internet."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import db  # noqa: E402


@pytest.fixture()
def klien(tmp_path, monkeypatch):
    """Klien Flask dengan database sementara berisi beberapa lowongan."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "web.db")
    db.init_db()

    from scraper import JobPosting, ScrapeOutcome, SourceResult

    postings = [
        JobPosting(
            slug=f"u{i}~pt-{i}",
            title=f"Backend Engineer {i}",
            company=f"PT Contoh {i}",
            job_type="Penuh waktu",
            location="Jakarta Selatan" if i % 2 else "Bandung",
            work_mode="On-site",
            experience_level="Min. 2 years of experience",
            salary_text="Negotiable",
            url=f"https://dealls.com/loker/u{i}~pt-{i}",
            source="IT",
            skills=["Python", "PostgreSQL"] if i % 2 else ["Golang"],
        )
        for i in range(1, 31)
    ]
    db.save_outcome(
        ScrapeOutcome(
            sources=[SourceResult(source="IT", pages_scraped=1, postings=postings)]
        ),
        config.now_iso(),
    )

    import app as dashboard

    dashboard.app.config.update(TESTING=True)
    return dashboard.app.test_client()


# ---------------------------------------------------------------------------
# Halaman
# ---------------------------------------------------------------------------
def test_beranda_menampilkan_data(klien):
    resp = klien.get("/")
    assert resp.status_code == 200
    halaman = resp.data.decode()
    assert "Rekap Lowongan Dealls" in halaman
    assert "Backend Engineer 1" in halaman


def test_pencarian_menyaring_hasil(klien):
    halaman = klien.get("/?q=golang").data.decode()
    # Lowongan genap punya skill Golang, yang ganjil tidak.
    assert "Backend Engineer 2</a>" in halaman
    assert "Backend Engineer 1</a>" not in halaman


def test_pencarian_tanpa_hasil_tetap_200(klien):
    resp = klien.get("/?q=zzzznihil")
    assert resp.status_code == 200
    assert "Belum ada data" in resp.data.decode()


def test_filter_status(klien):
    assert klien.get("/?status=aktif").status_code == 200
    assert klien.get("/?status=nonaktif").status_code == 200
    # Nilai status tak dikenal dikembalikan ke "semua", bukan error.
    assert klien.get("/?status=ngawur").status_code == 200


def test_halaman_tidak_valid_tidak_error(klien):
    assert klien.get("/?halaman=abc").status_code == 200
    assert klien.get("/?halaman=-5").status_code == 200


def test_pagination(klien):
    halaman1 = klien.get("/?halaman=1").data.decode()
    assert "Halaman 1 dari" in halaman1
    assert "Berikutnya" in halaman1

    halaman2 = klien.get("/?halaman=2").data.decode()
    assert "Halaman 2 dari" in halaman2
    assert "Sebelumnya" in halaman2


def test_detail_lowongan(klien):
    resp = klien.get("/lowongan/u1~pt-1")
    assert resp.status_code == 200
    halaman = resp.data.decode()
    assert "Backend Engineer 1" in halaman
    assert "PostgreSQL" in halaman


def test_detail_slug_tidak_ada_404(klien):
    assert klien.get("/lowongan/tidak-ada~x").status_code == 404


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
def test_api_statistik(klien):
    data = klien.get("/api/statistik").get_json()
    assert data["total"] == 30
    assert data["aktif"] == 30
    assert data["baru_hari_ini"] == 30


def test_api_lowongan(klien):
    data = klien.get("/api/lowongan?q=golang").get_json()
    assert data["total"] == 15
    assert all("Golang" in str(item["skills"]) for item in data["data"])


@pytest.mark.parametrize("jenis", ["harian", "skill", "perusahaan", "lokasi"])
def test_grafik_mengirim_png(klien, jenis):
    resp = klien.get(f"/grafik/{jenis}")
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert resp.data[:4] == b"\x89PNG"


def test_grafik_jenis_tidak_dikenal_404(klien):
    assert klien.get("/grafik/ngawur").status_code == 404


@pytest.mark.parametrize("jenis", ["csv", "xlsx", "docx"])
def test_unduhan(klien, jenis):
    resp = klien.get(f"/unduh/{jenis}")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["Content-Disposition"]
    assert len(resp.data) > 1000


def test_unduhan_jenis_tidak_dikenal_404(klien):
    assert klien.get("/unduh/pdf").status_code == 404


# ---------------------------------------------------------------------------
# Keamanan
# ---------------------------------------------------------------------------
def test_judul_berbahaya_ter_escape(klien):
    from scraper import JobPosting, ScrapeOutcome, SourceResult

    db.save_outcome(
        ScrapeOutcome(
            sources=[
                SourceResult(
                    source="X",
                    pages_scraped=1,
                    postings=[
                        JobPosting(
                            slug="xss~x",
                            title="<script>alert('xss')</script>",
                            company="<b>PT Nakal</b>",
                            url="https://x/xss~x",
                            source="X",
                        )
                    ],
                )
            ]
        ),
        config.now_iso(),
    )

    halaman = klien.get("/?q=xss").data.decode()
    assert "<script>alert('xss')</script>" not in halaman
    assert "&lt;script&gt;" in halaman


def test_css_tersedia(klien):
    resp = klien.get("/static/style.css")
    assert resp.status_code == 200
    assert "--aksen" in resp.data.decode()
