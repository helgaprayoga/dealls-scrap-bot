"""Simpan JSON __NEXT_DATA__ satu lowongan ke berkas agar bisa dibaca manual.

Pakai:
    python cek_json.py <slug>
    python cek_json.py golang-developer-10~pt-lawencon-internasional

Hasil ditulis ke json-detail.json (seluruh isi) dan skill saja dicetak ke layar.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

import config
import scraper


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Pakai: python cek_json.py <slug>")
        print("Contoh: python cek_json.py golang-developer-10~pt-lawencon-internasional")
        print("\nSlug bisa dilihat dari URL lowongan:")
        print("  https://dealls.com/loker/<slug-ini>")
        return 2

    slug = argv[1].strip()
    url = f"{config.BASE_URL}/loker/{slug}"

    print(f"Mengambil: {url}")
    respons = requests.get(url, headers=config.HEADERS, timeout=config.REQUEST_TIMEOUT)
    respons.encoding = "utf-8"
    print(f"Status   : {respons.status_code} | ukuran HTML: {len(respons.text)} karakter")

    if respons.status_code != 200:
        print("Halaman tidak bisa diambil.")
        return 1

    data = scraper.extract_next_data(respons.text)
    if data is None:
        print("Tidak ada <script id=\"__NEXT_DATA__\"> di halaman ini.")
        return 1

    # Berkas lengkap: seluruh JSON, sudah dirapikan agar mudah dibaca.
    berkas = Path("json-detail.json")
    berkas.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nJSON lengkap  : {berkas.resolve()}")
    print(f"Ukuran berkas : {berkas.stat().st_size / 1024:.1f} KB")

    payload = scraper._find_job_payload(data)
    if payload is None:
        print("Objek lowongan tidak ditemukan di dalam JSON.")
        return 1

    print("\n--- Lokasi data (untuk ditelusuri di berkas) ---")
    print("props.pageProps.dehydratedState.queries[0].state.data")
    print(f"  role    : {payload.get('role')}")
    print(f"  company : {(payload.get('company') or {}).get('name')}")

    print("\n--- candidatePreference.skills (mentah) ---")
    skills = (payload.get("candidatePreference") or {}).get("skills") or []
    print(json.dumps(skills, ensure_ascii=False, indent=2))

    print("\n--- Hasil parse_detail_skills() ---")
    print(scraper.parse_detail_skills(respons.text))

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
