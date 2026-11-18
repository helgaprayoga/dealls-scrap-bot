"""Uji modul pesan.py — pembentukan teks bot tanpa Telegram.

Sejak teks dipindahkan dari bot.py, seluruh perilaku pesan dapat diuji
tanpa token, tanpa jaringan, dan tanpa objek Update.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import db  # noqa: E402
import pesan  # noqa: E402


def baris(**ubah):
    dasar = {
        "slug": "a~b",
        "title": "Software Engineer",
        "company": "PT A",
        "job_type": "Penuh waktu",
        "location": "Jakarta",
        "work_mode": "On-site",
        "experience_level": "Min. 1 years of experience",
        "salary_text": "Negotiable",
        "url": "https://dealls.com/loker/a~b",
        "source": "IT",
        "skills_text": db.skills_to_json(["Python", "SQL"]),
        "is_active": 1,
    }
    dasar.update(ubah)
    return dasar


# ---------------------------------------------------------------------------
# Escape
# ---------------------------------------------------------------------------
def test_esc_mengamankan_html():
    assert pesan.esc("<b>&") == "&lt;b&gt;&amp;"


def test_esc_attr_mengamankan_kutip():
    assert "&quot;" in pesan.esc_attr('a"b')


def test_esc_none_kosong():
    assert pesan.esc(None) == ""


# ---------------------------------------------------------------------------
# Pemecahan pesan
# ---------------------------------------------------------------------------
def test_pesan_pendek_tidak_dipecah():
    assert pesan.pecah_pesan("halo") == ["halo"]


def test_pesan_panjang_dipecah_dan_isi_utuh():
    teks = "\n".join(f"baris-{i}" for i in range(400))
    bagian = pesan.pecah_pesan(teks, batas=500)
    assert len(bagian) > 1
    assert all(len(b) <= 500 for b in bagian)
    assert "\n".join(bagian) == teks


def test_pecah_pesan_tanpa_baris_baru():
    bagian = pesan.pecah_pesan("x" * 300, batas=100)
    assert all(len(b) <= 100 for b in bagian)
    assert "".join(bagian) == "x" * 300


# ---------------------------------------------------------------------------
# Format lowongan
# ---------------------------------------------------------------------------
def test_lowongan_memakai_tautan():
    teks = pesan.format_lowongan(baris())
    assert '<a href="https://dealls.com/loker/a~b">' in teks


def test_lowongan_menggabungkan_detail():
    teks = pesan.format_lowongan(baris())
    assert "On-site · Jakarta" in teks
    assert "Python" in teks


def test_lowongan_tidak_aktif_ditandai():
    assert "tidak aktif" in pesan.format_lowongan(baris(is_active=0))


def test_lowongan_judul_berbahaya_ter_escape():
    teks = pesan.format_lowongan(baris(title="<script>x</script>", url=""))
    assert "<script>" not in teks
    assert "&lt;script&gt;" in teks


def test_lowongan_tanpa_url_memakai_bold():
    assert pesan.format_lowongan(baris(url="")).startswith("<b>")


def test_daftar_lowongan_bernomor():
    teks = pesan.format_daftar_lowongan([baris(), baris()], total=2, offset=0, per_halaman=10)
    assert "<b>1.</b>" in teks
    assert "<b>2.</b>" in teks
    assert "1–2 dari 2" in teks


def test_hasil_cari_memuat_jumlah():
    teks = pesan.format_hasil_cari("python", [baris()])
    assert "1 lowongan" in teks
    assert "python" in teks


# ---------------------------------------------------------------------------
# Notifikasi — kini fungsi murni
# ---------------------------------------------------------------------------
def test_notifikasi_tanpa_lowongan_baru():
    hasil = pesan.format_notifikasi(0, [])
    assert len(hasil) == 1
    assert "Tidak ada lowongan baru" in hasil[0]


def test_notifikasi_menampilkan_jumlah_dan_lowongan():
    hasil = pesan.format_notifikasi(3, [baris()])
    gabungan = "\n".join(hasil)
    assert "3" in gabungan
    assert "Software Engineer" in gabungan


def test_notifikasi_tidak_menyebut_kata_kunci():
    """Filter kata kunci sudah dihapus; pesan tidak boleh menyinggungnya."""
    hasil = pesan.format_notifikasi(2, [baris()])
    gabungan = "\n".join(hasil)
    assert "/keyword" not in gabungan
    assert "kata kunci" not in gabungan.lower()


def test_notifikasi_panjang_dipecah():
    banyak = [baris(slug=f"s{i}~b", url=f"https://x/{i}", title=f"Posisi {i}") for i in range(300)]
    hasil = pesan.format_notifikasi(300, banyak)
    assert len(hasil) > 1
    assert all(len(h) <= config.TELEGRAM_MESSAGE_LIMIT for h in hasil)


def test_notifikasi_judul_berbahaya_ter_escape():
    hasil = pesan.format_notifikasi(1, [baris(title="<script>x</script>")])
    assert "<script>" not in "\n".join(hasil)


# ---------------------------------------------------------------------------
# Menu perintah
# ---------------------------------------------------------------------------
def test_menu_tidak_kosong():
    assert len(pesan.MENU_PERINTAH) >= 8


def test_menu_berisi_perintah_utama():
    nama = [p for p, _ in pesan.MENU_PERINTAH]
    for wajib in ("baru", "cari", "skill", "grafik", "export"):
        assert wajib in nama


def test_menu_tidak_lagi_memuat_keyword_dan_statistik():
    """Keduanya sudah dihapus dari bot."""
    nama = [p for p, _ in pesan.MENU_PERINTAH]
    assert "keyword" not in nama
    assert "statistik" not in nama


def test_setiap_menu_punya_deskripsi():
    for perintah, deskripsi in pesan.MENU_PERINTAH:
        assert deskripsi.strip(), f"/{perintah} tanpa deskripsi"
        assert len(deskripsi) <= 256


def test_nama_perintah_valid_untuk_telegram():
    # Telegram hanya menerima huruf kecil, angka, dan garis bawah.
    for perintah, _ in pesan.MENU_PERINTAH:
        assert perintah.islower()
        assert perintah.replace("_", "").isalnum()
        assert 1 <= len(perintah) <= 32


def test_menu_admin_menambahkan_scrape():
    nama = [p for p, _ in pesan.menu_untuk_admin()]
    assert "scrape" in nama
    assert len(pesan.menu_untuk_admin()) == len(pesan.MENU_PERINTAH) + 1


def test_menu_pengguna_biasa_tanpa_scrape():
    nama = [p for p, _ in pesan.MENU_PERINTAH]
    assert "scrape" not in nama


# ---------------------------------------------------------------------------
# Teks statis
# ---------------------------------------------------------------------------
def test_bantuan_memuat_semua_perintah():
    for perintah, _ in pesan.MENU_PERINTAH:
        assert f"/{perintah}" in pesan.BANTUAN


def test_sambutan_memuat_judul():
    assert "Selamat datang" in pesan.SAMBUTAN
