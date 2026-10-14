import os
import sys
import time
import uuid
from pathlib import Path
from fastapi.testclient import TestClient

# Ensure workspace root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config

import database
import storage_manager
from main import app


client = TestClient(app)


def test_suite():
    print("\n--- [1] Testing Database Initialization ---")
    database.init_db()
    stats = storage_manager.get_storage_stats()
    assert "used_bytes" in stats
    assert stats["max_mb"] == config.MAX_STORAGE_MB
    print("✓ DB and Storage initialized successfully.")

    print("\n--- [2] Testing Text Message Creation & Retrieval ---")
    res = client.post("/api/messages", json={
        "content": "Hello ShareNow World! Here is a link: https://example.com",
        "room": "global"
    })
    assert res.status_code == 200, res.text
    msg_data = res.json()
    assert msg_data["type"] == "text"
    assert "Hello ShareNow" in msg_data["content"]
    assert msg_data["room"] == "global"
    text_id = msg_data["id"]
    print("✓ Text message created:", text_id)

    # Fetch items
    res = client.get("/api/items?room=global")
    assert res.status_code == 200
    items = res.json()["items"]
    assert any(i["id"] == text_id for i in items)
    print("✓ Text message retrieved in feed.")

    print("\n--- [3] Testing File & Image Upload ---")
    # Test uploading a mock text file
    file_content = b"This is a shared document file for test."
    res = client.post("/api/upload", data={"room": "global"}, files={"file": ("sample.txt", file_content, "text/plain")})
    assert res.status_code == 200, res.text
    file_item = res.json()
    assert file_item["type"] == "file"
    assert file_item["content"] == "sample.txt"
    file_id = file_item["id"]
    file_path_name = file_item["file_path"]
    print("✓ Document file uploaded:", file_id)

    # Test uploading a mock image
    img_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    res = client.post("/api/upload", data={"room": "global"}, files={"file": ("photo.png", img_content, "image/png")})
    assert res.status_code == 200, res.text
    img_item = res.json()
    assert img_item["type"] == "image"
    print("✓ Image file uploaded and auto-classified as 'image'.")

    # Verify download of uploaded file
    res = client.get(f"/uploads/{file_path_name}")
    assert res.status_code == 200
    assert res.content == file_content
    print("✓ Uploaded file downloaded and content verified.")

    print("\n--- [4] Testing Burn-After-Reading Feature ---")
    burn_content = b"Super secret one-time download file."
    res = client.post("/api/upload", data={"room": "global", "burn_after_reading": "true"}, files={"file": ("secret.txt", burn_content, "text/plain")})
    assert res.status_code == 200
    burn_item = res.json()
    assert burn_item["burn_after_reading"] == 1
    burn_filename = burn_item["file_path"]

    # First download: should succeed
    res1 = client.get(f"/uploads/{burn_filename}?download=true")
    assert res1.status_code == 200
    assert res1.content == burn_content
    print("✓ First download of burn-after-reading file succeeded.")

    # Second download: should 404 because it was self-destructed
    res2 = client.get(f"/uploads/{burn_filename}?download=true")
    assert res2.status_code == 404
    print("✓ Second download returned 404 - file successfully self-destructed!")

    print("\n--- [5] Testing 1-Hour Auto-Purge Mechanism ---")
    # Insert an artificially expired item
    past_time = time.time() - 4000  # > 1 hour ago
    expired_id = str(uuid.uuid4())
    fake_file = config.UPLOAD_DIR / f"{expired_id}_old.txt"
    fake_file.write_bytes(b"Old expired content")
    database.create_item(
        item_id=expired_id,
        item_type="file",
        content="old.txt",
        file_path=fake_file.name,
        file_size=len(b"Old expired content"),
        created_at=past_time,
        expires_at=past_time + 3600
    )
    # Run purge
    purged = storage_manager.purge_expired_items()
    assert any(p["id"] == expired_id for p in purged)
    assert not fake_file.exists()
    assert database.get_item(expired_id) is None
    print("✓ Expired item and its physical file successfully purged by cleanup engine.")

    print("\n--- [6] Testing Storage Capacity & Priority Eviction ---")
    # Temporarily set max storage to small limit to test eviction
    old_max = config.MAX_STORAGE_BYTES
    config.MAX_STORAGE_BYTES = 1000  # 1000 bytes limit
    try:
        # Create item A (500 bytes) created earlier
        cand_a_id = str(uuid.uuid4())
        file_a = config.UPLOAD_DIR / f"{cand_a_id}_a.bin"
        file_a.write_bytes(b"A" * 500)
        database.create_item(
            item_id=cand_a_id,
            item_type="file",
            content="a.bin",
            file_path=file_a.name,
            file_size=500,
            created_at=time.time() - 1000
        )

        # Create item B (400 bytes) created more recently with downloads
        cand_b_id = str(uuid.uuid4())
        file_b = config.UPLOAD_DIR / f"{cand_b_id}_b.bin"
        file_b.write_bytes(b"B" * 400)
        database.create_item(
            item_id=cand_b_id,
            item_type="file",
            content="b.bin",
            file_path=file_b.name,
            file_size=400,
            created_at=time.time() - 100
        )
        database.increment_download(cand_b_id)
        database.increment_download(cand_b_id)

        # Now attempting to upload 300 bytes will exceed 1000 bytes (500 + 400 + 300 = 1200)
        # It must evict lowest priority item (cand_a is older and has 0 downloads)
        evicted = storage_manager.ensure_capacity_for_upload(300)
        assert len(evicted) >= 1
        assert any(e["id"] == cand_a_id for e in evicted)
        assert not any(e["id"] == cand_b_id for e in evicted)
        assert not file_a.exists()
        assert file_b.exists()
        print("✓ Storage quota exceeded: lowest-priority item A evicted, preserving item B.")
    finally:
        config.MAX_STORAGE_BYTES = old_max


    print("\n--- [7] Testing Room PIN Security ---")
    res = client.post("/api/rooms/pin", json={"room": "private-room", "pin": "9988"})
    assert res.status_code == 200

    # Attempting to view private-room without PIN should be 401
    res_unauth = client.get("/api/items?room=private-room")
    assert res_unauth.status_code == 401
    print("✓ Unauthorized access blocked on PIN-protected room.")

    # Viewing with correct PIN should succeed
    res_auth = client.get("/api/items?room=private-room&room_pin=9988")
    assert res_auth.status_code == 200
    print("✓ Authorized access granted with correct PIN.")

    print("\n--- [8] Testing Network Info Endpoint ---")
    res = client.get("/api/network-info")
    assert res.status_code == 200
    net_data = res.json()
    assert "ips" in net_data
    assert "urls" in net_data
    print("✓ Network info returns local LAN addresses:", net_data["urls"])

    print("\n🎉 ALL AUTOMATED TESTS PASSED SUCCESSFULLY! 🎉\n")


if __name__ == "__main__":
    test_suite()
