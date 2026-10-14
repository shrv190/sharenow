import os
import uuid
import time
import socket
import asyncio
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Set
from pathlib import Path

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File,
    Form, HTTPException, Query, status
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask
import aiofiles

from config import (
    UPLOAD_DIR, PORT, HOST, MAX_STORAGE_MB,
    CLEANUP_INTERVAL_SECONDS, ITEM_TTL_SECONDS
)
import database
import storage_manager


# -------------------------------------------------------------
# WebSocket Connection Hub
# -------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        # Maps room_name -> set of WebSockets
        self.active_rooms: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room: str):
        await websocket.accept()
        room = room.strip().lower() or "global"
        if room not in self.active_rooms:
            self.active_rooms[room] = set()
        self.active_rooms[room].add(websocket)
        await self.broadcast_room_count(room)

    def disconnect(self, websocket: WebSocket, room: str):
        room = room.strip().lower() or "global"
        if room in self.active_rooms:
            self.active_rooms[room].discard(websocket)
            if not self.active_rooms[room]:
                del self.active_rooms[room]

    async def broadcast_to_room(self, room: str, message: dict):
        room = room.strip().lower() or "global"
        if room in self.active_rooms:
            dead = set()
            for ws in self.active_rooms[room]:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.add(ws)
            for ws in dead:
                self.disconnect(ws, room)

    async def broadcast_all(self, message: dict):
        for room in list(self.active_rooms.keys()):
            await self.broadcast_to_room(room, message)

    async def broadcast_room_count(self, room: str):
        count = len(self.active_rooms.get(room, []))
        await self.broadcast_to_room(room, {
            "event": "client_count",
            "room": room,
            "count": count
        })


ws_manager = ConnectionManager()


# -------------------------------------------------------------
# Background Periodic Cleanup Worker (Every 60s)
# -------------------------------------------------------------
async def cleanup_worker():
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            purged = storage_manager.purge_expired_items()
            if purged:
                for item in purged:
                    await ws_manager.broadcast_to_room(item["room"], {
                        "event": "delete_item",
                        "id": item["id"],
                        "reason": "expired"
                    })
                # Broadcast updated storage stats
                stats = storage_manager.get_storage_stats()
                await ws_manager.broadcast_all({
                    "event": "storage_update",
                    "storage": stats
                })
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Worker Error] Cleanup task error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    worker_task = asyncio.create_task(cleanup_worker())
    print("[ShareNow] Server initialized and background cleanup worker started.")
    yield
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="ShareNow API", lifespan=lifespan)


# -------------------------------------------------------------
# Request Models
# -------------------------------------------------------------
class TextMessageRequest(BaseModel):
    content: str
    room: str = "global"
    burn_after_reading: bool = False
    room_pin: Optional[str] = None


class RoomPinRequest(BaseModel):
    room: str
    pin: str


class RoomVerifyRequest(BaseModel):
    room: str
    pin: Optional[str] = None


# -------------------------------------------------------------
# REST Endpoints
# -------------------------------------------------------------
@app.get("/api/storage")
def get_storage():
    return storage_manager.get_storage_stats()


@app.get("/api/network-info")
def get_network_info():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        primary_ip = s.getsockname()[0]
        s.close()
        if primary_ip and not primary_ip.startswith("127."):
            ips.append(primary_ip)
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    if not ips:
        ips.append("127.0.0.1")

    urls = [f"http://{ip}:{PORT}" for ip in ips]
    return {
        "host": HOST,
        "port": PORT,
        "ips": ips,
        "urls": urls,
        "primary_url": urls[0] if urls else f"http://localhost:{PORT}"
    }


@app.post("/api/rooms/pin")
def set_pin(req: RoomPinRequest):
    if not req.pin or len(req.pin) < 4:
        raise HTTPException(status_code=400, detail="PIN must be at least 4 characters")
    database.set_room_pin(req.room, req.pin)
    return {"status": "ok", "message": f"Room '{req.room}' is now protected with a PIN."}


@app.post("/api/rooms/verify")
def verify_pin(req: RoomVerifyRequest):
    room = req.room.strip().lower() or "global"
    protected = database.is_room_protected(room)
    if not protected:
        return {"valid": True, "is_protected": False}
    if not req.pin:
        return {"valid": False, "is_protected": True}
    valid = database.verify_room_pin(room, req.pin)
    return {"valid": valid, "is_protected": True}


@app.get("/api/items")
def list_items(
    room: str = Query("global"),
    type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    room_pin: Optional[str] = Query(None)
):
    room = room.strip().lower() or "global"
    if database.is_room_protected(room):
        if not room_pin or not database.verify_room_pin(room, room_pin):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="PIN required to view this room."
            )
    return {
        "items": database.get_items(room=room, item_type=type, search=search),
        "is_protected": database.is_room_protected(room),
        "storage": storage_manager.get_storage_stats()
    }


@app.post("/api/messages")
async def create_message(msg: TextMessageRequest):
    room = msg.room.strip().lower() or "global"
    if database.is_room_protected(room):
        if not msg.room_pin or not database.verify_room_pin(room, msg.room_pin):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing room PIN."
            )

    content = msg.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Message content cannot be empty.")

    item_id = str(uuid.uuid4())
    item = database.create_item(
        item_id=item_id,
        item_type="text",
        content=content,
        room=room,
        burn_after_reading=msg.burn_after_reading
    )

    await ws_manager.broadcast_to_room(room, {
        "event": "new_item",
        "item": item
    })
    return item


