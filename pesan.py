"""Teks dan format pesan bot.

Seluruh fungsi di sini murni: hanya menerima data dan mengembalikan teks
HTML. Tidak ada akses jaringan, database, maupun objek Telegram, sehingga
mudah diuji dengan pytest tanpa token bot.

Pemisahan ini mengikuti pola ``pesan.py`` pada proyek contoh Pekan 12.
"""

from __future__ import annotations

import html
from typing import Any, Sequence

import config
import db


# ---------------------------------------------------------------------------
# Utilitas dasar
# ---------------------------------------------------------------------------
def esc(teks: Any) -> str:
    """Escape teks agar aman dipakai dengan parse_mode HTML."""
    return html.escape(str(teks or ""), quote=False)


def esc_attr(teks: Any) -> str:
    """Escape untuk nilai atribut HTML, termasuk tanda kutip."""
    return html.escape(str(teks or ""), quote=True)


def potong(teks: str, batas: int) -> str:
    """Memotong teks panjang agar tidak melewati batas Telegram."""
    if len(teks) <= batas:
        return teks
    return teks[: batas - 3].rstrip() + "..."


def pecah_pesan(teks: str, batas: int | None = None) -> list[str]:
    """Memecah pesan panjang menjadi beberapa bagian.

    Pemecahan diusahakan pada batas baris agar tidak memotong tag HTML di
    tengah jalan.
    """
    batas = batas or config.TELEGRAM_MESSAGE_LIMIT
    if len(teks) <= batas:
        return [teks]

    bagian: list[str] = []
    sisa = teks
    while len(sisa) > batas:
        potong_di = sisa.rfind("\n", 0, batas)
        if potong_di <= 0:
            potong_di = batas
        bagian.append(sisa[:potong_di].rstrip())
        sisa = sisa[potong_di:].lstrip("\n")
    if sisa:
        bagian.append(sisa)
    return bagian


# ---------------------------------------------------------------------------
# Format lowongan
# ---------------------------------------------------------------------------
def format_lowongan(baris, dengan_skill: bool = True) -> str:
    """Membuat satu blok teks lowongan (format HTML)."""
    judul = esc(baris["title"])
    url = baris["url"] or ""
    if url:
        # Judul sekaligus menjadi tautan ke halaman detail.
        potongan = [f'<a href="{esc_attr(url)}">{judul}</a>']
    else:
        potongan = [f"<b>{judul}</b>"]

    if baris["company"]:
        potongan.append(esc(baris["company"]))

    detail: list[str] = []
    if baris["job_type"]:
        detail.append(esc(baris["job_type"]))
    if baris["location"]:
        lokasi = esc(baris["location"])
        if baris["work_mode"]:
            lokasi = f"{esc(baris['work_mode'])} · {lokasi}"
        detail.append(lokasi)
    if detail:
        potongan.append(" · ".join(detail))

    tambahan: list[str] = []
    if baris["experience_level"]:
        tambahan.append(esc(baris["experience_level"]))
    if baris["salary_text"]:
        tambahan.append(esc(baris["salary_text"]))
    if tambahan:
        potongan.append(" · ".join(tambahan))

    if dengan_skill:
        skills = db.skills_from_text(baris["skills_text"])
        if skills:
            potongan.append(f"<i>{esc(potong(', '.join(skills[:6]), 160))}</i>")

    if not baris["is_active"]:
        potongan.append("<i>(tidak aktif)</i>")

    return "\n".join(potongan)


def format_daftar_lowongan(
    rows: Sequence, total: int, offset: int, per_halaman: int
) -> str:
    """Membuat teks daftar lowongan lengkap dengan nomor urut."""
    blok: list[str] = []
    for indeks, baris in enumerate(rows, start=offset + 1):
        blok.append(f"<b>{indeks}.</b> {format_lowongan(baris)}")
    awal = offset + 1
    akhir = offset + len(rows)
    kepala = f"<b>Lowongan baru hari ini</b> ({awal}–{akhir} dari {total})"
    return kepala + "\n\n" + "\n\n".join(blok)


def format_hasil_cari(kata: str, rows: Sequence) -> str:
    """Teks hasil pencarian /cari."""
    blok = [f"<b>Hasil pencarian “{esc(kata)}”</b> ({len(rows)} lowongan)", ""]
    blok.extend(format_lowongan(baris) for baris in rows)
    return "\n\n".join(blok)


# ---------------------------------------------------------------------------
# Notifikasi harian
# ---------------------------------------------------------------------------
def format_notifikasi(total_baru: int, cocok: Sequence) -> list[str]:
    """Menyusun pesan notifikasi harian dari data yang sudah diambil.

    Parameter sengaja berupa data, bukan akses database, agar fungsinya murni
    dan bisa diuji tanpa menyiapkan tabel.

    Notifikasi tidak lagi difilter kata kunci: seluruh pelanggan menerima
    daftar lowongan baru terbaru.
    """
    if total_baru == 0:
        return [
            "<b>Rekap Lowongan Dealls</b>\n\n"
            "Tidak ada lowongan baru hari ini. Sampai jumpa besok!"
        ]

    kepala = (
        "<b>Rekap Lowongan Dealls</b>\n\n"
        f"Ada <b>{total_baru}</b> lowongan baru hari ini."
    )

    blok = [kepala]
    for baris in cocok:
        blok.append(format_lowongan(baris))
    blok.append("Lihat selengkapnya dengan /baru")

    return pecah_pesan("\n\n".join(blok))


# ---------------------------------------------------------------------------
# Teks statis perintah
# ---------------------------------------------------------------------------
BANTUAN = """<b>Perintah yang tersedia</b>

/start — mulai dan berlangganan notifikasi
/help — tampilkan bantuan ini
/baru — lowongan baru hari ini
/cari &lt;kata&gt; — cari lowongan
/berhenti — matikan notifikasi harian
/langganan — nyalakan notifikasi harian
/skill — skill paling sering diminta
/grafik harian|skill|perusahaan|lokasi — kirim gambar grafik
/export csv|xlsx|docx — unduh data lowongan
/scrape — jalankan scraping sekarang"""


SAMBUTAN = (
    "<b>Selamat datang di Bot Rekap Lowongan Dealls</b>\n\n"
    "Bot ini mengambil lowongan dari dealls.com setiap hari secara otomatis, "
    "menyimpannya secara akumulatif, lalu mengirim ringkasan lowongan baru.\n\n"
    "Anda sudah terdaftar sebagai pelanggan notifikasi harian.\n\n"
)


# ---------------------------------------------------------------------------
# Menu perintah (tombol di sisi kiri kolom pesan Telegram)
# ---------------------------------------------------------------------------
# Daftar ini dipakai untuk ``set_my_commands`` sehingga perintah muncul
# sebagai menu yang bisa diklik, tidak perlu diketik manual.
# Format: (perintah, deskripsi). Deskripsi maksimal 256 karakter.
MENU_PERINTAH: list[tuple[str, str]] = [
    ("baru", "Lowongan baru hari ini"),
    ("cari", "Cari lowongan berdasarkan kata"),
    ("skill", "Skill yang paling sering diminta"),
    ("grafik", "Kirim grafik analisis"),
    ("export", "Unduh data (csv / xlsx / docx)"),
    ("langganan", "Nyalakan notifikasi harian"),
    ("berhenti", "Matikan notifikasi harian"),
    ("help", "Daftar perintah"),
]


def menu_untuk_admin() -> list[tuple[str, str]]:
    """Menu lengkap termasuk perintah khusus admin."""
    return MENU_PERINTAH + [("scrape", "Jalankan scraping sekarang (admin)")]
