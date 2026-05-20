# 🎟️ Distributed Async Ticket Booking System with Fault Tolerance

Simulasi sistem pemesanan tiket online yang mendemonstrasikan **concurrency**, **synchronization**, **asynchronous processing**, **multiprocessing**, dan **fault tolerance** menggunakan Python.

---

## 📁 Struktur Project

```
project/
│
├── booking_service/
│   ├── __init__.py          # Export fungsi publik
│   └── service.py           # ⭐ Logika booking, retry, idempotency
│
├── payment_service/
│   ├── __init__.py
│   └── service.py           # ⭐ Simulasi gateway payment + timeout
│
├── notification_service/
│   ├── __init__.py
│   └── service.py           # ⭐ Async queue + background worker
│
├── shared/
│   ├── __init__.py
│   ├── database.py          # ⭐ In-memory DB thread-safe (Lock!)
│   └── logger.py            # Konfigurasi logging terpusat
│
├── main.py                  # ⭐ FastAPI server + demo multiprocessing
├── requirements.txt
└── README.md
```

---

## 🏗️ Arsitektur Sistem

```
┌────────────────────────────────────────────────────────────┐
│                    CLIENT LAYER                             │
│   [User 1] [User 2] [User 3] ... [User N]                  │
│      └────────────┬────────────────┘                       │
│         multiprocessing.Pool (parallel requests)            │
└────────────────────┬───────────────────────────────────────┘
                     │ HTTP Request
                     ▼
┌────────────────────────────────────────────────────────────┐
│                  FastAPI SERVER                             │
│                                                            │
│   POST /book ──► Booking Service                           │
│   POST /pay  ──► Payment Service                           │
│   GET  /status/{id} ──► Booking Service                    │
│   GET  /seats ──► Database                                 │
└─────────┬──────────────────┬──────────────────────────────┘
          │                  │
          ▼                  ▼
┌─────────────────┐  ┌──────────────────┐
│ Booking Service │  │ Payment Service  │
│                 │  │                  │
│ ┌─────────────┐ │  │ ┌──────────────┐ │
│ │asyncio tasks│ │  │ │asyncio.wait_ │ │
│ │+ retry loop │ │  │ │for (timeout) │ │
│ └──────┬──────┘ │  │ └──────┬───────┘ │
└────────┼────────┘  └────────┼─────────┘
         │                    │
         ▼                    ▼
┌────────────────────────────────────────┐
│         Shared In-Memory Database      │
│                                        │
│  _seats_lock  ◄── threading.Lock()    │
│  _booking_lock ◄── threading.Lock()   │
│                                        │
│  seats{}    bookings{}    idem_keys{}  │
└───────────────────────┬────────────────┘
                        │
                        ▼
┌────────────────────────────────────────┐
│       Notification Service             │
│                                        │
│  asyncio.Queue ──► background worker  │
│  [email] [sms] [push notification]    │
└────────────────────────────────────────┘
```

---

## ⚙️ Teknologi & Konsep

| Konsep | Implementasi | File |
|---|---|---|
| **Thread Synchronization** | `threading.Lock()` | `shared/database.py` |
| **Async Processing** | `asyncio`, `await`, `asyncio.create_task` | semua service |
| **Timeout Handling** | `asyncio.wait_for(coro, timeout=5)` | `payment_service/service.py` |
| **Retry + Backoff** | Loop + `asyncio.sleep(delay*2)` | `booking_service/service.py` |
| **Message Queue** | `asyncio.Queue` | `notification_service/service.py` |
| **Multiprocessing** | `multiprocessing.Pool.map()` | `main.py` |
| **Idempotency** | Dictionary `idem_key → booking_id` | `shared/database.py` |
| **REST API** | FastAPI + Pydantic | `main.py` |

---

## 🛡️ Fault Tolerance: Penjelasan Detail

### 1. Race Condition & Lock
```
User A ──┐
User B ──┼──► reserve_seat("A1") ← CRITICAL SECTION
User C ──┘         │
                   ▼
          threading.Lock() memastikan hanya
          satu thread yang bisa masuk sekaligus.
          User lain akan WAIT sampai lock dilepas.

Hasil: Hanya 1 user yang berhasil, sisanya ditolak.
```

### 2. Retry + Exponential Backoff
```
Attempt 1 ──► GAGAL ──► tunggu 0.5s
Attempt 2 ──► GAGAL ──► tunggu 1.0s
Attempt 3 ──► GAGAL ──► GIVE UP → BookingStatus.FAILED
```

### 3. Timeout Handling
```
Payment Gateway
      │
      ▼
asyncio.wait_for(process(), timeout=5.0s)
      │
      ├── Selesai < 5s ──► SUCCESS ✅
      └── Melebihi 5s ──► TimeoutError → kursi dibebaskan
```

### 4. Idempotency
```
Request pertama:  POST /book {seat: A1, idem_key: "abc123"}
                  → Booking dibuat, ID = XYZ789

Request duplikat: POST /book {seat: A1, idem_key: "abc123"}
                  → Dikembalikan booking ID = XYZ789 (dari cache)
                  → TIDAK membuat booking baru ✅
```

---

## 🚀 Cara Menjalankan

### Persiapan
```bash
cd project/
pip install -r requirements.txt
```

### Mode 1: Server saja
```bash
python main.py
# Server berjalan di http://127.0.0.1:8000
# Dokumentasi interaktif: http://127.0.0.1:8000/docs
```

### Mode 2: Server + Demo Concurrent (12 user)
```bash
python main.py --demo
```

### Mode 3: Custom jumlah user
```bash
python main.py --demo 20
# 20 user booking secara bersamaan
```

---

## 📡 Contoh Request & Response API

