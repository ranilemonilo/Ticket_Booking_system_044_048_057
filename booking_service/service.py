"""
booking_service/service.py
==========================
Booking Service – inti dari sistem.

Tanggung jawab:
  - Menerima permintaan booking dari user
  - Mereservasi kursi (dengan Lock untuk mencegah race condition)
  - Meneruskan ke Payment Service secara async
  - Mendukung idempotency (deteksi request duplikat)
  - Retry otomatis jika terjadi kegagalan sementara
"""

import asyncio
import time
from typing import Optional

from shared.database import db, BookingStatus, PaymentStatus
from shared.logger import get_logger

logger = get_logger("BookingService")


# ─────────────────────────────────────────────
#  Konfigurasi retry
# ─────────────────────────────────────────────

MAX_RETRIES     = 3
RETRY_DELAY_SEC = 0.5   # Delay awal (akan di-double setiap retry – exponential backoff)


async def _try_reserve_with_retry(seat_id: str, booking_id: str) -> tuple[bool, str]:
    """
    Coba reservasi kursi dengan mekanisme retry + exponential backoff.

    Kenapa perlu retry?
    - Mungkin ada transient error (misal lock sedang dipakai thread lain).
    - Exponential backoff: 0.5s → 1s → 2s sebelum menyerah.
    """
    delay = RETRY_DELAY_SEC
    for attempt in range(1, MAX_RETRIES + 1):
        success, reason = db.reserve_seat(seat_id, booking_id)
        if success:
            logger.info(f"[{booking_id}] Kursi {seat_id} berhasil direservasi (attempt {attempt})")
            return True, ""

        logger.warning(f"[{booking_id}] Attempt {attempt} gagal: {reason}")

        if attempt < MAX_RETRIES:
            logger.info(f"[{booking_id}] Retry dalam {delay:.1f}s...")
            await asyncio.sleep(delay)   # Non-blocking sleep (asyncio)
            delay *= 2                   # Exponential backoff

    return False, f"Gagal setelah {MAX_RETRIES} percobaan: {reason}"


# ─────────────────────────────────────────────
#  Fungsi utama booking
# ─────────────────────────────────────────────

async def create_booking(
    user_id: str,
    seat_id: str,
    idempotency_key: Optional[str] = None,
) -> dict:
    """
    Proses booking secara async.

    Alur:
      1. Deteksi duplicate request via idempotency_key
      2. Buat record booking di database
      3. Coba reservasi kursi (dengan retry)
      4. Jika berhasil → kembalikan booking_id untuk proses payment
      5. Jika gagal → tandai booking FAILED, bebaskan kursi

    Args:
        user_id         : ID pengguna
        seat_id         : Kursi yang ingin dipesan
        idempotency_key : Kunci unik per request (opsional, untuk mencegah double submit)

    Returns:
        dict berisi status dan detail booking
    """
    logger.info(f"User [{user_id}] mencoba booking kursi [{seat_id}]")

    # ── Langkah 1: Cek & buat booking record ──────────────────────────────
    booking = db.create_booking(user_id, seat_id, idempotency_key)

    # Deteksi idempotent (request duplikat)
    if booking.status == BookingStatus.CONFIRMED:
        logger.info(f"[{booking.booking_id}] Idempotent request – booking sudah CONFIRMED")
        return _booking_response(booking, from_cache=True)

    if booking.status == BookingStatus.PENDING and booking.created_at < time.time() - 1:
        logger.info(f"[{booking.booking_id}] Idempotent request – booking sedang diproses")
        return _booking_response(booking, from_cache=True)

    logger.info(f"[{booking.booking_id}] Booking record dibuat untuk user [{user_id}]")

    # ── Langkah 2: Reservasi kursi (critical section ada di db.reserve_seat) ──
    success, reason = await _try_reserve_with_retry(seat_id, booking.booking_id)

    if not success:
        # Booking gagal → update status
        db.update_booking_status(
            booking.booking_id,
            BookingStatus.FAILED,
            PaymentStatus.FAILED,
        )
        logger.error(f"[{booking.booking_id}] Booking GAGAL: {reason}")
        return {
            "success"    : False,
            "booking_id" : booking.booking_id,
            "message"    : reason,
            "status"     : BookingStatus.FAILED,
        }

    # ── Langkah 3: Booking PENDING – lanjut ke payment ─────────────────────
    logger.info(f"[{booking.booking_id}] Booking PENDING – menunggu pembayaran")
    return {
        "success"    : True,
        "booking_id" : booking.booking_id,
        "user_id"    : user_id,
        "seat_id"    : seat_id,
        "status"     : BookingStatus.PENDING,
        "message"    : "Kursi berhasil direservasi. Lakukan pembayaran dalam 30 detik.",
        "from_cache" : False,
    }


def get_booking_status(booking_id: str) -> dict:
    """Ambil status booking berdasarkan booking_id."""
    booking = db.get_booking(booking_id)
    if not booking:
        return {"success": False, "message": f"Booking {booking_id} tidak ditemukan"}

    return {
        "success"        : True,
        "booking_id"     : booking.booking_id,
        "user_id"        : booking.user_id,
        "seat_id"        : booking.seat_id,
        "status"         : booking.status,
        "payment_status" : booking.payment_status,
    }


def get_seats_overview() -> dict:
    """Ambil ringkasan status semua kursi."""
    seats = db.get_all_seats()
    return {
        "seats": [
            {
                "seat_id": s.seat_id,
                "status" : s.status,
                "booked_by": s.reserved_by,
            }
            for s in sorted(seats, key=lambda x: x.seat_id)
        ],
        "summary": {
            "total"    : len(seats),
            "available": sum(1 for s in seats if s.status.value == "available"),
            "reserved" : sum(1 for s in seats if s.status.value == "reserved"),
            "booked"   : sum(1 for s in seats if s.status.value == "booked"),
        },
    }


# ─────────────────────────────────────────────
#  Helper
# ─────────────────────────────────────────────

def _booking_response(booking, from_cache: bool = False) -> dict:
    return {
        "success"        : True,
        "booking_id"     : booking.booking_id,
        "user_id"        : booking.user_id,
        "seat_id"        : booking.seat_id,
        "status"         : booking.status,
        "payment_status" : booking.payment_status,
        "from_cache"     : from_cache,
        "message"        : "Idempotent: booking sudah ada" if from_cache else "OK",
    }
