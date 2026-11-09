"""Development entry point.

Run `python run.py`, then open http://127.0.0.1:8000 (or the LAN URL printed
below on a phone connected to the same Wi-Fi).
"""

from __future__ import annotations

import logging
import socket
import threading
import webbrowser

import uvicorn

from app.config import HOST, OPEN_BROWSER, PORT, PROVIDERS


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    configured = [p.label for p in PROVIDERS.values() if p.configured]
    print()
    print("  Corridor Fire-Safety Vision")
    print("  " + "-" * 46)
    print(f"  Local:  http://127.0.0.1:{PORT}")
    if HOST in {"0.0.0.0", "::"}:
        print(f"  Phone:  http://{lan_ip()}:{PORT}  (same Wi-Fi)")
    if configured:
        print(f"  Models: {', '.join(configured)}")
    else:
        print("  Models: none configured")
        print("  -> copy .env.example to .env and fill in an API key")
    print("  " + "-" * 46)
    print()

    if OPEN_BROWSER and HOST in {"0.0.0.0", "127.0.0.1", "::"}:
        threading.Timer(
            1.2, webbrowser.open, args=(f"http://127.0.0.1:{PORT}",)
        ).start()

    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
