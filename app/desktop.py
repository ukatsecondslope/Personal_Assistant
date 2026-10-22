"""Desktop entry point: runs the server in the background and shows the UI in a native window.

Started by the macOS app bundle built with scripts/make_mac_app.sh (or `python -m app.desktop`).
Closing the window stops the server.
"""
from __future__ import annotations

import logging
import socket
import subprocess
import threading
import time

import httpx

from . import config

# Configure logging before app.main does, so everything goes to a file (there is no terminal).
config.DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    filename=config.DATA_DIR / "desktop.log",
)
log = logging.getLogger("desktop")

import uvicorn  # noqa: E402
import webview  # noqa: E402

from .main import app  # noqa: E402

HOST = "127.0.0.1"
PREFERRED_PORT = 8000


def ensure_ollama(timeout: float = 30) -> bool:
    """Start Ollama.app if it isn't answering, and wait for it."""
    def up() -> bool:
        try:
            return httpx.get(f"{config.OLLAMA_URL}/api/version", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    if up():
        return True
    log.info("Ollama not running, starting Ollama.app")
    subprocess.run(["open", "-g", "-a", "Ollama"], check=False)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if up():
            return True
        time.sleep(0.5)
    log.warning("Ollama did not come up within %ss", timeout)
    return False


def free_port() -> int:
    """PREFERRED_PORT if it's free (e.g. ./run.sh isn't running), otherwise any free port."""
    for port in (PREFERRED_PORT, 0):
        with socket.socket() as s:
            try:
                s.bind((HOST, port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def wait_for_server(url: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{url}/api/health", timeout=10)
            return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise RuntimeError(f"server at {url} did not start")


def main() -> None:
    ensure_ollama()
    port = free_port()
    url = f"http://{HOST}:{port}"
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for_server(url)
    log.info("Serving on %s", url)

    def stop() -> None:
        # Runs on `closing`, which fires (and is waited for) both when the window is closed and
        # on Cmd+Q; Cmd+Q ends the process without returning from webview.start().
        if thread.is_alive():
            server.should_exit = True
            thread.join(timeout=10)
            log.info("Stopped")

    window = webview.create_window("Personal Assistant", url, width=1200, height=820, min_size=(720, 500))
    window.events.closing += stop
    webview.start()  # blocks on the main thread until the window is closed
    stop()


if __name__ == "__main__":
    main()
