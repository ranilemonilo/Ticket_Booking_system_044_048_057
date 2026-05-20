"""
shared/logger.py
================
Konfigurasi logging terpusat untuk seluruh service.
Setiap service menggunakan logger yang sama agar output terminal rapi.
"""

import logging
import sys
from datetime import datetime


def get_logger(name: str) -> logging.Logger:
    """
    Buat / ambil logger dengan nama tertentu.
    Format: [timestamp] [LEVEL] [nama_service] pesan
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.setLevel(logging.DEBUG)

        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)

        fmt = logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  [%(name)-20s]  %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)

    return logger