@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    room: str = Form("global"),
    burn_after_reading: bool = Form(False),
    room_pin: Optional[str] = Form(None)
):
    room = room.strip().lower() or "global"
    if database.is_room_protected(room):
        if not room_pin or not database.verify_room_pin(room, room_pin):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing room PIN."
            )

    orig_filename = file.filename or "unnamed_file"
    file_ext = Path(orig_filename).suffix.lower()

    # Read content or stream size check
    temp_content = await file.read()
    file_size = len(temp_content)

    if file_size == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Storage Quota & Least-Priority Eviction Analysis
    try:
        evicted = storage_manager.ensure_capacity_for_upload(file_size)
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_507_INSUFFICIENT_STORAGE, detail=str(ve))

    # Notify connected devices of evicted items if any were purged
    for ev in evicted:
        await ws_manager.broadcast_to_room(ev["room"], {
            "event": "delete_item",
            "id": ev["id"],
            "reason": "storage_eviction"
        })

    # Save to disk
    item_id = str(uuid.uuid4())
    safe_storage_name = f"{item_id}_{Path(orig_filename).name}"
    save_path = UPLOAD_DIR / safe_storage_name

    async with aiofiles.open(save_path, "wb") as f:
        await f.write(temp_content)

    # Classify type
    mime = file.content_type or "application/octet-stream"
    if mime.startswith("image/") or file_ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"]:
        item_type = "image"
    else:
        item_type = "file"

    item = database.create_item(
        item_id=item_id,
        item_type=item_type,
        content=orig_filename,
        file_path=safe_storage_name,
        file_size=file_size,
        mime_type=mime,
        room=room,
        burn_after_reading=burn_after_reading
    )

    # Broadcast new item to room
    await ws_manager.broadcast_to_room(room, {
        "event": "new_item",
        "item": item
    })

    # Broadcast storage stats
    stats = storage_manager.get_storage_stats()
    await ws_manager.broadcast_all({
        "event": "storage_update",
        "storage": stats
    })

    return item


@app.get("/uploads/{file_name}")
async def get_uploaded_file(
    file_name: str,
    download: bool = Query(False),
    room_pin: Optional[str] = Query(None)
):
    safe_name = Path(file_name).name
    file_path = UPLOAD_DIR / safe_name

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found or expired.")

    # Find item in DB to check room pin & burn_after_reading
    conn = database.get_connection()
    try:
        row = conn.execute("SELECT * FROM items WHERE file_path = ?", (safe_name,)).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="File metadata not found or expired.")

    item = dict(row)
    room = item["room"]

    if database.is_room_protected(room):
        if not room_pin or not database.verify_room_pin(room, room_pin):
            raise HTTPException(status_code=401, detail="PIN required to access file.")

    # Increment download count
    database.increment_download(item["id"])

    bg_task = None
    if item.get("burn_after_reading"):
        async def burn_cleanup(target_file: str, target_id: str, target_room: str):
            storage_manager.delete_physical_file(target_file)
            database.delete_item(target_id)
            await ws_manager.broadcast_to_room(target_room, {
                "event": "delete_item",
                "id": target_id,
                "reason": "burned"
            })
            await ws_manager.broadcast_all({
                "event": "storage_update",
                "storage": storage_manager.get_storage_stats()
            })

        bg_task = BackgroundTask(burn_cleanup, safe_name, item["id"], room)

    return FileResponse(
        path=file_path,
        media_type=item.get("mime_type") or "application/octet-stream",
        filename=item["content"] if download else None,
        content_disposition_type="attachment" if download else "inline",
        background=bg_task
    )



@app.delete("/api/items/{item_id}")
async def delete_single_item(item_id: str, room_pin: Optional[str] = Query(None)):
    item = database.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")

    room = item["room"]
    if database.is_room_protected(room):
        if not room_pin or not database.verify_room_pin(room, room_pin):
            raise HTTPException(status_code=401, detail="Invalid room PIN.")

    if item.get("file_path"):
        storage_manager.delete_physical_file(item["file_path"])

    database.delete_item(item_id)

    await ws_manager.broadcast_to_room(room, {
        "event": "delete_item",
        "id": item_id,
        "reason": "user_deleted"
    })

    stats = storage_manager.get_storage_stats()
    await ws_manager.broadcast_all({
        "event": "storage_update",
        "storage": stats
    })
    return {"status": "ok", "id": item_id}


# -------------------------------------------------------------
# WebSocket Endpoint
# -------------------------------------------------------------
@app.websocket("/ws/{room}")
async def websocket_endpoint(websocket: WebSocket, room: str):
    room = room.strip().lower() or "global"
    await ws_manager.connect(websocket, room)
    try:
        while True:
            data = await websocket.receive_json()
            # Echo or handle typing / ping
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, room)
        await ws_manager.broadcast_room_count(room)
    except Exception:
        ws_manager.disconnect(websocket, room)
        await ws_manager.broadcast_room_count(room)


# -------------------------------------------------------------
# Static Files (Frontend UI & PWA)
# -------------------------------------------------------------
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
