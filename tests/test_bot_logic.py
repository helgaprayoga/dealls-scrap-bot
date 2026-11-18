"""Uji logika pesan bot tanpa koneksi Telegram.

Yang diuji adalah fungsi murni: escape HTML, pemecahan pesan, penyusunan
notifikasi, dan penjadwalan. Tidak ada request jaringan.
"""

from __future__ import annotations

import sys
from datetime import datetime, time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import db  # noqa: E402
import bot  # noqa: E402


@pytest.fixture()
def basis_data(tmp_path, monkeypatch):
    """Database sementara yang bersih untuk setiap uji."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "uji.db")
    db.init_db()
    yield


def baris_lowongan(**ubah):
    """Membuat objek mirip sqlite3.Row untuk uji format pesan."""
    dasar = {
        "slug": "contoh~pt-a",
        "title": "Software Engineer",
        "company": "PT A",
        "job_type": "Penuh waktu",
        "location": "Jakarta Selatan",
        "work_mode": "On-site",
        "experience_level": "Min. 1 years of experience",
        "salary_text": "Rp5.000.000 – 7.000.000",
        "url": "https://dealls.com/loker/contoh~pt-a",
        "source": "IT",
        "skills_text": db.skills_to_json(["Python", "SQL"]),
        "first_seen_at": config.now_iso(),
        "last_seen_at": config.now_iso(),
        "is_active": 1,
    }
    dasar.update(ubah)
    return dasar


# ---------------------------------------------------------------------------
# Escape HTML
# ---------------------------------------------------------------------------
def test_esc_mengamankan_karakter_html():
    assert bot.esc("<b>&") == "&lt;b&gt;&amp;"


def test_esc_kutip_tidak_diubah_pada_teks_biasa():
    # quote=False agar tanda kutip pada judul tidak menjadi &quot;
    assert bot.esc('Judul "keren"') == 'Judul "keren"'


def test_esc_attr_mengamankan_kutip():
    # esc_attr kini berada di pesan.py; bot mengimpornya ulang.
    assert "&quot;" in bot.pesan.esc_attr('a"b')


def test_esc_none_menjadi_string_kosong():
    assert bot.esc(None) == ""


# ---------------------------------------------------------------------------
# Pemecahan pesan
# ---------------------------------------------------------------------------
def test_pesan_pendek_tidak_dipecah():
    assert bot.pecah_pesan("halo") == ["halo"]


def test_pesan_panjang_dipecah_di_bawah_batas():
    teks = "\n".join(["baris isi pesan"] * 500)
    bagian = bot.pecah_pesan(teks, batas=4096)
    assert len(bagian) > 1
    for isi in bagian:
        assert len(isi) <= 4096


def test_pecah_pesan_mempertahankan_isi():
    teks = "\n".join(f"baris-{i}" for i in range(300))
    gabungan = "\n".join(bot.pecah_pesan(teks, batas=500))
    assert gabungan == teks


def test_pecah_pesan_tanpa_baris_baru_tetap_aman():
    bagian = bot.pecah_pesan("x" * 300, batas=100)
    assert all(len(b) <= 100 for b in bagian)
    assert "".join(bagian) == "x" * 300


# ---------------------------------------------------------------------------
# Format satu lowongan
# ---------------------------------------------------------------------------
def test_format_lowongan_memakai_tautan_html():
    teks = bot.format_lowongan(baris_lowongan())
    assert '<a href="https://dealls.com/loker/contoh~pt-a">' in teks
    assert "Software Engineer" in teks


def test_format_lowongan_menggabungkan_mode_dan_lokasi():
    teks = bot.format_lowongan(baris_lowongan())
    assert "On-site · Jakarta Selatan" in teks


def test_format_lowongan_menampilkan_skill():
    teks = bot.format_lowongan(baris_lowongan())
    assert "Python" in teks
    assert "SQL" in teks


def test_format_lowongan_menandai_tidak_aktif():
    teks = bot.format_lowongan(baris_lowongan(is_active=0))
    assert "tidak aktif" in teks


def test_format_lowongan_mengamankan_judul_berbahaya():
    teks = bot.format_lowongan(
        baris_lowongan(title="<script>alert(1)</script>", url="", company="")
    )
    assert "<script>" not in teks
    assert "&lt;script&gt;" in teks


def test_format_lowongan_tanpa_url_memakai_bold():
    teks = bot.format_lowongan(baris_lowongan(url=""))
    assert teks.startswith("<b>")


def test_format_lowongan_field_kosong_tidak_error():
    teks = bot.format_lowongan(
        baris_lowongan(job_type="", location="", work_mode="", salary_text="",
                       experience_level="", skills_text="", company="")
    )
    assert "Software Engineer" in teks


# ---------------------------------------------------------------------------
# Notifikasi
# ---------------------------------------------------------------------------
def test_notifikasi_tanpa_lowongan_baru(basis_data):
    pesan = bot.susun_notifikasi()
    assert len(pesan) == 1
    assert "Tidak ada lowongan baru" in pesan[0]


def test_notifikasi_menampilkan_lowongan_baru(basis_data):
    posting = __import__("scraper").JobPosting(
        slug="x~a", title="Data Analyst", company="PT X",
        url="https://dealls.com/loker/x~a", source="IT",
    )
    hasil = __import__("scraper").ScrapeOutcome(
        sources=[__import__("scraper").SourceResult(
            source="IT", pages_scraped=1, postings=[posting])]
    )
    db.save_outcome(hasil, config.now_iso())

    gabungan = "\n".join(bot.susun_notifikasi())
    assert "Data Analyst" in gabungan
    assert "1" in gabungan


def test_notifikasi_sama_untuk_semua_pelanggan(basis_data):
    """Filter kata kunci sudah dihapus, jadi isi notifikasi tidak bergantung
    pada pelanggan."""
    from scraper import JobPosting, ScrapeOutcome, SourceResult

    posting = JobPosting(
        slug="y~a", title="Akuntan", company="PT Y",
        url="https://dealls.com/loker/y~a", source="IT",
    )
    db.save_outcome(
        ScrapeOutcome(sources=[SourceResult(
            source="IT", pages_scraped=1, postings=[posting])]),
        config.now_iso(),
    )

    assert bot.susun_notifikasi() == bot.susun_notifikasi()
    assert "Akuntan" in "\n".join(bot.susun_notifikasi())


def test_notifikasi_dibatasi_sepuluh_lowongan(basis_data):
    """Notifikasi memuat maksimal 10 lowongan sesuai batas per pesan."""
    from scraper import JobPosting, ScrapeOutcome, SourceResult

    banyak = [
        JobPosting(
            slug=f"j{i}~pt-{i}",
            title=f"Posisi Nomor {i} dengan Judul Cukup Panjang",
            company=f"PT Perusahaan {i}",
            location="Jakarta Selatan", work_mode="On-site",
            job_type="Penuh waktu", salary_text="Rp5.000.000",
            experience_level="Min. 1 years of experience",
            url=f"https://dealls.com/loker/j{i}~pt-{i}", source="IT",
        )
        for i in range(40)
    ]
    db.save_outcome(
        ScrapeOutcome(sources=[SourceResult(source="IT", pages_scraped=1, postings=banyak)]),
        config.now_iso(),
    )

    pesan = bot.susun_notifikasi()
    gabungan = "\n".join(pesan)
    for isi in pesan:
        assert len(isi) <= config.TELEGRAM_MESSAGE_LIMIT

    # Total disebut 40, tetapi daftar yang dikirim maksimal 10.
    assert "40" in gabungan
    jumlah_tautan = gabungan.count("https://dealls.com/loker/")
    assert jumlah_tautan <= config.JOBS_PER_PAGE


def test_notifikasi_panjang_dipecah_bila_melebihi_batas():
    """Isi notifikasi yang sangat panjang wajib dipecah, bukan dipotong."""
    isi_panjang = "\n\n".join(
        f'<a href="https://dealls.com/loker/x{i}~pt-a">Lowongan {i}</a>\nPT A'
        for i in range(400)
    )
    assert len(isi_panjang) > config.TELEGRAM_MESSAGE_LIMIT
    bagian = bot.pecah_pesan(isi_panjang)
    assert len(bagian) > 1
    for potongan in bagian:
        assert len(potongan) <= config.TELEGRAM_MESSAGE_LIMIT
    assert "".join(bagian).count("Lowongan") == 400


# ---------------------------------------------------------------------------
# Penjadwalan
# ---------------------------------------------------------------------------
def test_jadwal_memakai_zona_wib():
    # Regresi: tanpa tzinfo, JobQueue memakai UTC sehingga 07:00 WIB
    # akan berjalan pukul 14:00 WIB.
    waktu = time(hour=config.SCRAPE_HOUR, minute=config.SCRAPE_MINUTE, tzinfo=config.WIB)
    assert waktu.tzinfo is not None
    assert str(waktu.tzinfo) == "Asia/Jakarta"
    # time.utcoffset() selalu None, jadi offset dihitung lewat datetime.
    gabungan = datetime(
        2026, 11, 15, waktu.hour, waktu.minute, tzinfo=waktu.tzinfo
    )
    assert gabungan.utcoffset().total_seconds() == 7 * 3600
    assert gabungan.hour == 7


def test_default_jadwal_adalah_tujuh_pagi():
    assert config.SCRAPE_HOUR == 7
    assert config.SCRAPE_MINUTE == 0


# ---------------------------------------------------------------------------
# Tombol pagination
# ---------------------------------------------------------------------------
def test_tombol_berikutnya_muncul_bila_masih_ada():
    keyboard = bot._tombol_berikutnya(offset=0, total=25)
    assert keyboard is not None
    tombol = keyboard.inline_keyboard[0][0]
    assert tombol.callback_data == "baru:10"


def test_tombol_berikutnya_hilang_di_halaman_terakhir():
    assert bot._tombol_berikutnya(offset=20, total=25) is None


def test_filter_admin():
    class Chat:
        id = 42

    class Update:
        effective_chat = Chat()

    asli = config.ADMIN_CHAT_ID
    try:
        config.ADMIN_CHAT_ID = 42
        assert bot._boleh_admin(Update()) is True
        config.ADMIN_CHAT_ID = 99
        assert bot._boleh_admin(Update()) is False
    finally:
        config.ADMIN_CHAT_ID = asli
