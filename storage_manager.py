import os
import time
from typing import Dict, Any, List, Optional
import config
import database


def get_storage_stats() -> Dict[str, Any]:
    used_bytes = database.get_total_storage_used()
    max_bytes = config.MAX_STORAGE_BYTES
    max_mb = config.MAX_STORAGE_MB
    available_bytes = max(0, max_bytes - used_bytes)
    percentage = round((used_bytes / max_bytes) * 100, 1) if max_bytes > 0 else 0
    return {
        "used_bytes": used_bytes,
        "used_mb": round(used_bytes / (1024 * 1024), 2),
        "max_bytes": max_bytes,
        "max_mb": max_mb,
        "percentage": min(100.0, percentage),
        "available_bytes": available_bytes,
        "available_mb": round(available_bytes / (1024 * 1024), 2),
    }


def delete_physical_file(file_path: Optional[str]) -> bool:
    if not file_path:
        return False
    target = config.UPLOAD_DIR / file_path

    try:
        if target.exists() and target.is_file():
            target.unlink()
            return True
    except Exception as e:
        print(f"[Storage] Error deleting file {target}: {e}")
    return False


def purge_expired_items() -> List[Dict[str, Any]]:
    """Purges all items that have exceeded their 1-hour expiration."""
    now = time.time()
    expired_items = database.get_expired_items(now)
    purged: List[Dict[str, Any]] = []

    for item in expired_items:
        if item.get("file_path"):
            delete_physical_file(item["file_path"])
        database.delete_item(item["id"])
        purged.append({
            "id": item["id"],
            "room": item["room"],
            "type": item["type"],
            "content": item["content"],
            "reason": "expired"
        })

    if purged:
        print(f"[Cleanup] Purged {len(purged)} expired item(s) older than 1 hour.")
    return purged


def calculate_eviction_score(item: Dict[str, Any], now: float) -> float:
    """
    Higher score = lower retention value = evict first.
    Factors:
    - Age factor (0.0 to 1.0+): closer to expiration -> higher score
    - Download factor: fewer downloads -> higher score
    - Size factor: large files taking massive quota -> higher score
    """
    created_at = item.get("created_at", now)
    age = max(0.0, now - created_at)
    age_factor = min(1.0, age / config.ITEM_TTL_SECONDS)

    downloads = item.get("download_count", 0)
    download_factor = 1.0 / (1.0 + downloads)

    size = item.get("file_size", 0)
    size_factor = min(1.0, (size / config.MAX_STORAGE_BYTES) * 5) if config.MAX_STORAGE_BYTES > 0 else 0

    # 50% age, 30% download neglect, 20% size disproportion
    return (0.50 * age_factor) + (0.30 * download_factor) + (0.20 * size_factor)


def ensure_capacity_for_upload(incoming_size_bytes: int) -> List[Dict[str, Any]]:
    """
    Ensures storage has enough room for incoming_size_bytes.
    If storage would exceed quota, analyzes priority and evicts lowest-priority items.
    Returns list of evicted items for WebSocket notification.
    """
    max_bytes = config.MAX_STORAGE_BYTES
    max_mb = config.MAX_STORAGE_MB

    if incoming_size_bytes > max_bytes:
        raise ValueError(
            f"File size ({round(incoming_size_bytes / (1024*1024), 2)} MB) exceeds "
            f"maximum server quota ({max_mb} MB)."
        )

    current_used = database.get_total_storage_used()
    needed = (current_used + incoming_size_bytes) - max_bytes

    if needed <= 0:
        return []


    # Space needed: prioritize candidates
    candidates = database.get_candidates_for_eviction()
    now = time.time()

    # Sort descending by eviction score (highest score = least priority)
    scored = []
    for cand in candidates:
        score = calculate_eviction_score(cand, now)
        scored.append((score, cand))

    scored.sort(key=lambda x: x[0], reverse=True)

    evicted: List[Dict[str, Any]] = []
    freed_bytes = 0

    for score, cand in scored:
        if freed_bytes >= needed:
            break

        delete_physical_file(cand.get("file_path"))
        database.delete_item(cand["id"])
        freed_bytes += cand.get("file_size", 0)
        evicted.append({
            "id": cand["id"],
            "room": cand["room"],
            "type": cand["type"],
            "content": cand["content"],
            "reason": "storage_eviction",
            "eviction_score": round(score, 3)
        })

    if freed_bytes < needed:
        raise ValueError("Storage quota full and cannot free sufficient space for upload.")

    print(f"[Storage] Evicted {len(evicted)} item(s) to free {round(freed_bytes / (1024*1024), 2)} MB.")
    return evicted
