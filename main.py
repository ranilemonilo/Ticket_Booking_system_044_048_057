"""
main.py
=======
Entry point aplikasi – FastAPI server + simulasi concurrent clients.

Menjalankan dua hal sekaligus:
  1. FastAPI HTTP server (untuk endpoint /book, /pay, /status, /seats)
  2. Simulasi load test: banyak user booking secara bersamaan menggunakan
     multiprocessing + asyncio

Cara menjalankan:
  python main.py           ← jalankan server saja
  python main.py --demo    ← jalankan demo simulasi concurrent booking
"""

import asyncio
import sys
import time
import uuid
import multiprocessing
import random
import requests

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from booking_service.service import create_booking, get_booking_status, get_seats_overview
from payment_service.service import process_payment
from notification_service.service import (
    notification_worker,
    notify_booking_created,
    notify_payment_success,
    notify_payment_failed,
)
from shared.logger import get_logger

logger = get_logger("MainApp")


# ─────────────────────────────────────────────
#  Lifespan (startup / shutdown)
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Jalankan background worker saat server start,
    hentikan saat server shutdown.
    """
    logger.info("=" * 60)
    logger.info("  Distributed Async Ticket Booking System")
    logger.info("=" * 60)

    # Mulai notification worker sebagai background task
    worker_task = asyncio.create_task(notification_worker())
    logger.info("✅ Notification worker aktif")

    yield  # Server berjalan

    # Cleanup saat shutdown
    worker_task.cancel()
    logger.info("Server berhenti.")


# ─────────────────────────────────────────────
#  FastAPI App
# ─────────────────────────────────────────────

app = FastAPI(
    title="Ticket Booking System",
    description="Distributed Async Ticket Booking dengan Fault Tolerance",
    version="1.0.0",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────
#  Request / Response models (Pydantic)
# ─────────────────────────────────────────────

class BookRequest(BaseModel):
    user_id: str
    seat_id: str

    class Config:
        json_schema_extra = {
            "example": {"user_id": "user_001", "seat_id": "A1"}
        }


class PayRequest(BaseModel):
    booking_id: str
    amount: Optional[float] = 150_000.0

    class Config:
        json_schema_extra = {
            "example": {"booking_id": "ABC12345", "amount": 150000}
        }


# ─────────────────────────────────────────────
#  Endpoints
# ─────────────────────────────────────────────

@app.get("/", summary="Health check")
async def root():
    return {"status": "running", "service": "Ticket Booking System"}


@app.post("/book", summary="Booking kursi")
async def book_seat(
    req: BookRequest,
    x_idempotency_key: Optional[str] = Header(default=None),
):
    """
    Endpoint booking kursi.

    Headers:
      - X-Idempotency-Key (opsional): Kirim key unik untuk mencegah double booking
        jika terjadi retry request.

    Contoh:
      POST /book
      {"user_id": "user_001", "seat_id": "A1"}
    """
    logger.info(f"POST /book  user={req.user_id}  seat={req.seat_id}  "
                f"idem_key={x_idempotency_key}")

    result = await create_booking(
        user_id=req.user_id,
        seat_id=req.seat_id,
        idempotency_key=x_idempotency_key,
    )

    # Kirim notifikasi jika booking berhasil (non-blocking)
    if result.get("success") and not result.get("from_cache"):
        asyncio.create_task(
            notify_booking_created(req.user_id, result["booking_id"], req.seat_id)
        )

    status_code = 200 if result.get("success") else 409
    return JSONResponse(content=_serialize(result), status_code=status_code)


@app.post("/pay", summary="Proses pembayaran")
async def pay_booking(req: PayRequest):
    """
    Endpoint pembayaran.
    Akan timeout jika pemrosesan melebihi 5 detik.

    Contoh:
      POST /pay
      {"booking_id": "ABC12345", "amount": 150000}
    """
    logger.info(f"POST /pay  booking_id={req.booking_id}  amount={req.amount}")

    result = await process_payment(req.booking_id, req.amount)

    # Kirim notifikasi berdasarkan hasil pembayaran
    if result.get("success"):
        booking = __import__("shared").db.get_booking(req.booking_id)
        if booking:
            asyncio.create_task(
                notify_payment_success(booking.user_id, req.booking_id, booking.seat_id)
            )
    else:
        booking = __import__("shared").db.get_booking(req.booking_id)
        if booking:
            asyncio.create_task(
                notify_payment_failed(booking.user_id, req.booking_id, result.get("message", ""))
            )

    status_code = 200 if result.get("success") else 402
    return JSONResponse(content=_serialize(result), status_code=status_code)


@app.get("/status/{booking_id}", summary="Cek status booking")
async def booking_status(booking_id: str):
    """
    Cek status booking berdasarkan booking_id.

    Contoh:
      GET /status/ABC12345
    """
    result = get_booking_status(booking_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result["message"])
    return _serialize(result)


@app.get("/seats", summary="Daftar semua kursi")
async def list_seats():
    """Tampilkan semua kursi beserta statusnya."""
    return _serialize(get_seats_overview())


# ─────────────────────────────────────────────
#  Helper
# ─────────────────────────────────────────────

def _serialize(obj):
    """Konversi Enum ke string agar bisa di-JSON-kan."""
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    if hasattr(obj, "value"):
        return obj.value
    return obj


# ─────────────────────────────────────────────
#  Demo: Simulasi Concurrent Booking
# ─────────────────────────────────────────────

SERVER_URL = "http://127.0.0.1:8000"


def _single_user_booking(user_num: int):
    """
    Satu user melakukan booking + pembayaran.
    Fungsi ini dijalankan di process terpisah (multiprocessing).
    """
    user_id  = f"user_{user_num:03d}"
    # Setiap user memilih kursi acak
    seat_row = random.choice(["A", "B", "C"])
    seat_num = random.randint(1, 5)
    seat_id  = f"{seat_row}{seat_num}"

    # Idempotency key = user + seat (mencegah double submit dari user yang sama)
    idem_key = f"{user_id}_{seat_id}_{uuid.uuid4().hex[:6]}"

    print(f"\n[{user_id}] 🎯 Mencoba booking kursi {seat_id}...")

    # ── Request 1: Booking ──────────────────────────────────────────────────
    try:
        resp = requests.post(
            f"{SERVER_URL}/book",
            json={"user_id": user_id, "seat_id": seat_id},
            headers={"X-Idempotency-Key": idem_key},
            timeout=10,
        )
        data = resp.json()
        if not data.get("success"):
            print(f"[{user_id}] ❌ Booking gagal: {data.get('message')}")
            return
    except Exception as e:
        print(f"[{user_id}] ❌ Error saat booking: {e}")
        return

    booking_id = data["booking_id"]
    print(f"[{user_id}] 📋 Booking ID: {booking_id} | Status: {data.get('status')}")

    # Sedikit delay sebelum bayar (simulasi user membaca konfirmasi)
    time.sleep(random.uniform(0.2, 1.0))

    # ── Request 2: Pembayaran ───────────────────────────────────────────────
    try:
        resp = requests.post(
            f"{SERVER_URL}/pay",
            json={"booking_id": booking_id, "amount": 150_000},
            timeout=15,
        )
        pay_data = resp.json()
        if pay_data.get("success"):
            print(f"[{user_id}] ✅ Pembayaran SUKSES | Kursi: {pay_data.get('seat_id')}")
        else:
            print(f"[{user_id}] ❌ Pembayaran GAGAL: {pay_data.get('message')}")
    except Exception as e:
        print(f"[{user_id}] ❌ Error saat bayar: {e}")


def run_concurrent_demo(num_users: int = 10):
    """
    Jalankan simulasi N user melakukan booking secara BERSAMAAN
    menggunakan multiprocessing.Pool.

    Ini mensimulasikan kondisi nyata di mana banyak orang
    mencoba memesan kursi yang sama dalam waktu yang sama.
    """
    print("\n" + "=" * 60)
    print(f"  🚀 DEMO: {num_users} user booking BERSAMAAN")
    print(f"  Kursi tersedia: 15 (3 baris × 5 kursi)")
    print("=" * 60)

    # Tunggu server siap
    print("\nMenunggu server siap...", end="")
    for _ in range(10):
        try:
            r = requests.get(f"{SERVER_URL}/", timeout=2)
            if r.status_code == 200:
                print(" ✅ Server siap!\n")
                break
        except Exception:
            time.sleep(1)
            print(".", end="", flush=True)

    # Jalankan semua user secara PARALEL
    with multiprocessing.Pool(processes=min(num_users, 8)) as pool:
        pool.map(_single_user_booking, range(1, num_users + 1))

    # Tampilkan ringkasan akhir
    print("\n" + "=" * 60)
    print("  📊 RINGKASAN AKHIR")
    print("=" * 60)
    try:
        resp = requests.get(f"{SERVER_URL}/seats", timeout=5)
        data = resp.json()
        summary = data.get("summary", {})
        print(f"  Total kursi    : {summary.get('total', 0)}")
        print(f"  ✅ Booked      : {summary.get('booked', 0)}")
        print(f"  🟡 Reserved    : {summary.get('reserved', 0)}")
        print(f"  ⬜ Available   : {summary.get('available', 0)}")
        print()
        print("  Detail kursi:")
        for seat in data.get("seats", []):
            icon = {"available": "⬜", "reserved": "🟡", "booked": "✅"}.get(
                seat["status"], "❓"
            )
            print(f"    {icon} {seat['seat_id']} – {seat['status']}")
    except Exception as e:
        print(f"  Error mengambil data: {e}")
    print("=" * 60)


# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    if "--demo" in sys.argv:
        # Mode demo: jalankan server + simulasi di background
        import threading

        print("Menjalankan server + demo simulasi concurrent...")

        # Jalankan server di thread terpisah
        server_thread = threading.Thread(
            target=lambda: uvicorn.run(
                app, host="127.0.0.1", port=8000, log_level="warning"
            ),
            daemon=True,
        )
        server_thread.start()

        time.sleep(2)  # Tunggu server siap

        # Jalankan demo (N user sekaligus)
        num_users = int(sys.argv[2]) if len(sys.argv) > 2 else 12
        run_concurrent_demo(num_users)

        print("\n💡 Server masih berjalan. Coba endpoint di http://127.0.0.1:8000/docs")
        print("   Tekan Ctrl+C untuk berhenti.\n")
        try:
            server_thread.join()
        except KeyboardInterrupt:
            print("\nServer dihentikan.")
    else:
        # Mode normal: jalankan server saja
        print("Menjalankan server di http://127.0.0.1:8000")
        print("Dokumentasi API: http://127.0.0.1:8000/docs")
        print("Tekan Ctrl+C untuk berhenti.\n")
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
