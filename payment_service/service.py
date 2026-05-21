"""
payment_service/service.py
==========================
Payment Service – simulasi pemrosesan pembayaran.

Fitur:
  - Timeout handling: jika pembayaran melebihi batas waktu → TIMEOUT
  - Simulasi kegagalan acak (untuk demo fault tolerance)
  - Konfirmasi kursi setelah pembayaran sukses
  - Pembebasan kursi jika pembayaran gagal/timeout
"""

import asyncio
import random
import time

from shared.database import db, BookingStatus, PaymentStatus
from shared.logger import get_logger

logger = get_logger("PaymentService")

# ─────────────────────────────────────────────
#  Konfigurasi
# ─────────────────────────────────────────────

PAYMENT_TIMEOUT_SEC  = 5.0   # Batas waktu pemrosesan pembayaran
FAILURE_PROBABILITY  = 0.15  # 15% kemungkinan pembayaran gagal (simulasi)


async def process_payment(booking_id: str, amount: float = 150_000.0) -> dict:
    """
    Proses pembayaran secara async dengan simulasi:
      - Pemrosesan butuh waktu (0.5–4 detik)
      - 15% kemungkinan gagal (simulasi gateway error)
      - Timeout jika melebihi PAYMENT_TIMEOUT_SEC

    Args:
        booking_id : ID booking yang akan dibayar
        amount     : Nominal pembayaran (default Rp150.000)

    Returns:
        dict berisi status pembayaran
    """
    logger.info(f"[{booking_id}] Memulai pembayaran Rp{amount:,.0f}")

    # ── Validasi booking ────────────────────────────────────────────────────
    booking = db.get_booking(booking_id)
    if not booking:
        return {"success": False, "message": f"Booking {booking_id} tidak ditemukan"}

    if booking.status == BookingStatus.CONFIRMED:
        logger.info(f"[{booking_id}] Pembayaran sudah CONFIRMED sebelumnya (idempotent)")
        return {"success": True, "booking_id": booking_id, "status": PaymentStatus.SUCCESS,
                "message": "Pembayaran sudah dikonfirmasi sebelumnya"}

    if booking.status == BookingStatus.FAILED:
        return {"success": False, "message": "Booking sudah berstatus FAILED"}

    # ── Simulasi proses pembayaran dengan timeout ───────────────────────────
    processing_time = random.uniform(0.5, 4.5)  # Waktu proses acak
    logger.info(f"[{booking_id}] Estimasi waktu proses: {processing_time:.1f}s "
                f"(timeout: {PAYMENT_TIMEOUT_SEC}s)")

    try:
        # asyncio.wait_for akan melempar asyncio.TimeoutError jika melebihi batas
        result = await asyncio.wait_for(
            _simulate_payment_gateway(booking_id, processing_time),
            timeout=PAYMENT_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        # ── Timeout: bebaskan kursi dan tandai gagal ────────────────────────
        logger.error(f"[{booking_id}] ⏰ TIMEOUT! Pembayaran melebihi {PAYMENT_TIMEOUT_SEC}s")
        db.release_seat(booking.seat_id)
        db.update_booking_status(booking_id, BookingStatus.FAILED, PaymentStatus.TIMEOUT)
        return {
            "success"   : False,
            "booking_id": booking_id,
            "status"    : PaymentStatus.TIMEOUT,
            "message"   : f"Pembayaran timeout setelah {PAYMENT_TIMEOUT_SEC}s. Kursi dibebaskan.",
        }

    # ── Cek hasil pembayaran ────────────────────────────────────────────────
    if result["success"]:
        # Konfirmasi kursi & update booking
        db.confirm_seat(booking.seat_id, booking_id)
        db.update_booking_status(booking_id, BookingStatus.CONFIRMED, PaymentStatus.SUCCESS)
        logger.info(f"[{booking_id}] ✅ Pembayaran BERHASIL – kursi {booking.seat_id} dikonfirmasi")
        return {
            "success"        : True,
            "booking_id"     : booking_id,
            "seat_id"        : booking.seat_id,
            "status"         : PaymentStatus.SUCCESS,
            "amount"         : amount,
            "processing_time": result["elapsed"],
            "message"        : "Pembayaran berhasil! Tiket Anda telah dikonfirmasi.",
        }
    else:
        # Pembayaran gagal dari gateway
        db.release_seat(booking.seat_id)
        db.update_booking_status(booking_id, BookingStatus.FAILED, PaymentStatus.FAILED)
        logger.error(f"[{booking_id}] ❌ Pembayaran GAGAL: {result['reason']}")
        return {
            "success"   : False,
            "booking_id": booking_id,
            "status"    : PaymentStatus.FAILED,
            "message"   : result["reason"],
        }


async def _simulate_payment_gateway(booking_id: str, processing_time: float) -> dict:
    """
    Simulasi gateway pembayaran eksternal (misalnya Midtrans, Xendit).
    Memerlukan waktu acak dan bisa gagal secara acak.
    """
    start = time.time()
    await asyncio.sleep(processing_time)   # Simulasi network latency
    elapsed = time.time() - start

    # Simulasi kegagalan acak
    if random.random() < FAILURE_PROBABILITY:
        return {
            "success": False,
            "elapsed": elapsed,
            "reason" : "Payment gateway error: transaksi ditolak oleh bank",
        }

    return {"success": True, "elapsed": elapsed}
