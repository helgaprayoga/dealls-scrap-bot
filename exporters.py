"""Ekspor data lowongan ke CSV, XLSX, dan DOCX.

Setiap fungsi mengembalikan ``(buffer, nama_berkas)`` sehingga bot dapat
langsung mengirimkannya sebagai dokumen Telegram tanpa menyentuh disk.
"""

from __future__ import annotations

import csv
import html
import logging
import re
from datetime import datetime
from io import BytesIO, StringIO

import config
import db

logger = logging.getLogger(__name__)

# Urutan kolom yang dipakai pada CSV dan XLSX.
KOLOM = [
    ("title", "Judul"),
    ("company", "Perusahaan"),
    ("job_type", "Tipe"),
    ("work_mode", "Mode Kerja"),
    ("location", "Lokasi"),
    ("experience_level", "Pengalaman"),
    ("salary_text", "Gaji"),
    ("skills_text", "Skill"),
    ("source", "Sumber"),
    ("status", "Status"),
    ("first_seen_at", "Pertama Terlihat"),
    ("last_seen_at", "Terakhir Terlihat"),
    ("url", "URL"),
]


def _nama_berkas(ekstensi: str) -> str:
    return f"lowongan-dealls-{config.now_wib():%Y%m%d-%H%M}.{ekstensi}"


def _baris_lowongan(row) -> dict:
    """Mengubah sqlite3.Row menjadi dict siap ekspor."""
    data = {kunci: (row[kunci] or "") for kunci, _ in KOLOM if kunci != "status"}
    data["status"] = "Aktif" if row["is_active"] else "Tidak aktif"
    data["skills_text"] = db.skills_to_display(row["skills_text"])
    return data


def _ambil_data(limit: int) -> list[dict]:
    rows = db.latest_jobs(limit)
    return [_baris_lowongan(row) for row in rows]


def _ringkasan_ekspor() -> dict:
    stats = db.get_overall_stats()
    return {
        "dibuat_pada": config.now_wib().strftime("%d-%m-%Y %H:%M") + " WIB",
        "total": stats["total_jobs"],
        "aktif": stats["active_jobs"],
        "baru_hari_ini": stats["new_today"],
        "pelanggan": stats["subscribers"],
        "total_run": stats["total_runs"],
    }


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def ekspor_csv(limit: int = 1000) -> tuple[BytesIO, str]:
    """Ekspor seluruh lowongan terbaru ke CSV."""
    import pandas as pd

    data = _ambil_data(limit)
    kolom = [label for _, label in KOLOM]
    kunci = [kunci for kunci, _ in KOLOM]
    baris = [[item[kunci] for kunci in kunci] for item in data]

    # pandas menangani quoting dan pemisah secara konsisten, termasuk nilai
    # yang mengandung titik koma atau tanda kutip.
    kerangka = pd.DataFrame(baris, columns=kolom)
    teks = kerangka.to_csv(index=False, sep=";", lineterminator="\n")

    # BOM membuat Excel membaca UTF-8 dengan benar.
    buffer = BytesIO(teks.encode("utf-8-sig"))
    buffer.seek(0)
    logger.info("Ekspor CSV: %d baris.", len(data))
    return buffer, _nama_berkas("csv")


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------
def ekspor_xlsx(limit: int = 5000) -> tuple[BytesIO, str]:
    """Ekspor lowongan dan statistik ke XLSX dengan sheet terpisah."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    data = _ambil_data(limit)
    ringkasan = _ringkasan_ekspor()

    workbook = Workbook()

    # -- Sheet 1: lowongan --
    sheet = workbook.active
    sheet.title = "Lowongan"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F6FEB")
    for kolom, (_, label) in enumerate(KOLOM, start=1):
        sel = sheet.cell(row=1, column=kolom, value=label)
        sel.font = header_font
        sel.fill = header_fill
        sel.alignment = Alignment(horizontal="center", vertical="center")

    for baris_idx, baris in enumerate(data, start=2):
        for kolom, (kunci, _) in enumerate(KOLOM, start=1):
            sheet.cell(row=baris_idx, column=kolom, value=baris[kunci])

    lebar = {
        "Judul": 42, "Perusahaan": 30, "Tipe": 13, "Mode Kerja": 12,
        "Lokasi": 22, "Pengalaman": 26, "Gaji": 24, "Skill": 50,
        "Sumber": 26, "Status": 12, "Pertama Terlihat": 21,
        "Terakhir Terlihat": 21, "URL": 60,
    }
    for kolom, (_, label) in enumerate(KOLOM, start=1):
        sheet.column_dimensions[get_column_letter(kolom)].width = lebar.get(label, 18)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(KOLOM))}{max(len(data) + 1, 1)}"

    # -- Sheet 2: statistik --
    stat = workbook.create_sheet("Statistik")
    stat["A1"] = "Ringkasan Data Lowongan Dealls"
    stat["A1"].font = Font(bold=True, size=14)

    baris_statistik = [
        ("Dibuat pada", ringkasan["dibuat_pada"]),
        ("Total lowongan", ringkasan["total"]),
        ("Lowongan aktif", ringkasan["aktif"]),
        ("Lowongan baru hari ini", ringkasan["baru_hari_ini"]),
        ("Pelanggan aktif", ringkasan["pelanggan"]),
        ("Total run scraping", ringkasan["total_run"]),
    ]
    for i, (label, nilai) in enumerate(baris_statistik, start=3):
        stat.cell(row=i, column=1, value=label).font = Font(bold=True)
        stat.cell(row=i, column=2, value=nilai)

    baris = 3 + len(baris_statistik) + 2

    stat.cell(row=baris, column=1, value="10 Skill Teratas").font = Font(bold=True)
    baris += 1
    stat.cell(row=baris, column=1, value="Skill").font = Font(bold=True)
    stat.cell(row=baris, column=2, value="Jumlah").font = Font(bold=True)
    for skill, jumlah in db.top_skills(10):
        baris += 1
        stat.cell(row=baris, column=1, value=skill)
        stat.cell(row=baris, column=2, value=jumlah)

    baris += 2
    stat.cell(row=baris, column=1, value="10 Perusahaan Teratas").font = Font(bold=True)
    baris += 1
    stat.cell(row=baris, column=1, value="Perusahaan").font = Font(bold=True)
    stat.cell(row=baris, column=2, value="Jumlah").font = Font(bold=True)
    for perusahaan, jumlah in db.top_companies(10):
        baris += 1
        stat.cell(row=baris, column=1, value=perusahaan)
        stat.cell(row=baris, column=2, value=jumlah)

    baris += 2
    stat.cell(row=baris, column=1, value="10 Lokasi Teratas").font = Font(bold=True)
    baris += 1
    stat.cell(row=baris, column=1, value="Lokasi").font = Font(bold=True)
    stat.cell(row=baris, column=2, value="Jumlah").font = Font(bold=True)
    for lokasi, jumlah in db.top_locations(10):
        baris += 1
        stat.cell(row=baris, column=1, value=lokasi)
        stat.cell(row=baris, column=2, value=jumlah)

    stat.column_dimensions["A"].width = 48
    stat.column_dimensions["B"].width = 22

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    logger.info("Ekspor XLSX: %d baris.", len(data))
    return buffer, _nama_berkas("xlsx")


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
def _bersih_docx(teks: str) -> str:
    """Membuang karakter kontrol yang tidak diterima python-docx."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(teks or ""))


