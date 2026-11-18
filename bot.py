"""Bot Telegram rekap lowongan Dealls.

Bagian ini menangani seluruh interaksi Telegram dan penjadwalan harian.
Seluruh pekerjaan berat (scraping, pembuatan grafik, ekspor) dijalankan
melalui ``asyncio.to_thread`` agar event loop bot tidak terblokir.

Jalankan dengan::

    python bot.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import time as dt_time
from logging.handlers import RotatingFileHandler

from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import (
    BadRequest,
    Conflict,
    Forbidden,
    InvalidToken,
    NetworkError,
    RetryAfter,
    TelegramError,
    TimedOut,
)
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ExtBot,
    MessageHandler,
    filters,
)

import analytics
import config
import db
import exporters
import pesan
import scraper

logger = logging.getLogger(__name__)

# Awalan callback_data untuk tombol "Berikutnya" pada /baru.
CALLBACK_PREFIX = "baru:"

# Kunci ``bot_data`` untuk mencegah dua proses scraping berjalan bersamaan.
KUNCI_SCRAPE = "scrape_berjalan"


# ---------------------------------------------------------------------------
# Bot dengan percobaan ulang otomatis
# ---------------------------------------------------------------------------
class BotTahanGangguan(ExtBot):
    """ExtBot yang mengulang request saat jaringan bermasalah.

    python-telegram-bot tidak mengulang request ketika terjadi
    ``TimedOut`` maupun ``NetworkError``. Akibatnya satu gangguan jaringan
    sesaat langsung menggagalkan perintah pengguna. Kelas ini menambahkan
    percobaan ulang dengan jeda yang bertambah untuk endpoint biasa.

    ``getUpdates`` sengaja tidak diulang di sini karena sudah ditangani
    sendiri oleh ``run_polling``; mengulangnya di lapisan ini hanya akan
    menggandakan percobaan.
    """

    ENDPOINT_DIKECUALIKAN = {"getUpdates"}

    # Di PTB, BadRequest adalah turunan NetworkError, sehingga retry pada
    # NetworkError saja akan ikut mengulang permintaan yang sebenarnya
    # ditolak karena kesalahan data (mis. pesan terlalu panjang). Kesalahan
    # seperti itu tidak akan berubah hasilnya walau diulang.
    TIDAK_DIULANG = (BadRequest, Forbidden, RetryAfter, Conflict, InvalidToken)

    async def _do_post(  # type: ignore[override]
        self,
        endpoint: str,
        data: dict,
        *,
        read_timeout=None,
        write_timeout=None,
        connect_timeout=None,
        pool_timeout=None,
    ):
        percobaan = 0
        while True:
            percobaan += 1
            try:
                return await super()._do_post(
                    endpoint=endpoint,
                    data=data,
                    read_timeout=read_timeout,
                    write_timeout=write_timeout,
                    connect_timeout=connect_timeout,
                    pool_timeout=pool_timeout,
                )
            except self.TIDAK_DIULANG:
                # Kesalahan data atau izin: mengulang tidak akan menolong.
                raise
            except (TimedOut, NetworkError) as exc:
                if endpoint in self.ENDPOINT_DIKECUALIKAN:
                    raise
                if percobaan >= max(1, config.TELEGRAM_MAX_RETRY):
                    logger.error(
                        "Menyerah setelah %d percobaan pada %s: %s",
                        percobaan, endpoint, exc,
                    )
                    raise
                jeda = config.TELEGRAM_RETRY_DELAY * percobaan
                logger.warning(
                    "Gangguan jaringan pada %s (%s). Percobaan %d/%d, "
                    "mengulang dalam %.1f detik.",
                    endpoint, type(exc).__name__, percobaan,
                    config.TELEGRAM_MAX_RETRY, jeda,
                )
                await asyncio.sleep(jeda)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def siapkan_logging() -> None:
    """Menyiapkan log ke berkas berputar dan ke konsol."""
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    format_log = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    formatter = logging.Formatter(format_log)

    berkas = RotatingFileHandler(
        config.LOG_FILE,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    berkas.setFormatter(formatter)

    konsol = logging.StreamHandler(sys.stdout)
    konsol.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Hindari duplikasi bila fungsi ini dipanggil lebih dari sekali.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(berkas)
    root.addHandler(konsol)

    # Library HTTP cukup menampilkan peringatan agar log tidak penuh.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Utilitas pesan
# ---------------------------------------------------------------------------
# Seluruh pembentukan teks berada di pesan.py. Nama-nama ini diimpor ulang
# agar handler di bawah tetap ringkas dibaca.
esc = pesan.esc
potong = pesan.potong
pecah_pesan = pesan.pecah_pesan
format_lowongan = pesan.format_lowongan
format_daftar_lowongan = pesan.format_daftar_lowongan


# ---------------------------------------------------------------------------
# Pencatatan aktivitas
# ---------------------------------------------------------------------------
def catat(update: Update, command: str, args: str = "") -> None:
    """Mencatat pemakaian perintah ke activity_log."""
    chat = update.effective_chat
    chat_id = chat.id if chat else None
    try:
        db.log_activity(chat_id, command, args)
    except Exception:  # noqa: BLE001 - log tidak boleh menggagalkan perintah
        logger.exception("Gagal mencatat aktivitas untuk %s", command)


def ambil_args(context: ContextTypes.DEFAULT_TYPE) -> str:
    return " ".join(context.args or []).strip()


# ---------------------------------------------------------------------------
# Penanganan kesalahan umum
# ---------------------------------------------------------------------------
async def tangani_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mencatat error tak terduga agar bot tidak berhenti.

    Gangguan jaringan diperlakukan berbeda: tidak perlu mengirim pesan
    (justru akan gagal lagi) dan tidak perlu menulis traceback penuh, karena
    itu bukan kesalahan kode.
    """
    error = context.error

    # BadRequest adalah turunan NetworkError di PTB, jadi harus diperiksa
    # lebih dulu agar kesalahan data tidak dilaporkan sebagai gangguan
    # jaringan.
    gangguan_jaringan = isinstance(error, (TimedOut, NetworkError)) and not isinstance(
        error, (BadRequest, Forbidden, RetryAfter, Conflict, InvalidToken)
    )

    if gangguan_jaringan:
        # Jaringan sedang bermasalah. Cukup catat satu baris agar log tidak
        # dipenuhi traceback yang sama berulang kali.
        logger.warning(
            "Gangguan jaringan saat memproses update: %s: %s",
            type(error).__name__, error,
        )
        return

    logger.exception("Error saat memproses update", exc_info=error)

    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Terjadi kesalahan saat memproses permintaan. Silakan coba lagi.",
            )
        except TelegramError:
            logger.warning("Tidak dapat mengirim pesan kesalahan ke pengguna.")