### POST /book — Booking Kursi

**Request:**
```bash
curl -X POST http://localhost:8000/book \
  -H "Content-Type: application/json" \
  -H "X-Idempotency-Key: my-unique-key-001" \
  -d '{"user_id": "user_001", "seat_id": "A1"}'
```

**Response (Sukses 200):**
```json
{
  "success": true,
  "booking_id": "4F2A8B1C",
  "user_id": "user_001",
  "seat_id": "A1",
  "status": "pending",
  "message": "Kursi berhasil direservasi. Lakukan pembayaran dalam 30 detik.",
  "from_cache": false
}
```

**Response (Kursi sudah diambil 409):**
```json
{
  "success": false,
  "booking_id": "9D3E7F2A",
  "message": "Gagal setelah 3 percobaan: Kursi A1 sudah reserved",
  "status": "failed"
}
```

---

### POST /pay — Proses Pembayaran

**Request:**
```bash
curl -X POST http://localhost:8000/pay \
  -H "Content-Type: application/json" \
  -d '{"booking_id": "4F2A8B1C", "amount": 150000}'
```

**Response (Sukses 200):**
```json
{
  "success": true,
  "booking_id": "4F2A8B1C",
  "seat_id": "A1",
  "status": "success",
  "amount": 150000.0,
  "processing_time": 2.34,
  "message": "Pembayaran berhasil! Tiket Anda telah dikonfirmasi."
}
```

**Response (Timeout 402):**
```json
{
  "success": false,
  "booking_id": "4F2A8B1C",
  "status": "timeout",
  "message": "Pembayaran timeout setelah 5.0s. Kursi dibebaskan."
}
```

---

### GET /status/{booking_id} — Cek Status

```bash
curl http://localhost:8000/status/4F2A8B1C
```

```json
{
  "success": true,
  "booking_id": "4F2A8B1C",
  "user_id": "user_001",
  "seat_id": "A1",
  "status": "confirmed",
  "payment_status": "success"
}
```

---

### GET /seats — Daftar Semua Kursi

```bash
curl http://localhost:8000/seats
```

```json
{
  "seats": [
    {"seat_id": "A1", "status": "booked",     "booked_by": "4F2A8B1C"},
    {"seat_id": "A2", "status": "available",  "booked_by": null},
    {"seat_id": "B3", "status": "reserved",   "booked_by": "7C9D1E3F"}
  ],
  "summary": {
    "total": 15, "available": 10, "reserved": 2, "booked": 3
  }
}
```

---

## 🖥️ Contoh Output Terminal

```
01:20:10  INFO      [MainApp             ]  ============================================================
01:20:10  INFO      [MainApp             ]    Distributed Async Ticket Booking System
01:20:10  INFO      [MainApp             ]  ============================================================
01:20:10  INFO      [NotificationSvc     ]  Notification worker mulai berjalan...

[user_001] 🎯 Mencoba booking kursi A3...
[user_002] 🎯 Mencoba booking kursi B2...
[user_003] 🎯 Mencoba booking kursi A3...   ← rebutan!

01:20:11  INFO      [BookingService      ]  [4F2A8B1C] Kursi A3 berhasil direservasi (attempt 1)
01:20:11  WARNING   [BookingService      ]  [9D3E7F2A] Attempt 1 gagal: Kursi A3 sudah reserved
01:20:11  INFO      [BookingService      ]  [9D3E7F2A] Retry dalam 0.5s...

[user_001] 📋 Booking ID: 4F2A8B1C | Status: pending
[user_002] 📋 Booking ID: B7C23D1E | Status: pending

01:20:12  INFO      [PaymentService      ]  [4F2A8B1C] Memulai pembayaran Rp150,000
01:20:12  INFO      [PaymentService      ]  [4F2A8B1C] Estimasi waktu proses: 3.2s (timeout: 5.0s)
01:20:14  INFO      [PaymentService      ]  [4F2A8B1C] ✅ Pembayaran BERHASIL – kursi A3 dikonfirmasi

[user_001] ✅ Pembayaran SUKSES | Kursi: A3
[user_003] ❌ Booking gagal: Gagal setelah 3 percobaan

01:20:14  INFO      [NotificationSvc     ]  📧 Notifikasi terkirim ke [user_001] via EMAIL
01:20:14  INFO      [NotificationSvc     ]  📱 Notifikasi terkirim ke [user_003] via SMS

============================================================
  📊 RINGKASAN AKHIR
============================================================
  Total kursi    : 15
  ✅ Booked      : 8
  🟡 Reserved    : 1
  ⬜ Available   : 6
============================================================
```

---

## 💡 Konsep Kunci untuk Mahasiswa

### Kenapa Lock diperlukan?
Tanpa lock, dua thread bisa membaca status kursi "available" **secara bersamaan**, lalu keduanya mengklaim kursi tersebut → **double booking**. Lock memastikan hanya satu thread yang bisa memodifikasi data kursi dalam satu waktu.

### Kenapa asyncio bukan threading untuk I/O?
Pembayaran dan notifikasi adalah I/O-bound (menunggu network). `asyncio` memungkinkan ribuan operasi I/O berjalan "bersamaan" tanpa membuat thread baru untuk setiap operasi → lebih efisien.

### Kenapa multiprocessing untuk simulasi client?
Simulasi banyak user = CPU-bound (Python GIL membatasi threading untuk CPU-bound). `multiprocessing.Pool` membuat proses terpisah yang benar-benar paralel.

### Apa itu Idempotency?
Request yang dikirim dua kali menghasilkan efek yang sama seperti dikirim sekali. Penting untuk kasus retry otomatis di jaringan yang tidak stabil.