def ekspor_docx(limit: int = 200) -> tuple[BytesIO, str]:
    """Membuat dokumen Word berisi ringkasan dan tabel lowongan terbaru."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    data = _ambil_data(limit)
    ringkasan = _ringkasan_ekspor()

    dokumen = Document()

    judul = dokumen.add_heading("Rekap Lowongan Kerja Dealls", level=0)
    judul.alignment = WD_ALIGN_PARAGRAPH.CENTER

    sub = dokumen.add_paragraph(f"Dibuat pada {ringkasan['dibuat_pada']}")
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.runs[0].font.size = Pt(10)
    sub.runs[0].font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    dokumen.add_heading("Ringkasan Statistik", level=1)
    tabel_stat = dokumen.add_table(rows=0, cols=2)
    tabel_stat.style = "Light Grid Accent 1"
    for label, nilai in (
        ("Total lowongan", ringkasan["total"]),
        ("Lowongan aktif", ringkasan["aktif"]),
        ("Lowongan baru hari ini", ringkasan["baru_hari_ini"]),
        ("Total run scraping", ringkasan["total_run"]),
    ):
        sel = tabel_stat.add_row().cells
        sel[0].text = str(label)
        sel[1].text = str(nilai)

    dokumen.add_heading(f"Daftar {len(data)} Lowongan Terbaru", level=1)
    if not data:
        dokumen.add_paragraph("Belum ada data lowongan.")
    else:
        kolom = ["Judul", "Perusahaan", "Lokasi", "Tipe", "Gaji", "Skill"]
        kunci = ["title", "company", "location", "job_type", "salary_text", "skills_text"]
        tabel = dokumen.add_table(rows=1, cols=len(kolom))
        tabel.style = "Light Grid Accent 1"

        header = tabel.rows[0].cells
        for i, label in enumerate(kolom):
            header[i].text = label
            for paragraf in header[i].paragraphs:
                for run in paragraf.runs:
                    run.bold = True

        for baris in data:
            sel = tabel.add_row().cells
            for i, kunci_baris in enumerate(kunci):
                nilai = _bersih_docx(baris[kunci_baris])
                # Skill panjang dipotong agar tabel tetap terbaca.
                if kunci_baris == "skills_text" and len(nilai) > 80:
                    nilai = nilai[:77] + "..."
                sel[i].text = nilai

    dokumen.add_heading("Daftar Skill Teratas", level=1)
    skill = db.top_skills(10)
    if not skill:
        dokumen.add_paragraph("Belum ada data skill.")
    else:
        for urutan, (nama, jumlah) in enumerate(skill, start=1):
            dokumen.add_paragraph(
                f"{urutan}. {_bersih_docx(nama)} ({jumlah} lowongan)",
                style="List Number",
            )

    catatan = dokumen.add_paragraph()
    catatan.add_run(
        "Sumber data: dealls.com. Data dikumpulkan secara akumulatif oleh bot "
        "dan dapat berubah sewaktu-waktu."
    ).italic = True

    buffer = BytesIO()
    dokumen.save(buffer)
    buffer.seek(0)
    logger.info("Ekspor DOCX: %d baris.", len(data))
    return buffer, _nama_berkas("docx")


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
EKSPORTER = {
    "csv": ekspor_csv,
    "xlsx": ekspor_xlsx,
    "docx": ekspor_docx,
}


def buat_ekspor(jenis: str) -> tuple[BytesIO, str] | None:
    """Menjalankan ekspor sesuai jenis. None bila jenis tidak dikenal."""
    fungsi = EKSPORTER.get((jenis or "").strip().lower())
    if fungsi is None:
        return None
    return fungsi()