# ---------------------------------------------------------------------------
# Perintah dasar
# ---------------------------------------------------------------------------
async def perintah_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start: menyapa pengguna dan mendaftarkannya sebagai pelanggan."""
    catat(update, "start")
    chat_id = update.effective_chat.id
    db.ensure_user(chat_id)
    db.set_subscription(chat_id, True)

    teks = pesan.SAMBUTAN + pesan.BANTUAN
    await update.effective_message.reply_text(teks, parse_mode=ParseMode.HTML)


async def perintah_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help: menampilkan daftar perintah."""
    catat(update, "help")
    await update.effective_message.reply_text(
        pesan.BANTUAN, parse_mode=ParseMode.HTML
    )


# ---------------------------------------------------------------------------
# /baru dengan pagination
# ---------------------------------------------------------------------------
def _tombol_berikutnya(offset: int, total: int) -> InlineKeyboardMarkup | None:
    berikut = offset + config.JOBS_PER_PAGE
    if berikut >= total:
        return None
    tombol = InlineKeyboardButton(
        "Berikutnya ➡️", callback_data=f"{CALLBACK_PREFIX}{berikut}"
    )
    return InlineKeyboardMarkup([[tombol]])


async def perintah_baru(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/baru: lowongan yang pertama kali terlihat hari ini."""
    catat(update, "baru")

    total = db.count_new_jobs_today()
    if total == 0:
        await update.effective_message.reply_text(
            "Belum ada lowongan baru hari ini.\n"
            "Scraping berjalan otomatis setiap pagi; Anda juga bisa menunggu "
            "notifikasi berikutnya."
        )
        return

    rows = db.get_new_jobs_today(config.JOBS_PER_PAGE, 0)
    teks = format_daftar_lowongan(rows, total, 0, config.JOBS_PER_PAGE)
    keyboard = _tombol_berikutnya(0, total)

    bagian = pecah_pesan(teks)
    # Tombol hanya ditempel pada bagian terakhir agar tidak tampil berulang.
    for indeks, isi in enumerate(bagian):
        await update.effective_message.reply_text(
            isi,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=keyboard if indeks == len(bagian) - 1 else None,
        )


async def tangani_tombol_berikutnya(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Menangani tombol "Berikutnya" pada daftar /baru."""
    query = update.callback_query
    await query.answer()

    try:
        offset = int(query.data.replace(CALLBACK_PREFIX, "", 1))
    except ValueError:
        offset = 0

    total = db.count_new_jobs_today()
    rows = db.get_new_jobs_today(config.JOBS_PER_PAGE, offset)

    if not rows:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Tidak ada lowongan lagi.")
        return

    teks = format_daftar_lowongan(rows, total, offset, config.JOBS_PER_PAGE)
    keyboard = _tombol_berikutnya(offset, total)
    bagian = pecah_pesan(teks)

    # Tombol dilepas dari pesan lama agar tidak bisa ditekan dua kali.
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        logger.debug("Tidak dapat menghapus tombol pada pesan lama.")

    for indeks, isi in enumerate(bagian):
        await query.message.reply_text(
            isi,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=keyboard if indeks == len(bagian) - 1 else None,
        )


# ---------------------------------------------------------------------------
# /cari
# ---------------------------------------------------------------------------
async def perintah_cari(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/cari <kata>: mencari lowongan di judul, perusahaan, dan skill."""
    kata = ambil_args(context)
    catat(update, "cari", kata)

    if not kata:
        await update.effective_message.reply_text(
            "Gunakan format: <code>/cari python</code>", parse_mode=ParseMode.HTML
        )
        return

    rows = db.search_jobs(kata, limit=20)
    if not rows:
        await update.effective_message.reply_text(
            f"Tidak ada lowongan yang cocok dengan <b>{esc(kata)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    for bagian in pecah_pesan(pesan.format_hasil_cari(kata, rows)):
        await update.effective_message.reply_text(
            bagian, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )


# ---------------------------------------------------------------------------
# /berhenti dan /langganan
# ---------------------------------------------------------------------------
async def perintah_berhenti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/berhenti: mematikan notifikasi harian."""
    catat(update, "berhenti")
    chat_id = update.effective_chat.id
    db.ensure_user(chat_id)
    db.set_subscription(chat_id, False)
    await update.effective_message.reply_text(
        "Notifikasi harian dimatikan.\n"
        "Anda bisa menyalakannya lagi kapan saja dengan /langganan."
    )


async def perintah_langganan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/langganan: menyalakan notifikasi harian."""
    catat(update, "langganan")
    chat_id = update.effective_chat.id
    db.ensure_user(chat_id)
    db.set_subscription(chat_id, True)
    await update.effective_message.reply_text(
        "Notifikasi harian diaktifkan.\n"
        "Anda akan menerima ringkasan lowongan baru setiap pagi."
    )


# ---------------------------------------------------------------------------
# /skill
# ---------------------------------------------------------------------------
async def perintah_skill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/skill: skill paling sering diminta."""
    catat(update, "skill")
    teks = await asyncio.to_thread(analytics.skill_teks, 10)
    await update.effective_message.reply_text(teks, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /grafik
# ---------------------------------------------------------------------------
async def perintah_grafik(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/grafik <jenis>: mengirim grafik sebagai gambar."""
    jenis = ambil_args(context).lower()
    catat(update, "grafik", jenis)

    if jenis not in analytics.JENIS_GRAFIK:
        pilihan = "\n".join(
            f"• <code>{nama}</code> — {esc(judul)}"
            for nama, judul in analytics.JENIS_GRAFIK.items()
        )
        await update.effective_message.reply_text(
            "Pilih jenis grafik:\n\n" + pilihan + "\n\nContoh: <code>/grafik harian</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.effective_message.reply_text("Membuat grafik...")

    hasil = await asyncio.to_thread(analytics.buat_grafik, jenis)
    if hasil is None:
        await update.effective_message.reply_text(
            f"Belum ada data untuk grafik <b>{esc(jenis)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    buffer, judul = hasil
    await update.effective_message.reply_photo(
        photo=InputFile(buffer, filename=f"grafik-{jenis}.png"),
        caption=f"<b>{esc(judul)}</b>",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /export
# ---------------------------------------------------------------------------
async def perintah_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/export csv|xlsx|docx: mengirim berkas data."""
    jenis = ambil_args(context).lower()
    catat(update, "export", jenis)

    if jenis not in exporters.EKSPORTER:
        await update.effective_message.reply_text(
            "Pilih format ekspor:\n"
            "• <code>/export csv</code>\n"
            "• <code>/export xlsx</code>\n"
            "• <code>/export docx</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if db.count_jobs() == 0:
        await update.effective_message.reply_text(
            "Belum ada data untuk diekspor."
        )
        return

    await update.effective_message.reply_text("Menyiapkan berkas...")

    hasil = await asyncio.to_thread(exporters.buat_ekspor, jenis)
    if hasil is None:
        await update.effective_message.reply_text("Format tidak dikenal.")
        return

    buffer, nama = hasil
    await update.effective_message.reply_document(
        document=InputFile(buffer, filename=nama),
        caption=f"Ekspor {jenis.upper()} — {config.now_wib():%d-%m-%Y %H:%M} WIB",
    )


# ---------------------------------------------------------------------------
# Scraping manual (admin)
# ---------------------------------------------------------------------------
def _boleh_admin(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and config.ADMIN_CHAT_ID and chat.id == config.ADMIN_CHAT_ID)


def jalankan_scraping_sinkron() -> dict:
    """Menjalankan scraping lalu menyimpannya. Dijalankan di thread terpisah."""
    db.init_db()
    mulai = config.now_iso()
    pemburu = scraper.DeallsScraper()
    hasil = pemburu.run(
        known_slugs=db.known_slugs(),
        # Lowongan lama yang detailnya belum pernah dibuka ikut dilengkapi.
        tertunda=db.jobs_needing_detail(config.DETAIL_MAX_PER_RUN),
    )
    tersimpan = db.save_outcome(hasil, mulai)
    return {
        "status": hasil.status,
        "error": hasil.error_message,
        "diblokir": hasil.blocked,
        "robots": hasil.robots_disallowed,
        **tersimpan,
    }


async def perintah_scrape(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/scrape: menjalankan scraping manual (khusus admin)."""
    catat(update, "scrape")

    if not _boleh_admin(update):
        await update.effective_message.reply_text(
            "Perintah ini hanya untuk admin."
        )
        return

    if context.application.bot_data.get(KUNCI_SCRAPE):
        await update.effective_message.reply_text(
            "Scraping sedang berjalan. Mohon tunggu hingga selesai."
        )
        return

    context.application.bot_data[KUNCI_SCRAPE] = True
    await update.effective_message.reply_text("Scraping dimulai. Mohon tunggu...")
    try:
        hasil = await asyncio.to_thread(jalankan_scraping_sinkron)
    except Exception as exc:  # noqa: BLE001 - jangan sampai bot crash
        logger.exception("Scraping manual gagal")
        await update.effective_message.reply_text(
            f"Scraping gagal: {esc(exc)}", parse_mode=ParseMode.HTML
        )
        return
    finally:
        context.application.bot_data[KUNCI_SCRAPE] = False

    teks = (
        "<b>Scraping selesai</b>\n"
        f"Status        : {esc(hasil['status'])}\n"
        f"Lowongan baru : {hasil['new_jobs']}\n"
        f"Total terbaca : {hasil['found_jobs']}\n"
        f"Sumber dicatat: {hasil['runs']}"
    )
    if hasil.get("error"):
        teks += f"\nCatatan       : {esc(potong(str(hasil['error']), 300))}"
    await update.effective_message.reply_text(teks, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Notifikasi harian
# ---------------------------------------------------------------------------
def susun_notifikasi() -> list[str]:
    """Menyusun pesan notifikasi harian.

    Sejak filter kata kunci dihapus, isi notifikasi sama untuk semua
    pelanggan. Jadi pesan disusun sekali saja di luar loop pengiriman
    (lihat ``notifikasi_harian``), bukan diulang per pelanggan.
    """
    total_baru = db.count_new_jobs_today()
    cocok = (
        db.get_new_jobs_today(config.JOBS_PER_PAGE, 0) if total_baru else []
    )
    return pesan.format_notifikasi(total_baru, cocok)


async def notifikasi_harian(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job harian: scraping lalu mengirim ringkasan ke semua pelanggan."""
    if context.application.bot_data.get(KUNCI_SCRAPE):
        logger.warning("Scraping sebelumnya masih berjalan; jadwal dilewati.")
        return

    context.application.bot_data[KUNCI_SCRAPE] = True
    logger.info("Menjalankan scraping harian.")
    hasil: dict | None = None
    try:
        hasil = await asyncio.to_thread(jalankan_scraping_sinkron)
        logger.info(
            "Scraping harian selesai: status=%s, baru=%s",
            hasil["status"], hasil["new_jobs"],
        )
    except Exception:  # noqa: BLE001 - bot tidak boleh mati
        logger.exception("Scraping harian gagal total")
    finally:
        context.application.bot_data[KUNCI_SCRAPE] = False

    # Kabari admin bila ada masalah.
    if hasil is None or hasil["status"] in {"gagal", "diblokir", "dilarang"}:
        if config.ADMIN_CHAT_ID:
            pesan = "<b>Peringatan scraping harian</b>\n\n"
            if hasil is None:
                pesan += "Scraping gagal total. Periksa logs/bot.log."
            else:
                pesan += f"Status: {esc(hasil['status'])}\n"
                pesan += f"Catatan: {esc(potong(str(hasil.get('error') or '-'), 400))}"
            try:
                await context.bot.send_message(
                    chat_id=config.ADMIN_CHAT_ID,
                    text=pesan,
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                logger.exception("Gagal mengirim peringatan ke admin.")

    # Kirim ringkasan ke pelanggan.
    pelanggan = await asyncio.to_thread(db.get_subscribers)
    logger.info("Mengirim notifikasi ke %d pelanggan.", len(pelanggan))

    if not pelanggan:
        return

    # Isi notifikasi sama untuk semua pelanggan, jadi disusun sekali saja.
    try:
        bagian = await asyncio.to_thread(susun_notifikasi)
    except Exception:  # noqa: BLE001
        logger.exception("Gagal menyusun notifikasi harian.")
        return

    for chat_id in pelanggan:
        for isi_pesan in bagian:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=isi_pesan,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except TelegramError as exc:
                # Pengguna memblokir bot atau chat tidak valid: hentikan
                # langganannya agar tidak dicoba terus setiap hari.
                logger.warning("Gagal mengirim ke %s: %s", chat_id, exc)
                if "blocked" in str(exc).lower() or "chat not found" in str(exc).lower():
                    await asyncio.to_thread(db.set_subscription, chat_id, False)
                break
            await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Penyiapan aplikasi
# ---------------------------------------------------------------------------
def bangun_aplikasi() -> Application:
    """Membangun Application lengkap dengan handler dan jadwal harian."""
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN belum diisi. Salin .env.example menjadi .env "
            "lalu isi token dari @BotFather."
        )

    # Timeout bawaan PTB (5 detik) terlalu ketat untuk jaringan yang tidak
    # stabil, sehingga perintah gagal hanya karena gangguan sesaat.
    permintaan = HTTPXRequest(
        connect_timeout=config.TELEGRAM_CONNECT_TIMEOUT,
        read_timeout=config.TELEGRAM_READ_TIMEOUT,
        write_timeout=config.TELEGRAM_WRITE_TIMEOUT,
        pool_timeout=config.TELEGRAM_POOL_TIMEOUT,
    )

    # Bot dibuat eksplisit karena ApplicationBuilder tidak mengizinkan
    # parameter timeout builder digabung dengan bot kustom.
    bot_kustom = BotTahanGangguan(
        token=config.TELEGRAM_BOT_TOKEN,
        request=permintaan,
        get_updates_request=permintaan,
    )

    aplikasi = (
        ApplicationBuilder()
        .bot(bot_kustom)
        .post_init(pasang_jadwal)
        .build()
    )

    aplikasi.add_handler(CommandHandler("start", perintah_start))
    aplikasi.add_handler(CommandHandler("help", perintah_help))
    aplikasi.add_handler(CommandHandler("baru", perintah_baru))
    aplikasi.add_handler(CommandHandler("cari", perintah_cari))
    aplikasi.add_handler(CommandHandler("berhenti", perintah_berhenti))
    aplikasi.add_handler(CommandHandler("langganan", perintah_langganan))
    aplikasi.add_handler(CommandHandler("skill", perintah_skill))
    aplikasi.add_handler(CommandHandler("grafik", perintah_grafik))
    aplikasi.add_handler(CommandHandler("export", perintah_export))
    aplikasi.add_handler(CommandHandler("scrape", perintah_scrape))

    aplikasi.add_handler(CallbackQueryHandler(tangani_tombol_berikutnya, pattern=r"^baru:"))
    # Fallback perintah tak dikenal harus didaftarkan paling akhir.
    aplikasi.add_handler(MessageHandler(filters.COMMAND, perintah_tidak_dikenal))
    aplikasi.add_error_handler(tangani_error)
    return aplikasi


async def perintah_tidak_dikenal(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Menangani perintah yang tidak dikenal.

    Handler ini didaftarkan paling akhir sehingga hanya menangkap perintah
    yang tidak cocok dengan handler lain.
    """
    perintah = (update.effective_message.text or "").split()[0]
    catat(update, "tidak_dikenal", perintah)
    await update.effective_message.reply_text(
        f"Perintah <code>{esc(perintah)}</code> tidak dikenal.\n"
        "Ketik /help untuk melihat daftar perintah, atau pilih dari menu.",
        parse_mode=ParseMode.HTML,
    )


async def daftarkan_menu(aplikasi: Application) -> None:
    """Mendaftarkan menu perintah agar muncul sebagai tombol di Telegram.

    Setelah ini terpasang, pengguna melihat tombol menu di sisi kiri kolom
    pesan dan bisa menjalankan perintah tanpa mengetiknya.
    """
    daftar_pengguna = [
        BotCommand(perintah, deskripsi)
        for perintah, deskripsi in pesan.MENU_PERINTAH
    ]
    try:
        await aplikasi.bot.set_my_commands(
            daftar_pengguna, scope=BotCommandScopeAllPrivateChats()
        )
        # Menu default (tanpa scope) dipakai untuk grup dan chat lain.
        await aplikasi.bot.set_my_commands(daftar_pengguna)
    except TelegramError:
        logger.exception("Gagal mendaftarkan menu perintah.")
        return

    # Menu admin berisi tambahan /scrape, hanya untuk chat admin.
    if config.ADMIN_CHAT_ID:
        try:
            await aplikasi.bot.set_my_commands(
                [
                    BotCommand(perintah, deskripsi)
                    for perintah, deskripsi in pesan.menu_untuk_admin()
                ],
                scope=BotCommandScopeChat(chat_id=config.ADMIN_CHAT_ID),
            )
        except TelegramError:
            logger.warning(
                "Gagal mendaftarkan menu admin. Pastikan ADMIN_CHAT_ID benar "
                "dan Anda sudah pernah memulai chat dengan bot."
            )

    logger.info("Menu perintah terdaftar: %d perintah.", len(daftar_pengguna))


async def pasang_jadwal(aplikasi: Application) -> None:
    """Menjalankan persiapan setelah aplikasi siap.

    Nama fungsi dipertahankan agar ``post_init`` tetap jelas: di sini jadwal
    harian dipasang sekaligus menu perintah didaftarkan.
    """
    await daftarkan_menu(aplikasi)

    if not aplikasi.job_queue:
        logger.error(
            "JobQueue tidak tersedia. Pasang dengan: "
            "pip install 'python-telegram-bot[job-queue]'"
        )
        return

    # tzinfo wajib disertakan; tanpa itu JobQueue memakai UTC sehingga
    # 07:00 akan berjalan pukul 14:00 WIB.
    waktu = dt_time(
        hour=config.SCRAPE_HOUR,
        minute=config.SCRAPE_MINUTE,
        tzinfo=config.WIB,
    )
    aplikasi.job_queue.run_daily(
        notifikasi_harian,
        time=waktu,
        name="scraping-harian",
    )
    logger.info(
        "Jadwal scraping harian dipasang pukul %02d:%02d WIB.",
        config.SCRAPE_HOUR, config.SCRAPE_MINUTE,
    )


def main() -> None:
    """Titik masuk bot."""
    siapkan_logging()
    db.init_db()

    logger.info("Memulai bot rekap lowongan Dealls.")
    aplikasi = bangun_aplikasi()
    aplikasi.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
