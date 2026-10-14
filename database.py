import sqlite3
import time
import hashlib
from typing import List, Dict, Optional, Any
from config import DB_PATH, ITEM_TTL_SECONDS


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db():
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    file_path TEXT,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    mime_type TEXT,
                    room TEXT NOT NULL DEFAULT 'global',
                    burn_after_reading INTEGER NOT NULL DEFAULT 0,
                    download_count INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_items_room_expires 
                ON items (room, expires_at);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_items_expires 
                ON items (expires_at);
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS rooms (
                    room_name TEXT PRIMARY KEY,
                    pin_hash TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
            """)
    finally:
        conn.close()


def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()


def set_room_pin(room: str, pin: str) -> bool:
    room = room.strip().lower()
    hashed = hash_pin(pin)
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO rooms (room_name, pin_hash, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(room_name) DO UPDATE SET pin_hash = excluded.pin_hash;
            """, (room, hashed, time.time()))
        return True
    finally:
        conn.close()


def verify_room_pin(room: str, pin: str) -> bool:
    room = room.strip().lower()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT pin_hash FROM rooms WHERE room_name = ?", (room,)
        ).fetchone()
        if not row:
            return True  # Not password protected
        return row["pin_hash"] == hash_pin(pin)
    finally:
        conn.close()


def is_room_protected(room: str) -> bool:
    room = room.strip().lower()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM rooms WHERE room_name = ?", (room,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def create_item(
    item_id: str,
    item_type: str,
    content: str,
    file_path: Optional[str] = None,
    file_size: int = 0,
    mime_type: Optional[str] = None,
    room: str = "global",
    burn_after_reading: bool = False,
    created_at: Optional[float] = None,
    expires_at: Optional[float] = None,
) -> Dict[str, Any]:
    now = created_at or time.time()
    exp = expires_at or (now + ITEM_TTL_SECONDS)
    room = room.strip().lower() or "global"

    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO items (
                    id, type, content, file_path, file_size, mime_type, 
                    room, burn_after_reading, download_count, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?);
            """, (
                item_id, item_type, content, file_path, file_size, mime_type,
                room, 1 if burn_after_reading else 0, now, exp
            ))
        return get_item(item_id)
    finally:
        conn.close()


def get_items(
    room: str = "global",
    limit: int = 150,
    item_type: Optional[str] = None,
    search: Optional[str] = None
) -> List[Dict[str, Any]]:
    room = room.strip().lower() or "global"
    now = time.time()
    query = """
        SELECT * FROM items 
        WHERE room = ? AND expires_at > ?
    """
    params: List[Any] = [room, now]

    if item_type and item_type in ("text", "image", "file"):
        query += " AND type = ?"
        params.append(item_type)

    if search:
        query += " AND (content LIKE ? OR mime_type LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term])

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    conn = get_connection()
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_item(item_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def increment_download(item_id: str) -> int:
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE items SET download_count = download_count + 1 WHERE id = ?",
                (item_id,)
            )
            row = conn.execute(
                "SELECT download_count FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            return row["download_count"] if row else 0
    finally:
        conn.close()


def delete_item(item_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        item = get_item(item_id)
        if item:
            with conn:
                conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        return item
    finally:
        conn.close()


def get_expired_items(current_time: Optional[float] = None) -> List[Dict[str, Any]]:
    now = current_time or time.time()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM items WHERE expires_at <= ?", (now,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_total_storage_used() -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(file_size), 0) as total FROM items WHERE file_path IS NOT NULL"
        ).fetchone()
        return int(row["total"]) if row else 0
    finally:
        conn.close()


def get_candidates_for_eviction() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT id, type, content, file_path, file_size, room, 
                   download_count, created_at, expires_at
            FROM items
            WHERE file_path IS NOT NULL AND file_size > 0
            ORDER BY created_at ASC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
