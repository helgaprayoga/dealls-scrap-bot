"""Uji parser lowongan memakai HTML sampel.

Seluruh uji berjalan tanpa akses internet: fixture dibaca dari folder
``tests/fixtures``.
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


@pytest.fixture(scope="module")
def listing_html() -> str:
    return (FIXTURES / "listing.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def detail_html() -> str:
    return (FIXTURES / "detail.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def postings(listing_html: str) -> list[scraper.JobPosting]:
    return scraper.parse_listing(listing_html, source="IT & Software Engineering")


# ---------------------------------------------------------------------------
# Pengambilan slug
# ---------------------------------------------------------------------------
def test_slug_from_href_mengambil_bagian_setelah_loker():
    href = "/loker/golang-developer-10~pt-lawencon-internasional"
    assert scraper.slug_from_href(href) == "golang-developer-10~pt-lawencon-internasional"


def test_slug_from_href_membuang_query_string():
    href = "/loker/it-support-57~segari?ref=homepage"
    assert scraper.slug_from_href(href) == "it-support-57~segari"


def test_slug_from_href_url_penuh():
    href = "https://dealls.com/loker/it-support-57~segari"
    assert scraper.slug_from_href(href) == "it-support-57~segari"


# ---------------------------------------------------------------------------
# Jumlah dan deduplikasi
# ---------------------------------------------------------------------------
def test_jumlah_lowongan_setelah_deduplikasi(postings):
    # Lima kartu di fixture, tetapi kartu ketiga duplikat kartu pertama.
    assert len(postings) == 4


def test_tautan_navigasi_tidak_dianggap_lowongan(postings):
    slugs = [posting.slug for posting in postings]
    assert "posisi" not in " ".join(slugs)
    assert all("~" in slug for slug in slugs)


def test_slug_unik(postings):
    slugs = [posting.slug for posting in postings]
    assert len(slugs) == len(set(slugs))


def test_urutan_kemunculan_pertama_dipertahankan(postings):
    assert postings[0].slug == "golang-developer-10~pt-lawencon-internasional"
    assert postings[1].slug == "project-support-intern-mandarin-speaker~pt-smart-tbk"


# ---------------------------------------------------------------------------
# Isi kartu
# ---------------------------------------------------------------------------
def test_judul_dan_perusahaan(postings):
    posting = postings[0]
    assert posting.title == "Golang Developer"
    assert posting.company == "PT Lawencon Internasional"


def test_nama_perusahaan_tanpa_sisa_lencana_svg(postings):
    for posting in postings:
        assert "<" not in posting.company
        assert "svg" not in posting.company.lower()


def test_tipe_lokasi_mode_dan_pengalaman(postings):
    posting = postings[0]
    assert posting.job_type == "Penuh waktu"
    assert posting.work_mode == "On-site"
    assert posting.location == "Jakarta Selatan"
    assert posting.experience_level == "Min. 4 years of experience"


def test_gaji_rupiah(postings):
    assert postings[0].salary_text == "Rp7.000.000 – 9.000.000"


def test_lokasi_hybrid_dipisah_dengan_benar(postings):
    # Kartu duplikat dibuang, sehingga kartu keempat fixture ada di indeks 2.
    posting = postings[2]
    assert posting.work_mode == "Hybrid"
    assert posting.location == "Jakarta Barat"
    assert posting.salary_text == "Negotiable"
    assert posting.experience_level == "Min. Freshgrad"


def test_kartu_magang_dikenali(postings):
    posting = postings[1]
    assert posting.job_type == "Magang"
    assert posting.experience_level == "Min. Sedang Kuliah (Non-Final Year)"
    # "Magang Berbayar" bukan nominal gaji, tetapi mengisi slot gaji
    # karena tidak ada baris gaji lain.
    assert posting.salary_text == "Magang Berbayar"


def test_kartu_tanpa_blok_meta_tetap_terbaca(postings):
    posting = postings[3]
    assert posting.title == "IT Support"
    assert posting.company == "Segari"
    assert posting.job_type == ""
    assert posting.location == ""
    assert posting.salary_text == ""


def test_url_dibentuk_dari_slug(postings):
    assert postings[0].url == (
        "https://dealls.com/loker/golang-developer-10~pt-lawencon-internasional"
    )


def test_sumber_tercatat_pada_setiap_lowongan(postings):
    assert all(posting.source == "IT & Software Engineering" for posting in postings)


# ---------------------------------------------------------------------------
# Ketahanan terhadap masukan tidak wajar
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("html", ["", "   ", "<html><body></body></html>"])
def test_html_kosong_menghasilkan_daftar_kosong(html):
    assert scraper.parse_listing(html) == []


def test_kartu_tanpa_judul_diabaikan():
    html = (
        '<a href="/loker/tanpa-judul~pt-contoh">'
        '<div class="px-4">'
        '<div class="mb-1.5"><div><span>Magang</span></div></div>'
        "</div></a>"
    )
    assert scraper.parse_listing(html) == []


def test_spasi_berlebih_dirapikan():
    html = (
        '<a href="/loker/berlebih~pt-contoh">'
        "<div>"
        "<h2>   Software   Engineer  </h2>"
        "<div>PT   Contoh   Indonesia</div>"
        "</div></a>"
    )
    postings = scraper.parse_listing(html)
    assert len(postings) == 1
    assert postings[0].title == "Software Engineer"
    assert postings[0].company == "PT Contoh Indonesia"


def test_href_tanpa_pemisah_slug_dilewati():
    html = '<a href="/loker/posisi/loker-it"><h2>Posisi IT</h2><div>PT A</div></a>'
    assert scraper.parse_listing(html) == []


# ---------------------------------------------------------------------------
# Parser halaman detail dan skill
# ---------------------------------------------------------------------------
def test_skill_diambil_dari_next_data(detail_html):
    skills = scraper.parse_detail_skills(detail_html)
    assert skills == [
        "Excel (VLOOKUP, HLOOKUP, PIVOT, IF, SUM, AVERAGE, etc)",
        "SQL & Database Management",
        "Dashboard",
    ]


def test_skill_duplikat_dibuang(detail_html):
    skills = scraper.parse_detail_skills(detail_html)
    assert len(skills) == len(set(skills))


def test_skill_disimpan_sebagai_json(detail_html):
    posting = scraper.JobPosting(
        slug="a~b", title="T", company="C",
        skills=scraper.parse_detail_skills(detail_html),
    )
    # Nama skill mengandung koma, sehingga format JSON wajib dipertahankan.
    simpan = posting.skills_text
    assert simpan.startswith("[")
    assert db.skills_from_text(simpan) == posting.skills


def test_skill_mengandung_koma_tidak_terpotong():
    posting = scraper.JobPosting(
        slug="a~b", title="T", company="C",
        skills=["Excel (VLOOKUP, HLOOKUP, PIVOT, IF, SUM)"],
    )
    hasil = db.skills_from_text(posting.skills_text)
    assert hasil == ["Excel (VLOOKUP, HLOOKUP, PIVOT, IF, SUM)"]


def test_skills_from_text_mendukung_format_lama():
    # Data lama tersimpan sebagai teks dipisah koma.
    assert db.skills_from_text("Python, SQL") == ["Python", "SQL"]


def test_skills_from_text_kosong():
    assert db.skills_from_text("") == []
    assert db.skills_from_text(None) == []


def test_detail_tanpa_next_data_menghasilkan_daftar_kosong():
    assert scraper.parse_detail_skills("<html><body><h1>Halo</h1></body></html>") == []


def test_detail_json_rusak_tidak_melempar_pengecualian():
    html = '<script id="__NEXT_DATA__">{bukan json}</script>'
    assert scraper.parse_detail_skills(html) == []


def test_next_data_tanpa_skill_menghasilkan_daftar_kosong():
    html = (
        '<script id="__NEXT_DATA__">'
        '{"props":{"pageProps":{"dehydratedState":{"queries":'
        '[{"state":{"data":{"role":"QA"}}}]}}}}'
        "</script>"
    )
    assert scraper.parse_detail_skills(html) == []


# ---------------------------------------------------------------------------
# Konfigurasi selector tetap terpusat
# ---------------------------------------------------------------------------
def test_selector_didefinisikan_di_config():
    for nama in (
        "SELECTOR_JOB_CARD",
        "SELECTOR_TITLE",
        "SELECTOR_COMPANY",
        "SELECTOR_META_ROWS",
        "NEXT_DATA_SCRIPT_ID",
    ):
        assert hasattr(config, nama)
        assert getattr(config, nama)


# ---------------------------------------------------------------------------
# Status outcome
# ---------------------------------------------------------------------------
def test_status_outcome_kosong_tanpa_sumber_adalah_gagal():
    outcome = scraper.ScrapeOutcome()
    assert outcome.status == "gagal"


def test_status_outcome_sukses_bila_ada_halaman_terbaca():
    outcome = scraper.ScrapeOutcome(
        sources=[scraper.SourceResult(source="A", pages_scraped=1)]
    )
    assert outcome.status == "sukses"


def test_status_outcome_sebagian_bila_ada_error_tapi_ada_halaman():
    outcome = scraper.ScrapeOutcome(
        error_message="sumber B gagal",
        sources=[scraper.SourceResult(source="A", pages_scraped=1)],
    )
    assert outcome.status == "sebagian"


def test_status_outcome_diblokir_menang():
    outcome = scraper.ScrapeOutcome(
        error_message="403", blocked_status=403,
        sources=[scraper.SourceResult(source="A", pages_scraped=1)],
    )
    assert outcome.blocked is True
    assert outcome.status == "diblokir"


def test_status_outcome_dilarang_robots_menang():
    outcome = scraper.ScrapeOutcome(
        error_message="robots.txt melarang",
        robots_disallowed=True,
        blocked_status=403,
        sources=[scraper.SourceResult(source="A", status="dilarang")],
    )
    assert outcome.status == "dilarang"


def test_sumber_gagal_tidak_dianggap_ok():
    gagal = scraper.SourceResult(source="A", pages_scraped=0, status="gagal")
    sukses = scraper.SourceResult(source="A", pages_scraped=1, status="sukses")
    assert gagal.ok is False
    assert sukses.ok is True


# ---------------------------------------------------------------------------
# Pelaporan error (regresi: pesan error hilang saat semua sumber gagal)
# ---------------------------------------------------------------------------
class _ScraperTanpaJaringan(scraper.DeallsScraper):
    """Scraper yang selalu gagal mengambil halaman."""

    def load_robots(self):
        from urllib.robotparser import RobotFileParser

        parser = RobotFileParser()
        parser.parse(["User-agent: *", "Allow: /"])
        self._robots = parser
        return parser

    def fetch(self, url: str) -> str:
        raise scraper.ScrapeError("jaringan mati")


class _ScraperDilarangRobots(scraper.DeallsScraper):
    """Scraper yang robots.txt-nya melarang seluruh jalur."""

    def load_robots(self):
        from urllib.robotparser import RobotFileParser

        parser = RobotFileParser()
        parser.parse(["User-agent: *", "Disallow: /"])
        self._robots = parser
        return parser


def test_error_message_terisi_saat_semua_sumber_gagal():
    outcome = _ScraperTanpaJaringan(
        sources=[{"name": "A", "url": "https://dealls.com/loker/a"}],
        pages_per_source=1,
    ).run()
    assert outcome.status == "gagal"
    # Pesan wajib ada agar peringatan ke admin tidak kosong.
    assert outcome.error_message
    assert "jaringan mati" in outcome.error_message


def test_robots_dilarang_menghentikan_scraping():
    outcome = _ScraperDilarangRobots(
        sources=[{"name": "A", "url": "https://dealls.com/loker/a"}],
        pages_per_source=1,
    ).run()
    assert outcome.robots_disallowed is True
    assert outcome.status == "dilarang"
    assert outcome.postings == []
    assert outcome.error_message
