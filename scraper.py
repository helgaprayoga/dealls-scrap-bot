"""Pengambil dan pengurai lowongan dari dealls.com.

Modul ini sengaja dipisah menjadi dua lapisan:

1. Fungsi parser murni (``parse_listing`` dan ``parse_detail_skills``) yang
   hanya bekerja pada teks HTML. Fungsi ini tidak menyentuh jaringan sehingga
   mudah diuji dengan fixture.
2. Kelas ``DeallsScraper`` yang menangani robots.txt, jeda sopan, retry, dan
   pengambilan halaman detail.

Scraping bersifat sinkron. Pemanggil yang berjalan di dalam event loop
(seperti bot Telegram) wajib membungkusnya dengan ``asyncio.to_thread``.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
from dataclasses import dataclass, field
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

import config
import db as db_mod

logger = logging.getLogger(__name__)

WHITESPACE_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Pengecualian
# ---------------------------------------------------------------------------
class ScrapeError(Exception):
    """Kegagalan umum saat mengambil atau mengurai halaman."""


class RobotsDisallowedError(ScrapeError):
    """robots.txt melarang pengambilan jalur yang diminta."""


class ScrapeBlockedError(ScrapeError):
    """Server menolak akses (403/429); scraping harus dihentikan hari ini."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Struktur data
# ---------------------------------------------------------------------------
@dataclass
class JobPosting:
    """Satu lowongan hasil parsing halaman daftar."""

    slug: str
    title: str
    company: str
    job_type: str = ""
    location: str = ""
    work_mode: str = ""
    experience_level: str = ""
    salary_text: str = ""
    url: str = ""
    source: str = ""
    skills: list[str] = field(default_factory=list)
    # True bila halaman detail lowongan ini sudah dibuka pada run ini.
    # Dipakai saat menyimpan agar INSERT menulis detail_checked = 1.
    detail_dicek: bool = False

    @property
    def skills_text(self) -> str:
        """Skill dalam bentuk JSON untuk disimpan ke database.

        JSON dipakai, bukan teks dipisah koma, karena nama skill dari Dealls
        sendiri mengandung koma.
        """
        return db_mod.skills_to_json(self.skills)

    def as_dict(self) -> dict:
        return {
            "slug": self.slug,
            "title": self.title,
            "company": self.company,
            "job_type": self.job_type,
            "location": self.location,
            "work_mode": self.work_mode,
            "experience_level": self.experience_level,
            "salary_text": self.salary_text,
            "url": self.url,
            "source": self.source,
            "skills": list(self.skills),
        }


@dataclass
class SourceResult:
    """Hasil scraping untuk satu sumber."""

    source: str
    pages_scraped: int = 0
    postings: list[JobPosting] = field(default_factory=list)
    status: str = "sukses"
    error_message: str | None = None

    @property
    def found(self) -> int:
        return len(self.postings)

    @property
    def ok(self) -> bool:
        """True bila sumber ini benar-benar berhasil dibaca.

        Sumber yang gagal total tidak boleh dipakai untuk menghitung
        lowongan yang "tidak terlihat", karena ketiadaan data bukan berarti
        lowongannya sudah tutup.
        """
        return self.status == "sukses" and self.pages_scraped > 0


@dataclass
class ScrapeOutcome:
    """Hasil satu putaran scraping (semua sumber)."""

    sources: list[SourceResult] = field(default_factory=list)
    postings: list[JobPosting] = field(default_factory=list)
    error_message: str | None = None
    blocked_status: int | None = None
    robots_disallowed: bool = False

    @property
    def blocked(self) -> bool:
        return self.blocked_status is not None

    @property
    def pages_scraped(self) -> int:
        return sum(source.pages_scraped for source in self.sources)

    @property
    def status(self) -> str:
        """Status ringkas untuk kolom scrape_runs.status.

        Nilai yang dipakai: ``dilarang``, ``diblokir``, ``gagal``,
        ``sebagian``, ``sukses``.
        """
        if self.robots_disallowed:
            return "dilarang"
        if self.blocked:
            return "diblokir"
        if self.pages_scraped == 0:
            return "gagal"
        if self.error_message:
            return "sebagian"
        return "sukses"


