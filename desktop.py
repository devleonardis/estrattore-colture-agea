"""
Wrapper desktop per l'app Streamlit: avvia il server su localhost (porta libera
scelta automaticamente) in un processo separato e lo mostra in una finestra
nativa (pywebview) invece che nel browser. Tutto gira in locale sulla macchina
dell'utente, nessun hosting/server esterno.

Uso da sorgente:    python desktop.py
Uso da eseguibile:  vedi build_app.py (PyInstaller) -> un file .app (macOS) /
                    .exe (Windows) da distribuire, senza bisogno di installare
                    Python sulla macchina di destinazione.

Nota tecnica: il server Streamlit viene avviato in un SOTTOPROCESSO (non in un
thread) perche' `streamlit.web.bootstrap.run` installa signal handler validi
solo nel thread principale. In sorgente il sottoprocesso e' `python -m
streamlit`; nell'eseguibile PyInstaller (che non ha un `python` a parte) il
sottoprocesso e' lo stesso eseguibile richiamato con `--streamlit-server`,
gestito qui sotto in `_serve()`.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

FLAG_SERVE = "--streamlit-server"


def _app_path() -> Path:
    # PyInstaller estrae i file in una cartella temporanea (sys._MEIPASS)
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "app.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(port: int) -> None:
    """Eseguito nel sottoprocesso: avvia il server Streamlit (blocca)."""
    from streamlit.web import bootstrap

    flag_options = {
        "server.port": port,
        "server.address": "127.0.0.1",
        "server.headless": True,
        "global.developmentMode": False,
        "browser.gatherUsageStats": False,
    }
    bootstrap.load_config_options(flag_options=flag_options)
    bootstrap.run(str(_app_path()), is_hello=False, args=[], flag_options=flag_options)


def _spawn_server(port: int) -> subprocess.Popen:
    if getattr(sys, "frozen", False):
        # eseguibile PyInstaller: richiama se stesso in modalita' server
        cmd = [sys.executable, FLAG_SERVE, str(port)]
    else:
        cmd = [sys.executable, "-m", "streamlit", "run", str(_app_path()),
               "--server.port", str(port), "--server.address", "127.0.0.1",
               "--server.headless", "true", "--browser.gatherUsageStats", "false"]
    return subprocess.Popen(cmd)


def _wait_ready(url: str, timeout_s: float = 30.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == FLAG_SERVE:
        _serve(int(sys.argv[2]))
        return

    import webview

    port = _free_port()
    proc = _spawn_server(port)
    url = f"http://127.0.0.1:{port}"
    try:
        if not _wait_ready(url):
            raise RuntimeError("Il server locale non ha risposto in tempo.")
        webview.create_window(
            "Estrattore Colture AGEA",
            url,
            width=1280,
            height=860,
            min_size=(900, 600),
        )
        webview.start()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
