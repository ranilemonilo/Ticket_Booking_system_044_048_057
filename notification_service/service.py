"""
notification_service/service.py
================================
Notification Service – mengirim notifikasi ke user via antrian async.

Fitur:
  - Menggunakan asyncio.Queue sebagai message queue (produsen–konsumen)
  - Notifikasi berjalan di background tanpa memblokir booking/payment
  - Simulasi berbagai saluran: email, SMS, push notification
"""

import asyncio
import random
from typing import Optional

from shared.logger import get_logger

logger = get_logger("NotificationSvc")

# ─────────────────────────────────────────────
#  Message Queue (asyncio.Queue)
# ─────────────────────────────────────────────
#
# Producer (booking/payment service) menaruh pesan ke queue.
# Consumer (worker di bawah) memproses pesan secara async.
#
notification_queue: asyncio.Queue = asyncio.Queue()

# Flag untuk menghentikan worker
_worker_running = False


# ─────────────────────────────────────────────
#  Producer: kirim notifikasi ke queue
# ─────────────────────────────────────────────

async def send_notification(
    user_id: str,
    booking_id: str,
    event: str,
    message: str,
    channel: str = "email",
) -> None:
    """
    Taruh notifikasi ke dalam queue.
    Fungsi ini langsung return; pengiriman terjadi di background worker.

    Args:
        user_id    : Penerima notifikasi
        booking_id : ID booking terkait
        event      : Jenis event (booking_created, payment_success, dll.)
        message    : Isi pesan
        channel    : Saluran pengiriman (email / sms / push)
    """
    notification = {
        "user_id"   : user_id,
        "booking_id": booking_id,
        "event"     : event,
        "message"   : message,
        "channel"   : channel,
    }
    await notification_queue.put(notification)
    logger.debug(f"[{booking_id}] Notifikasi [{event}] ditambahkan ke queue")


# ─────────────────────────────────────────────
#  Consumer: background worker
# ─────────────────────────────────────────────

async def notification_worker() -> None:
    """
    Worker yang berjalan selamanya di background.

    Cara kerja:
      - Menunggu item baru di queue (non-blocking dengan asyncio)
      - Memproses satu per satu
      - Simulasi pengiriman yang memerlukan waktu
    """
    global _worker_running
    _worker_running = True
    logger.info("Notification worker mulai berjalan...")

    while True:
        try:
            # Tunggu notifikasi baru (non-blocking untuk event loop lain)
            notification = await notification_queue.get()

            await _deliver_notification(notification)

            # Tandai item sudah selesai diproses
            notification_queue.task_done()

        except asyncio.CancelledError:
            logger.info("Notification worker dihentikan")
            break
        except Exception as e:
            logger.error(f"Error di notification worker: {e}")


async def _deliver_notification(notification: dict) -> None:
    """
    Simulasi pengiriman notifikasi ke berbagai channel.
    Pengiriman memerlukan waktu 0.1–0.5 detik (simulasi jaringan).
    """
    # Simulasi latency pengiriman
    delivery_time = random.uniform(0.1, 0.5)
    await asyncio.sleep(delivery_time)

    channel_icon = {"email": "📧", "sms": "📱", "push": "🔔"}.get(
        notification["channel"], "📨"
    )

    logger.info(
        f"{channel_icon}  Notifikasi terkirim ke [{notification['user_id']}] "
        f"via {notification['channel'].upper()} | "
        f"Event: {notification['event']} | "
        f"Booking: {notification['booking_id']}"
    )
    logger.debug(f"   └─ Pesan: {notification['message']}")


# ─────────────────────────────────────────────
#  Helper: kirim notifikasi berdasarkan event
# ─────────────────────────────────────────────

async def notify_booking_created(user_id: str, booking_id: str, seat_id: str) -> None:
    await send_notification(
        user_id, booking_id,
        event="booking_created",
        message=f"Kursi {seat_id} berhasil direservasi. Selesaikan pembayaran dalam 30 detik.",
        channel="push",
    )


async def notify_payment_success(user_id: str, booking_id: str, seat_id: str) -> None:
    await send_notification(
        user_id, booking_id,
        event="payment_success",
        message=f"🎉 Pembayaran berhasil! Tiket kursi {seat_id} Anda sudah terkonfirmasi.",
        channel="email",
    )


async def notify_payment_failed(user_id: str, booking_id: str, reason: str) -> None:
    await send_notification(
        user_id, booking_id,
        event="payment_failed",
        message=f"Pembayaran gagal: {reason}. Silakan coba lagi.",
        channel="sms",
    )
