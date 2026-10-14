import os
import sys
import socket

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import uvicorn

from config import HOST, PORT, MAX_STORAGE_MB, ITEM_TTL_SECONDS


def get_lan_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        primary = s.getsockname()[0]
        s.close()
        if primary and not primary.startswith("127."):
            ips.append(primary)
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


def main():
    lan_ips = get_lan_ips()
    
    print("\n" + "=" * 65)
    print(" 🚀  ShareNow - Real-Time File, Image & Text Sharing Platform")
    print("=" * 65)
    print(f" • Auto-Cleanup TTL     : {ITEM_TTL_SECONDS // 60} Minutes (1 Hour)")
    print(f" • Free-Tier Storage Cap: {MAX_STORAGE_MB} MB (Least-Priority Eviction)")
    print(f" • Real-Time Engine     : WebSockets + SQLite WAL")
    print(f" • Features             : Clipboard Paste, Camera Snap, QR Code, PWA")
    print("-" * 65)
    print(f" 💻 Local Access        : http://localhost:{PORT}")
    if lan_ips:
        for ip in lan_ips:
            print(f" 📱 Mobile / Wi-Fi Access: http://{ip}:{PORT}")
    print("=" * 65 + "\n")

    uvicorn.run(
        "main:app",
        host=HOST,
        port=PORT,
        reload=True
    )


if __name__ == "__main__":
    main()
