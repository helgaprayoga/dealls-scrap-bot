"""Dashboard web untuk data lowongan Dealls.

Dashboard ini membaca database yang sama dengan bot Telegram
(``data.db``), jadi jalankan ``python scraper.py`` minimal sekali agar ada
data yang ditampilkan.

Jalankan::

    python app.py

Lalu buka http://127.0.0.1:5000

Catatan: dashboard ini hanya membaca data. Scraping dan pengiriman
notifikasi tetap dilakukan oleh bot.
"""

from __future__ import annotations

import csv
import io
import logging
import os

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    render_template,
    request,
)

import analytics
import config
import db
import exporters
import pesan

logger = logging.getLogger(__name__)

# Jumlah baris per halaman pada tabel lowongan.
PER_HALAMAN = 25

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


# ---------------------------------------------------------------------------
# Data untuk template
# ---------------------------------------------------------------------------
def _statistik() -> dict:
    """Angka ringkasan untuk kartu di bagian atas dashboard."""
    stats = db.get_overall_stats()
    return {
        "total": stats["total_jobs"],
        "aktif": stats["active_jobs"],
        "baru_hari_ini": stats["new_today"],
        "pelanggan": stats["subscribers"],
        "total_run": stats["total_runs"],
        "run_terakhir": stats["last_run"],
    }


def _lowongan(kata: str = "", status: str = "semua", halaman: int = 1) -> dict:
    """Ambil lowongan dengan filter dan pagination sederhana."""
    syarat: list[str] = []
    parameter: list = []

    if kata:
        pola = f"%{kata.strip()}%"
        syarat.append("(title LIKE ? OR company LIKE ? OR skills_text LIKE ?)")
        parameter.extend([pola, pola, pola])

    if status == "aktif":
        syarat.append("is_active = 1")
    elif status == "nonaktif":
        syarat.append("is_active = 0")

    klausa = f"WHERE {' AND '.join(syarat)}" if syarat else ""

    total_row = db.fetch_one(f"SELECT COUNT(*) AS n FROM jobs {klausa}", parameter)
    total = int(total_row["n"]) if total_row else 0

    halaman = max(1, halaman)
    offset = (halaman - 1) * PER_HALAMAN

    rows = db.fetch_all(
        f"""
        SELECT {db.JOB_COLUMNS} FROM jobs
        {klausa}
        ORDER BY is_active DESC, first_seen_at DESC, title ASC
        LIMIT ? OFFSET ?
        """,
        [*parameter, PER_HALAMAN, offset],
    )

    # Skill diubah menjadi daftar agar template bisa menampilkannya sebagai
    # label, bukan satu teks JSON panjang.
    data = []
    for row in rows:
        item = dict(row)
        item["skills"] = db.skills_from_text(row["skills_text"])[:8]
        data.append(item)

    return {
        "data": data,
        "total": total,
        "halaman": halaman,
        "total_halaman": max(1, (total + PER_HALAMAN - 1) // PER_HALAMAN),
        "per_halaman": PER_HALAMAN,
    }


# ---------------------------------------------------------------------------
# Template filter
# ---------------------------------------------------------------------------
@app.template_filter("tanggal")
def _filter_tanggal(nilai: str | None) -> str:
    """2026-11-15T07:00:00+07:00 -> 15 Nov 2026, 07:00."""
    if not nilai:
        return "-"
    teks = str(nilai)
    try:
        from datetime import datetime

        waktu = datetime.fromisoformat(teks)
        return waktu.strftime("%d %b %Y, %H:%M")
    except ValueError:
        return teks[:16].replace("T", " ")


@app.template_filter("angka")
def _filter_angka(nilai) -> str:
    """12345 -> 12.345 (pemisah ribuan gaya Indonesia)."""
    try:
        return f"{int(nilai):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(nilai)


@app.context_processor
def _variabel_umum() -> dict:
    return {
        "nama_aplikasi": "Rekap Lowongan Dealls",
        "jam_scraping": f"{config.SCRAPE_HOUR:02d}:{config.SCRAPE_MINUTE:02d}",
        "dibuat_pada": config.now_wib().strftime("%d %b %Y, %H:%M"),
    }


# ---------------------------------------------------------------------------
# Halaman
# ---------------------------------------------------------------------------
@app.route("/")
def beranda():
    """Halaman utama: ringkasan angka dan daftar lowongan."""
    kata = request.args.get("q", "").strip()
    status = request.args.get("status", "semua")
    if status not in {"semua", "aktif", "nonaktif"}:
        status = "semua"
    try:
        halaman = int(request.args.get("halaman", 1))
    except ValueError:
        halaman = 1

    hasil = _lowongan(kata, status, halaman)

    return render_template(
        "index.html",
        statistik=_statistik(),
        lowongan=hasil,
        kata=kata,
        status=status,
        skill_teratas=db.top_skills(8),
        perusahaan_teratas=db.top_companies(5),
        lokasi_teratas=db.top_locations(5),
        run_terakhir=db.get_recent_runs(5),
    )


@app.route("/lowongan/<path:slug>")
def detail_lowongan(slug: str):
    """Halaman detail satu lowongan."""
    row = db.fetch_one(
        f"SELECT {db.JOB_COLUMNS} FROM jobs WHERE slug = ?", (slug,)
    )
    if row is None:
        abort(404)

    item = dict(row)
    item["skills"] = db.skills_from_text(row["skills_text"])
    return render_template("detail.html", lowongan=item)


# ---------------------------------------------------------------------------
# Endpoint JSON dan grafik
# ---------------------------------------------------------------------------
@app.route("/api/statistik")
def api_statistik():
    return jsonify(_statistik())


@app.route("/api/lowongan")
def api_lowongan():
    kata = request.args.get("q", "").strip()
    status = request.args.get("status", "semua")
    try:
        halaman = int(request.args.get("halaman", 1))
    except ValueError:
        halaman = 1
    hasil = _lowongan(kata, status, halaman)
    return jsonify(
        {
            "total": hasil["total"],
            "halaman": hasil["halaman"],
            "total_halaman": hasil["total_halaman"],
            "data": hasil["data"],
        }
    )


@app.route("/grafik/<jenis>")
def grafik(jenis: str):
    """Mengirim grafik sebagai PNG, dibuat di memori."""
    hasil = analytics.buat_grafik(jenis)
    if hasil is None:
        abort(404)
    buffer, _judul = hasil
    return Response(buffer.getvalue(), mimetype="image/png")


# ---------------------------------------------------------------------------
# Unduhan
# ---------------------------------------------------------------------------
@app.route("/unduh/<jenis>")
def unduh(jenis: str):
    """Mengunduh data sebagai CSV, XLSX, atau DOCX."""
    if jenis not in exporters.EKSPORTER:
        abort(404)
    if db.count_jobs() == 0:
        abort(404)

    buffer, nama = exporters.buat_ekspor(jenis)
    return Response(
        buffer.getvalue(),
        mimetype="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{nama}"'},
    )


def main() -> None:
    """Menjalankan server pengembangan."""
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    db.init_db()

    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("DASHBOARD_PORT", "5000"))
    debug = os.getenv("DASHBOARD_DEBUG", "0") == "1"

    logger.info("Dashboard berjalan di http://%s:%d", host, port)
    # use_reloader dimatikan agar init_db tidak berjalan dua kali.
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    main()