# ---------------------------------------------------------------------------
# Utilitas teks
# ---------------------------------------------------------------------------
def _clean(text: str | None) -> str:
    """Merapikan spasi berlebih dan karakter tak terlihat."""
    if not text:
        return ""
    return WHITESPACE_RE.sub(" ", text).strip()


def slug_from_href(href: str) -> str:
    """Mengambil slug lowongan dari tautan detail.

    Contoh: ``/loker/golang-developer-10~pt-lawencon-internasional`` menjadi
    ``golang-developer-10~pt-lawencon-internasional``. Slug ini menjadi kunci
    unik karena memuat slug lowongan sekaligus slug perusahaan.
    """
    path = href.split("?", 1)[0].split("#", 1)[0].strip()
    marker = "/loker/"
    if marker in path:
        path = path.split(marker, 1)[1]
    return path.strip("/")


def _split_location(text: str) -> tuple[str, str]:
    """Memisahkan mode kerja dan kota, mis. "On-site • Jakarta Selatan"."""
    if config.LOCATION_SEPARATOR in text:
        mode, city = text.split(config.LOCATION_SEPARATOR, 1)
        return _clean(mode), _clean(city)
    return "", _clean(text)


def _is_job_type(text: str) -> bool:
    return text.strip().lower() in config.JOB_TYPE_LABELS


def _is_experience(text: str) -> bool:
    return text.strip().lower().startswith(config.EXPERIENCE_PREFIX)


def _is_salary(text: str) -> bool:
    lowered = text.strip().lower()
    return (
        lowered.startswith(config.SALARY_CURRENCY_PREFIX)
        or lowered.startswith(config.SALARY_NEGOTIABLE)
        or lowered.startswith("idr")
    )


# ---------------------------------------------------------------------------
# Parser halaman daftar
# ---------------------------------------------------------------------------
def parse_card(card, source: str = "") -> JobPosting | None:
    """Mengurai satu kartu lowongan. Mengembalikan None bila bukan lowongan."""
    href = card.get("href") or ""
    # Tautan navigasi (/loker/posisi/...) tidak punya pemisah "~".
    if config.CARD_SLUG_SEPARATOR not in href:
        return None

    slug = slug_from_href(href)
    if not slug:
        return None

    title_el = card.select_one(config.SELECTOR_TITLE)
    if title_el is None:
        return None
    title = _clean(title_el.get_text(" ", strip=True))
    if not title:
        return None

    company_el = card.select_one(config.SELECTOR_COMPANY)
    company = _clean(company_el.get_text(" ", strip=True)) if company_el else ""

    posting = JobPosting(
        slug=slug,
        title=title,
        company=company,
        url=f"{config.BASE_URL}/loker/{slug}",
        source=source,
    )

    # Baris meta diklasifikasikan berdasarkan isi, bukan urutan, karena
    # jumlah baris per kartu bisa 3 sampai 5.
    extra_rows: list[str] = []
    for row in card.select(config.SELECTOR_META_ROWS):
        text = _clean(row.get_text(" ", strip=True))
        if not text:
            continue
        if _is_job_type(text) and not posting.job_type:
            posting.job_type = text
        elif config.LOCATION_SEPARATOR in text and not posting.location:
            mode, city = _split_location(text)
            posting.work_mode = mode
            posting.location = city
        elif _is_experience(text) and not posting.experience_level:
            posting.experience_level = text
        elif _is_salary(text) and not posting.salary_text:
            posting.salary_text = text
        else:
            extra_rows.append(text)

    # Label tambahan seperti "Magang Berbayar" mengisi slot gaji bila belum ada.
    if extra_rows and not posting.salary_text:
        posting.salary_text = extra_rows[0]

    return posting


def parse_listing(html: str, source: str = "") -> list[JobPosting]:
    """Mengurai seluruh kartu lowongan dari satu halaman daftar.

    Hasil sudah terdeduplikasi berdasarkan slug dengan urutan kemunculan
    pertama dipertahankan.
    """
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    postings: list[JobPosting] = []
    seen: set[str] = set()
    for card in soup.select(config.SELECTOR_JOB_CARD):
        posting = parse_card(card, source=source)
        if posting is None or posting.slug in seen:
            continue
        seen.add(posting.slug)
        postings.append(posting)
    return postings


