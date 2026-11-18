"""Uji ketahanan bot terhadap gangguan jaringan.

Tanpa jaringan sungguhan: ``_do_post`` internal diganti agar melempar
exception sesuai skenario.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from telegram.error import (
    BadRequest,
    Forbidden,
    NetworkError,
    RetryAfter,
    TimedOut,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
import config  # noqa: E402


@pytest.fixture()
def aplikasi(tmp_path, monkeypatch):
    """Application dengan token palsu (tidak ada request sungguhan)."""
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:PAKAI")
    monkeypatch.setattr(config, "TELEGRAM_RETRY_DELAY", 0.0)  # jangan menunggu
    return bot.bangun_aplikasi()


def jalankan_skenario(app, kegagalan: list, endpoint: str = "sendMessage"):
    """Jalankan _do_post dengan urutan kegagalan tertentu.

    Mengembalikan (hasil, jumlah_percobaan).
    """
    hitungan = {"n": 0}

    async def palsu(self, endpoint, data, **kw):
        i = hitungan["n"]
        hitungan["n"] += 1
        if i < len(kegagalan) and kegagalan[i] is not None:
            raise kegagalan[i]
        return {"ok": True}

    async def jalan():
        asli = bot.ExtBot._do_post
        bot.ExtBot._do_post = palsu
        try:
            return await app.bot._do_post(endpoint, {})
        finally:
            bot.ExtBot._do_post = asli

    try:
        hasil = asyncio.run(jalan())
        return hasil, hitungan["n"]
    except Exception as exc:  # noqa: BLE001
        return exc, hitungan["n"]


# ---------------------------------------------------------------------------
# Konfigurasi koneksi
# ---------------------------------------------------------------------------
def test_bot_memakai_kelas_kustom(aplikasi):
    assert isinstance(aplikasi.bot, bot.BotTahanGangguan)


def test_timeout_lebih_longgar_dari_default_ptb(aplikasi):
    # Default bawaan python-telegram-bot hanya 5 detik.
    assert aplikasi.bot.request.read_timeout == config.TELEGRAM_READ_TIMEOUT
    assert config.TELEGRAM_READ_TIMEOUT > 5.0
    assert config.TELEGRAM_CONNECT_TIMEOUT > 5.0


# ---------------------------------------------------------------------------
# Percobaan ulang
# ---------------------------------------------------------------------------
def test_timeout_sekali_lalu_sukses(aplikasi):
    hasil, percobaan = jalankan_skenario(aplikasi, [TimedOut("x"), None])
    assert hasil == {"ok": True}
    assert percobaan == 2


def test_timeout_dua_kali_lalu_sukses(aplikasi):
    hasil, percobaan = jalankan_skenario(
        aplikasi, [TimedOut("x"), TimedOut("x"), None]
    )
    assert hasil == {"ok": True}
    assert percobaan == 3


def test_network_error_diulang(aplikasi):
    hasil, percobaan = jalankan_skenario(aplikasi, [NetworkError("x"), None])
    assert hasil == {"ok": True}
    assert percobaan == 2


def test_berhenti_setelah_batas_percobaan(aplikasi):
    hasil, percobaan = jalankan_skenario(
        aplikasi, [TimedOut("x")] * 10 + [None]
    )
    assert isinstance(hasil, TimedOut)
    assert percobaan == config.TELEGRAM_MAX_RETRY


def test_getUpdates_tidak_diulang(aplikasi):
    # run_polling sudah punya penanganan sendiri; mengulang di sini akan
    # menggandakan percobaan.
    hasil, percobaan = jalankan_skenario(
        aplikasi, [TimedOut("x"), None], endpoint="getUpdates"
    )
    assert isinstance(hasil, TimedOut)
    assert percobaan == 1


# ---------------------------------------------------------------------------
# Kesalahan yang tidak boleh diulang
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kesalahan",
    [BadRequest("salah"), Forbidden("diblokir"), RetryAfter(3)],
)
def test_kesalahan_data_tidak_diulang(aplikasi, kesalahan):
    hasil, percobaan = jalankan_skenario(aplikasi, [kesalahan, None])
    assert isinstance(hasil, type(kesalahan))
    assert percobaan == 1


def test_badrequest_adalah_turunan_networkerror():
    # Alasan mengapa BadRequest harus diperiksa sebelum NetworkError:
    # tanpa urutan itu, BadRequest akan ikut diulang percuma.
    assert issubclass(BadRequest, NetworkError)


# ---------------------------------------------------------------------------
# Penanganan error
# ---------------------------------------------------------------------------
def test_error_jaringan_tidak_mengirim_pesan(monkeypatch):
    """Saat jaringan mati, bot tidak boleh mencoba kirim pesan lagi."""
    terkirim: list = []

    class FakeBot:
        async def send_message(self, *a, **kw):
            terkirim.append(1)

    class FakeUpdate:
        effective_chat = None

    class FakeContext:
        error = TimedOut("jaringan mati")
        bot = FakeBot()

    asyncio.run(bot.tangani_error(FakeUpdate(), FakeContext()))
    assert terkirim == []


def test_badrequest_bukan_gangguan_jaringan():
    """BadRequest harus masuk jalur error biasa, bukan jalur jaringan."""
    error = BadRequest("pesan terlalu panjang")
    gangguan = isinstance(error, (TimedOut, NetworkError)) and not isinstance(
        error, (BadRequest, Forbidden, RetryAfter, bot.Conflict, bot.InvalidToken)
    )
    assert gangguan is False
