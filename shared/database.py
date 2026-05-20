"""
shared/database.py
==================
Modul database in-memory yang digunakan bersama oleh semua service.
Menggunakan threading.Lock() untuk mencegah race condition saat banyak
user mencoba booking kursi secara bersamaan.
"""

import threading
import time
import uuid
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────
#  Enumerasi status
# ─────────────────────────────────────────────

class SeatStatus(str, Enum):
    AVAILABLE = "available"
    RESERVED  = "reserved"      # Sudah direservasi, menunggu pembayaran
    BOOKED    = "booked"        # Sudah dibayar / terkonfirmasi


class BookingStatus(str, Enum):
    PENDING   = "pending"
    CONFIRMED = "confirmed"
    FAILED    = "failed"
    CANCELLED = "cancelled"


class PaymentStatus(str, Enum):
    PENDING  = "pending"
    SUCCESS  = "success"
    TIMEOUT  = "timeout"
    FAILED   = "failed"


# ─────────────────────────────────────────────
#  Data models (sederhana, tanpa ORM)
# ─────────────────────────────────────────────

@dataclass
class Seat:
    seat_id: str
    row: str
    number: int
    status: SeatStatus = SeatStatus.AVAILABLE
    reserved_by: Optional[str] = None   # booking_id
    reserved_at: Optional[float] = None


@dataclass
class Booking:
    booking_id: str
    user_id: str
    seat_id: str
    status: BookingStatus = BookingStatus.PENDING
    payment_status: PaymentStatus = PaymentStatus.PENDING
    created_at: float = field(default_factory=time.time)
    idempotency_key: Optional[str] = None  # Untuk deteksi duplicate request


# ─────────────────────────────────────────────
#  Database in-memory (thread-safe)
# ─────────────────────────────────────────────

class InMemoryDatabase:
    """
    Database sederhana berbasis dictionary.

    Kunci utama:
      - _seats_lock  : Lock untuk operasi pada data kursi (mencegah double booking)
      - _booking_lock: Lock untuk operasi pada data booking
    """

    def __init__(self, total_rows: int = 3, seats_per_row: int = 5):
        # === Data storage ===
        self._seats: dict[str, Seat] = {}
        self._bookings: dict[str, Booking] = {}
        self._idempotency_keys: dict[str, str] = {}  # key -> booking_id

        # === Locks (synchronization primitives) ===
        self._seats_lock   = threading.Lock()
        self._booking_lock = threading.Lock()

        # === Inisialisasi kursi ===
        rows = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for r in range(total_rows):
            for n in range(1, seats_per_row + 1):
                seat_id = f"{rows[r]}{n}"
                self._seats[seat_id] = Seat(
                    seat_id=seat_id,
                    row=rows[r],
                    number=n,
                )

        self.RESERVATION_TIMEOUT = 30  # detik sebelum reservasi kadaluarsa

    # ─────────────────────────────────────────
    #  Operasi Kursi (dengan locking)
    # ─────────────────────────────────────────

    def get_available_seats(self) -> list[Seat]:
        """Kembalikan daftar kursi yang tersedia."""
        self._release_expired_reservations()
        with self._seats_lock:
            return [s for s in self._seats.values() if s.status == SeatStatus.AVAILABLE]

    def reserve_seat(self, seat_id: str, booking_id: str) -> tuple[bool, str]:
        """
        Coba reservasi kursi secara atomik.

        Ini adalah titik kritis (critical section) – hanya satu thread yang boleh
        mengeksekusi blok ini sekaligus, mencegah dua user mendapat kursi yang sama.

        Returns:
            (True, "")          jika berhasil
            (False, alasan)     jika gagal
        """
        self._release_expired_reservations()

        with self._seats_lock:          # <-- LOCK DIAMBIL
            seat = self._seats.get(seat_id)
            if seat is None:
                return False, f"Kursi {seat_id} tidak ditemukan"
            if seat.status != SeatStatus.AVAILABLE:
                return False, f"Kursi {seat_id} sudah {seat.status.value}"

            # Atomik: update status & catat siapa yang mereservasi
            seat.status      = SeatStatus.RESERVED
            seat.reserved_by = booking_id
            seat.reserved_at = time.time()
            return True, ""
        # <-- LOCK DILEPAS

    def confirm_seat(self, seat_id: str, booking_id: str) -> bool:
        """Konfirmasi kursi setelah pembayaran berhasil."""
        with self._seats_lock:
            seat = self._seats.get(seat_id)
            if seat and seat.reserved_by == booking_id:
                seat.status = SeatStatus.BOOKED
                return True
            return False

    def release_seat(self, seat_id: str) -> bool:
        """Bebaskan kursi (misal: pembayaran gagal / timeout)."""
        with self._seats_lock:
            seat = self._seats.get(seat_id)
            if seat and seat.status in (SeatStatus.RESERVED, SeatStatus.BOOKED):
                seat.status      = SeatStatus.AVAILABLE
                seat.reserved_by = None
                seat.reserved_at = None
                return True
            return False

    def _release_expired_reservations(self):
        """Bebaskan kursi yang reservasinya sudah kadaluarsa (tanpa pembayaran)."""
        now = time.time()
        with self._seats_lock:
            for seat in self._seats.values():
                if (
                    seat.status == SeatStatus.RESERVED
                    and seat.reserved_at is not None
                    and (now - seat.reserved_at) > self.RESERVATION_TIMEOUT
                ):
                    seat.status      = SeatStatus.AVAILABLE
                    seat.reserved_by = None
                    seat.reserved_at = None

    # ─────────────────────────────────────────
    #  Operasi Booking
    # ─────────────────────────────────────────

    def create_booking(
        self,
        user_id: str,
        seat_id: str,
        idempotency_key: Optional[str] = None,
    ) -> Booking:
        """Buat booking baru. Mendeteksi duplicate via idempotency_key."""
        with self._booking_lock:
            # Cek duplicate request
            if idempotency_key and idempotency_key in self._idempotency_keys:
                existing_id = self._idempotency_keys[idempotency_key]
                return self._bookings[existing_id]

            booking = Booking(
                booking_id=str(uuid.uuid4())[:8].upper(),
                user_id=user_id,
                seat_id=seat_id,
                idempotency_key=idempotency_key,
            )
            self._bookings[booking.booking_id] = booking

            if idempotency_key:
                self._idempotency_keys[idempotency_key] = booking.booking_id

            return booking

    def update_booking_status(
        self,
        booking_id: str,
        status: BookingStatus,
        payment_status: Optional[PaymentStatus] = None,
    ) -> Optional[Booking]:
        with self._booking_lock:
            booking = self._bookings.get(booking_id)
            if booking:
                booking.status = status
                if payment_status:
                    booking.payment_status = payment_status
            return booking

    def get_booking(self, booking_id: str) -> Optional[Booking]:
        return self._bookings.get(booking_id)

    def get_all_seats(self) -> list[Seat]:
        return list(self._seats.values())


# ─────────────────────────────────────────────
#  Instance global (shared antar service)
# ─────────────────────────────────────────────

db = InMemoryDatabase(total_rows=3, seats_per_row=5)