# ---------------------------------------------------------------------------
# Parser halaman detail
# ---------------------------------------------------------------------------
def _find_job_payload(data: dict) -> dict | None:
    """Mencari objek lowongan di dalam struktur __NEXT_DATA__."""
    try:
        queries = data["props"]["pageProps"]["dehydratedState"]["queries"]
    except (KeyError, TypeError):
        return None
    for query in queries:
        try:
            payload = query["state"]["data"]
        except (KeyError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("role"):
            return payload
    return None


def extract_next_data(html: str) -> dict | None:
    """Mengambil isi <script id="__NEXT_DATA__"> sebagai dict."""
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", id=config.NEXT_DATA_SCRIPT_ID)
    if script is None or not script.string:
        return None
    try:
        return json.loads(script.string)
    except json.JSONDecodeError:
        logger.debug("Gagal mengurai JSON __NEXT_DATA__.")
        return None


def parse_detail_skills(html: str) -> list[str]:
    """Mengekstrak daftar keahlian dari halaman detail lowongan.

    Keahlian diambil dari data JSON yang ditanam halaman, bukan dari DOM,
    karena struktur DOM dapat berubah tanpa memengaruhi data.
    """
    data = extract_next_data(html)
    if data is None:
        return []
    payload = _find_job_payload(data)
    if payload is None:
        return []

    preference = payload.get("candidatePreference") or {}
    skills = preference.get("skills") or []
    names: list[str] = []
    seen: set[str] = set()
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        name = _clean(skill.get("name"))
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------
class DeallsScraper:
    """Pengambil lowongan dengan batasan sopan terhadap server."""

    def __init__(
        self,
        sources: list[dict] | None = None,
        pages_per_source: int | None = None,
        detail_enabled: bool | None = None,
        detail_max: int | None = None,
        delay_min: float | None = None,
        delay_max: float | None = None,
        timeout: int | None = None,
    ) -> None:
        self.sources = sources if sources is not None else config.SOURCES
        self.pages_per_source = (
            pages_per_source if pages_per_source is not None
            else config.PAGES_PER_SOURCE
        )
        self.detail_enabled = (
            detail_enabled if detail_enabled is not None
            else config.DETAIL_SCRAPING_ENABLED
        )
        self.detail_max = detail_max if detail_max is not None else config.DETAIL_MAX_PER_RUN
        self.delay_min = delay_min if delay_min is not None else config.REQUEST_DELAY_MIN
        self.delay_max = delay_max if delay_max is not None else config.REQUEST_DELAY_MAX
        self.timeout = timeout if timeout is not None else config.REQUEST_TIMEOUT

        self.session = requests.Session()
        self.session.headers.update(config.HEADERS)
        self._robots: RobotFileParser | None = None
        self._last_request_at = 0.0

    # -- robots.txt --------------------------------------------------------
    def load_robots(self) -> RobotFileParser:
        """Membaca dan mengurai robots.txt."""
        last_error: Exception | None = None
        for attempt in range(1, config.MAX_RETRIES + 1):
            self._polite_sleep()
            try:
                response = self.session.get(config.ROBOTS_URL, timeout=self.timeout)
                self._last_request_at = time.monotonic()
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    "Gagal membaca robots.txt (percobaan %d/%d): %s",
                    attempt, config.MAX_RETRIES, exc,
                )
            else:
                if response.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(response.text.splitlines())
                    self._robots = parser
                    return parser
                if response.status_code in config.STOP_STATUS_CODES:
                    raise ScrapeBlockedError(
                        f"robots.txt menolak akses dengan status {response.status_code}.",
                        response.status_code,
                    )
                last_error = ScrapeError(
                    f"robots.txt mengembalikan status {response.status_code}."
                )
            self._backoff(attempt)

        self._robots = RobotFileParser()
        self._robots.parse([])
        logger.warning(
            "robots.txt tidak dapat dibaca (%s); menganggap semua jalur diizinkan.",
            last_error,
        )
        return self._robots

    def ensure_allowed(self, url: str) -> None:
        """Melempar RobotsDisallowedError bila jalur tidak diizinkan."""
        if self._robots is None:
            self.load_robots()
        assert self._robots is not None
        if not self._robots.can_fetch(config.USER_AGENT, url):
            raise RobotsDisallowedError(
                f"robots.txt dealls.com melarang pengambilan {url}."
            )

    # -- jeda dan retry ----------------------------------------------------
    def _polite_sleep(self) -> None:
        """Memberi jeda acak antar request agar tidak membebani server."""
        if self._last_request_at == 0.0:
            return
        elapsed = time.monotonic() - self._last_request_at
        target = random.uniform(self.delay_min, self.delay_max)
        remaining = target - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _backoff(self, attempt: int) -> None:
        """Menunggu dengan exponential backoff plus sedikit keacakan."""
        delay = config.BACKOFF_BASE ** attempt + random.uniform(0, 0.5)
        logger.debug("Menunggu %.1f detik sebelum mencoba lagi.", delay)
        time.sleep(delay)

    def fetch(self, url: str) -> str:
        """Mengambil HTML satu halaman dengan retry dan batas sopan."""
        self.ensure_allowed(url)

        last_error: Exception | None = None
        for attempt in range(1, config.MAX_RETRIES + 1):
            self._polite_sleep()
            try:
                response = self.session.get(url, timeout=self.timeout)
                self._last_request_at = time.monotonic()
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    "Request gagal (percobaan %d/%d) %s: %s",
                    attempt, config.MAX_RETRIES, url, exc,
                )
                self._backoff(attempt)
                continue

            if response.status_code in config.STOP_STATUS_CODES:
                raise ScrapeBlockedError(
                    f"Server mengembalikan status {response.status_code} untuk {url}.",
                    response.status_code,
                )
            if response.status_code == 404:
                raise ScrapeError(f"Halaman tidak ditemukan: {url}")
            if response.status_code >= 500:
                last_error = ScrapeError(f"Server error {response.status_code} untuk {url}")
                logger.warning(
                    "Server error (percobaan %d/%d) %s: %s",
                    attempt, config.MAX_RETRIES, url, response.status_code,
                )
                self._backoff(attempt)
                continue
            if response.status_code != 200:
                raise ScrapeError(
                    f"Status tak terduga {response.status_code} untuk {url}"
                )

            # dealls.com mengirim charset=utf-8; dikunci agar karakter
            # seperti "•" tidak rusak.
            response.encoding = "utf-8"
            return response.text

        raise ScrapeError(f"Gagal mengambil {url} setelah {config.MAX_RETRIES} percobaan: {last_error}")

    # -- alur utama --------------------------------------------------------
    def _page_url(self, base_url: str, page: int) -> str:
        return base_url if page == 1 else f"{base_url}?page={page}"

    def scrape_source(self, source: dict) -> SourceResult:
        """Mengambil seluruh halaman dari satu sumber.

        Kegagalan pada satu halaman tidak membuang halaman yang sudah
        berhasil dibaca; sumber hanya ditandai ``sebagian``.
        """
        result = SourceResult(source=source["name"])
        for page in range(1, self.pages_per_source + 1):
            url = self._page_url(source["url"], page)
            try:
                html = self.fetch(url)
            except (ScrapeBlockedError, RobotsDisallowedError):
                # Pemblokiran berlaku untuk seluruh situs: hentikan seketika.
                raise
            except ScrapeError as exc:
                logger.error(
                    "Halaman %d %s gagal: %s", page, source["name"], exc
                )
                if result.pages_scraped == 0:
                    # Belum ada data sama sekali dari sumber ini.
                    result.status = "gagal"
                else:
                    result.status = "sebagian"
                result.error_message = str(exc)
                return result

            postings = parse_listing(html, source=source["name"])
            result.pages_scraped += 1
            logger.info(
                "Halaman %d %s: %d lowongan.", page, source["name"], len(postings)
            )
            if not postings:
                # Halaman kosong menandakan pagination sudah habis.
                break
            result.postings.extend(postings)
        return result

    def enrich_with_skills(
        self,
        postings: list[JobPosting],
        tertunda: list[dict] | None = None,
    ) -> None:
        """Melengkapi skill dari halaman detail lowongan.

        Dua kelompok dikerjakan:

        1. ``postings`` — lowongan yang baru ditemukan pada run ini.
        2. ``tertunda`` — lowongan yang sudah tersimpan tetapi halaman
           detailnya belum pernah dibuka (mis. karena melewati batas
           ``DETAIL_MAX_PER_RUN`` pada run sebelumnya).

        Tanpa kelompok kedua, lowongan yang terlewat tidak akan pernah
        mendapat skill karena run berikutnya menganggapnya "sudah ada".
        """
        if not self.detail_enabled:
            return

        sisa = max(0, self.detail_max)
        targets: list[tuple[str, str]] = []

        for posting in postings:
            if sisa == 0:
                break
            targets.append((posting.slug, posting.url))
            sisa -= 1

        for item in tertunda or []:
            if sisa == 0:
                break
            targets.append((item["slug"], item["url"]))
            sisa -= 1

        total_diminta = len(postings) + len(tertunda or [])
        if total_diminta > len(targets):
            logger.info(
                "Batas DETAIL_MAX_PER_RUN=%d: %d dari %d lowongan diambil "
                "halaman detailnya, sisanya menyusul pada run berikutnya.",
                self.detail_max, len(targets), total_diminta,
            )

        for slug, url in targets:
            try:
                html = self.fetch(url)
            except (ScrapeBlockedError, RobotsDisallowedError):
                raise
            except ScrapeError as exc:
                logger.warning("Lewati detail %s: %s", slug, exc)
                continue

            skills = parse_detail_skills(html)
            # Ditandai sudah dicek apa pun hasilnya, agar lowongan yang
            # memang tanpa skill tidak diambil berulang kali.
            db_mod.mark_detail_checked(slug, skills)
            for posting in postings:
                if posting.slug == slug:
                    posting.skills = skills
                    posting.detail_dicek = True
                    break

    def run(
        self,
        known_slugs: set[str] | None = None,
        tertunda: list[dict] | None = None,
    ) -> ScrapeOutcome:
        """Menjalankan scraping untuk semua sumber.

        ``known_slugs`` dipakai agar halaman detail hanya diambil untuk
        lowongan yang belum pernah tersimpan.

        ``tertunda`` berisi lowongan lama yang halaman detailnya belum
        pernah dibuka; daftar ini diambil dari database oleh pemanggil.
        """
        known = known_slugs or set()
        outcome = ScrapeOutcome()

        try:
            self.load_robots()
        except ScrapeBlockedError as exc:
            outcome.blocked_status = exc.status_code
            outcome.error_message = str(exc)
            logger.error("Scraping dihentikan: %s", exc)
            return outcome
        except ScrapeError as exc:
            # robots.txt gagal dibaca tetapi situs tetap dapat diakses.
            logger.warning("robots.txt tidak terbaca: %s", exc)

        seen: set[str] = set()
        new_postings: list[JobPosting] = []

        for source in self.sources:
            try:
                source_result = self.scrape_source(source)
            except RobotsDisallowedError as exc:
                # robots.txt melarang jalur ini: hentikan dan laporkan,
                # jangan menembus.
                outcome.sources.append(
                    SourceResult(
                        source=source["name"],
                        status="dilarang",
                        error_message=str(exc),
                    )
                )
                outcome.robots_disallowed = True
                outcome.error_message = str(exc)
                logger.error("Scraping dihentikan oleh robots.txt: %s", exc)
                break
            except ScrapeBlockedError as exc:
                outcome.sources.append(
                    SourceResult(
                        source=source["name"],
                        status="diblokir",
                        error_message=str(exc),
                    )
                )
                outcome.blocked_status = exc.status_code
                outcome.error_message = str(exc)
                logger.error("Scraping dihentikan: %s", exc)
                break
            except ScrapeError as exc:
                outcome.sources.append(
                    SourceResult(
                        source=source["name"],
                        status="gagal",
                        error_message=str(exc),
                    )
                )
                outcome.error_message = outcome.error_message or str(exc)
                logger.error("Sumber %s gagal: %s", source["name"], exc)
                continue

            outcome.sources.append(source_result)

            # scrape_source menangani kegagalan halaman sendiri dan
            # mengembalikan status "gagal"/"sebagian". Pesannya tetap harus
            # diteruskan agar laporan ke admin tidak kosong.
            if source_result.status != "sukses":
                outcome.error_message = (
                    outcome.error_message or source_result.error_message
                )

            for posting in source_result.postings:
                if posting.slug in seen:
                    continue
                seen.add(posting.slug)
                outcome.postings.append(posting)
                if posting.slug not in known:
                    new_postings.append(posting)

        try:
            self.enrich_with_skills(new_postings, tertunda)
        except RobotsDisallowedError as exc:
            outcome.robots_disallowed = True
            outcome.error_message = str(exc)
            logger.error("Pengambilan halaman detail dihentikan oleh robots.txt: %s", exc)
        except ScrapeBlockedError as exc:
            outcome.blocked_status = exc.status_code
            outcome.error_message = str(exc)
            logger.error("Pengambilan halaman detail dihentikan: %s", exc)

        return outcome


