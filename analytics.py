"""Statistik dan grafik lowongan.

Modul ini hanya membaca dari database dan menghasilkan teks atau gambar.
Pembuatan grafik memakai backend ``Agg`` agar tidak memerlukan layar dan
aman dijalankan dari proses bot.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime
from io import BytesIO

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402

logger = logging.getLogger(__name__)

# Jenis grafik yang didukung beserta judulnya.
JENIS_GRAFIK = {
    "harian": "Jumlah lowongan baru per hari",
    "skill": "10 skill paling sering diminta",
    "perusahaan": "10 perusahaan paling banyak membuka lowongan",
    "lokasi": "10 lokasi paling banyak membuka lowongan",
}

WARNA_UTAMA = "#1f6feb"
WARNA_AKSEN = "#009C9A"


# ---------------------------------------------------------------------------
# Ringkasan teks
# ---------------------------------------------------------------------------
def skill_teks(limit: int = 10) -> str:
    """Daftar skill tersering untuk perintah /skill."""
    daftar = db.top_skills(limit)
    if not daftar:
        return (
            "Belum ada data skill.\n"
            "Skill diambil dari halaman detail lowongan baru, jadi pastikan "
            "<code>DETAIL_SCRAPING_ENABLED=1</code> dan sudah ada run scraping."
        )
    baris = [f"<b>{len(daftar)} skill paling sering diminta</b>", ""]
    for urutan, (skill, jumlah) in enumerate(daftar, start=1):
        baris.append(f"{urutan}. {html.escape(skill)} — {jumlah} lowongan")
    return "\n".join(baris)


# ---------------------------------------------------------------------------
# Pembuatan grafik
# ---------------------------------------------------------------------------
def _gaya_sumbu(ax, judul: str, sumbu_x: str = "", sumbu_y: str = "") -> None:
    ax.set_title(judul, fontsize=13, fontweight="bold", pad=14)
    if sumbu_x:
        ax.set_xlabel(sumbu_x, fontsize=10)
    if sumbu_y:
        ax.set_ylabel(sumbu_y, fontsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="x", linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=9)


def _simpan(fig) -> BytesIO:
    """Menyimpan figure ke buffer PNG dan menutupnya."""
    buffer = BytesIO()
    fig.tight_layout()
    fig.savefig(buffer, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return buffer


def _format_tanggal(teks: str) -> str:
    """Mengubah 2026-11-15 menjadi 15/11."""
    try:
        return datetime.strptime(teks, "%Y-%m-%d").strftime("%d/%m")
    except (TypeError, ValueError):
        return str(teks)


def _grafik_batang_mendatar(
    label: list[str], nilai: list[int], judul: str, sumbu: str
) -> BytesIO:
    """Grafik batang mendatar; label panjang tetap terbaca."""
    tinggi = max(3.2, 0.45 * len(label) + 1.6)
    fig, ax = plt.subplots(figsize=(8.5, tinggi))
    posisi = range(len(label))
    ax.barh(list(posisi), nilai, color=WARNA_AKSEN, height=0.62)
    ax.set_yticks(list(posisi))
    ax.set_yticklabels(label)
    ax.invert_yaxis()
    for p, v in zip(posisi, nilai):
        ax.text(v, p, f" {v}", va="center", fontsize=9, color="#333333")
    ax.set_xlim(0, max(nilai) * 1.18 if nilai else 1)
    _gaya_sumbu(ax, judul, sumbu_x=sumbu)
    return _simpan(fig)


def grafik_harian(days: int = 14) -> BytesIO | None:
    """Jumlah lowongan baru per hari."""
    data = db.daily_new_counts(days)
    if not data:
        return None
    label = [_format_tanggal(tanggal) for tanggal, _ in data]
    nilai = [jumlah for _, jumlah in data]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.bar(label, nilai, color=WARNA_UTAMA, width=0.6)
    for x, v in zip(label, nilai):
        ax.text(x, v, str(v), ha="center", va="bottom", fontsize=8)
    ax.margins(y=0.15)
    _gaya_sumbu(ax, "Jumlah lowongan baru per hari", sumbu_x="Tanggal (hari/bulan)", sumbu_y="Lowongan")
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.grid(axis="x", visible=False)
    if len(label) > 10:
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    return _simpan(fig)


def grafik_skill(limit: int = 10) -> BytesIO | None:
    data = db.top_skills(limit)
    if not data:
        return None
    label = [nama[:38] + ("…" if len(nama) > 38 else "") for nama, _ in data]
    nilai = [jumlah for _, jumlah in data]
    return _grafik_batang_mendatar(label, nilai, "Skill paling sering diminta", "Jumlah lowongan")


def grafik_perusahaan(limit: int = 10) -> BytesIO | None:
    data = db.top_companies(limit)
    if not data:
        return None
    label = [nama[:38] + ("…" if len(nama) > 38 else "") for nama, _ in data]
    nilai = [jumlah for _, jumlah in data]
    return _grafik_batang_mendatar(label, nilai, "Perusahaan paling banyak membuka lowongan", "Jumlah lowongan")


def grafik_lokasi(limit: int = 10) -> BytesIO | None:
    data = db.top_locations(limit)
    if not data:
        return None
    label = [nama[:38] + ("…" if len(nama) > 38 else "") for nama, _ in data]
    nilai = [jumlah for _, jumlah in data]
    return _grafik_batang_mendatar(label, nilai, "Lokasi paling banyak membuka lowongan", "Jumlah lowongan")


def buat_grafik(jenis: str) -> tuple[BytesIO, str] | None:
    """Membuat grafik sesuai jenis.

    Mengembalikan ``(buffer_png, judul)`` atau ``None`` bila jenis tidak
    dikenal maupun datanya masih kosong.
    """
    jenis = (jenis or "").strip().lower()
    pembuat = {
        "harian": grafik_harian,
        "skill": grafik_skill,
        "perusahaan": grafik_perusahaan,
        "lokasi": grafik_lokasi,
    }
    if jenis not in pembuat:
        return None

    buffer = pembuat[jenis]()
    if buffer is None:
        return None
    return buffer, JENIS_GRAFIK[jenis]
