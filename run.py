"""Start the local check-in form server; open the browser (hotel PC / exe)."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser

from server import make_server


def _env_host() -> str:
    return str(os.getenv("HOST", "127.0.0.1")).strip() or "127.0.0.1"


def _env_port() -> int:
    return int(str(os.getenv("PORT", "8787")).strip() or "8787")


def _port_open(host: str, port: int) -> bool:
    probe = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    try:
        with socket.create_connection((probe, port), timeout=0.4):
            return True
    except OSError:
        return False


def _open_browser(url: str, host: str, port: int) -> None:
    for _ in range(40):
        if _port_open(host, port):
            webbrowser.open(url)
            return
        time.sleep(0.15)
    webbrowser.open(url)


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    host = _env_host()
    port = _env_port()
    # Cloud (Railway): do not open a browser; bind all interfaces.
    on_railway = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_STATIC_URL"))
    if on_railway or os.getenv("CHECKIN_BIND_ALL", "").strip() in {"1", "true", "yes"}:
        host = "0.0.0.0"
    open_browser = ("--no-browser" not in args) and not on_railway and host in {
        "127.0.0.1",
        "localhost",
    }
    public = str(os.getenv("CHECKIN_PUBLIC_BASE_URL", "")).strip().rstrip("/")
    url = f"{public}/checkin" if public else f"http://127.0.0.1:{port}/checkin"

    if _port_open(host, port):
        print(f"Already running at {url}")
        if open_browser:
            webbrowser.open(url)
        print("Close the other WhatelseCheckin window to stop the server.")
        return

    try:
        server = make_server(host=host, port=port)
    except OSError as exc:
        print(f"Could not start server on {host}:{port}. {exc}")
        if open_browser and _port_open(host, port):
            webbrowser.open(url)
        raise SystemExit(1) from exc

    print("Whatelse check-in (SIBA + guest links + INE report).")
    print(f"Listening: {host}:{port}")
    print(f"Form:  {url}")
    print(f"List:  {public or f'http://127.0.0.1:{port}'}/list")
    print(f"Links: {public or f'http://127.0.0.1:{port}'}/links")
    print(f"INE:   {public or f'http://127.0.0.1:{port}'}/ine")
    if not on_railway:
        print("Leave this window open. Press Ctrl+C to stop.")

    if open_browser:
        threading.Thread(target=_open_browser, args=(url, host, port), daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