# ---------------------------------------------------------------------------
# Mode uji: python scraper.py --dry-run
# ---------------------------------------------------------------------------
def _print_outcome(outcome: ScrapeOutcome) -> None:
    print("=" * 72)
    print("HASIL PARSING LOWONGAN DEALLS")
    print("=" * 72)
    for source in outcome.sources:
        print(f"\n[SUMBER] {source.source}  (status: {source.status})")
        print(f"  halaman diambil : {source.pages_scraped}")
        print(f"  lowongan terbaca: {source.found}")
        if source.error_message:
            print(f"  catatan         : {source.error_message}")
        for posting in source.postings:
            print(f"    - {posting.title}")
            print(f"      slug      : {posting.slug}")
            print(f"      perusahaan: {posting.company or '-'}")
            print(f"      tipe      : {posting.job_type or '-'}")
            print(f"      mode      : {posting.work_mode or '-'}")
            print(f"      lokasi    : {posting.location or '-'}")
            print(f"      pengalaman: {posting.experience_level or '-'}")
            print(f"      gaji      : {posting.salary_text or '-'}")
            print(f"      skill     : {', '.join(posting.skills) or '-'}")
            print(f"      url       : {posting.url}")

    total = len(outcome.postings)
    print("\n" + "=" * 72)
    print(f"TOTAL LOWONGAN UNIK: {total}")
    print(f"STATUS             : {outcome.status}")
    if outcome.error_message:
        print(f"CATATAN            : {outcome.error_message}")
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    """Titik masuk CLI untuk pengujian tanpa Telegram."""
    parser = argparse.ArgumentParser(
        description="Scraper lowongan dealls.com (tanpa Telegram).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Hanya mencetak hasil parse; tidak menulis ke database.",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=None,
        help="Jumlah halaman per sumber (default dari konfigurasi).",
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="Ambil halaman detail untuk mengekstrak skill (lebih lambat).",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=None,
        help="Batasi ke satu nama sumber; boleh diulang.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Cetak hasil dry-run sebagai JSON.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    sources = config.SOURCES
    if args.source:
        wanted = {name.lower() for name in args.source}
        sources = [
            source for source in config.SOURCES
            if source["name"].lower() in wanted or source["category"] in wanted
        ]
        if not sources:
            print(f"Sumber tidak dikenal: {args.source}")
            return 2

    scraper = DeallsScraper(
        sources=sources,
        pages_per_source=args.pages,
        detail_enabled=args.detail,
    )

    # Waktu mulai dicatat sebelum scraping agar durasi run tercatat benar.
    run_started_at = config.now_iso()

    # Halaman detail hanya diambil untuk lowongan yang belum pernah tersimpan.
    known: set[str] = set()
    tertunda: list[dict] = []
    if not args.dry_run:
        import db

        db.init_db()
        known = db.known_slugs()
        # Lowongan lama yang detailnya belum pernah dibuka ikut dilengkapi,
        # jadi tidak ada yang tertinggal tanpa skill.
        tertunda = db.jobs_needing_detail(config.DETAIL_MAX_PER_RUN)

    outcome = scraper.run(known_slugs=known, tertunda=tertunda)

    if args.json:
        payload = [posting.as_dict() for posting in outcome.postings]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload else 1

    _print_outcome(outcome)

    if not args.dry_run:
        import db

        saved = db.save_outcome(outcome, run_started_at)
        logger.info(
            "Tersimpan: %d run, %d lowongan baru.", saved["runs"], saved["new_jobs"]
        )
        print(
            f"\nDisimpan ke database: {saved['new_jobs']} lowongan baru "
            f"dari {saved['found_jobs']} lowongan terbaca."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
