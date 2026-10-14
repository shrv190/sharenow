import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# If running on Vercel or cloud serverless, use /tmp for writable storage
IS_VERCEL = os.getenv("VERCEL") == "1"
if IS_VERCEL:
    DATA_DIR = Path("/tmp")
else:
    DATA_DIR = BASE_DIR

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "sharenow.db"


# Storage quota in MB (default: 500 MB for free-tier compatibility)
MAX_STORAGE_MB = int(os.getenv("SHARENOW_MAX_STORAGE_MB", "500"))
MAX_STORAGE_BYTES = MAX_STORAGE_MB * 1024 * 1024

# Auto-cleanup time-to-live: 1 hour (3600 seconds)
ITEM_TTL_SECONDS = int(os.getenv("SHARENOW_TTL_SECONDS", "3600"))

# Sweep interval for expired items: 60 seconds
CLEANUP_INTERVAL_SECONDS = int(os.getenv("SHARENOW_CLEANUP_INTERVAL", "60"))

# Host & Port (Render sets PORT automatically)
HOST = os.getenv("HOST", os.getenv("SHARENOW_HOST", "0.0.0.0"))
PORT = int(os.getenv("PORT", os.getenv("SHARENOW_PORT", "8000")))

