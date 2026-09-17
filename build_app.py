"""
Genera l'eseguibile desktop con PyInstaller.

IMPORTANTE: PyInstaller non fa cross-compiling. Va eseguito:
  - su macOS per ottenere l'app .app (Intel/ARM: eseguire sull'architettura target,
    o build "universal2" se serve girare su entrambe)
  - su Windows per ottenere il .exe

Uso:
    python build_app.py

Output: dist/EstrattoreColtureAGEA(.app|.exe|/)
"""
import platform
import subprocess
import sys

NAME = "EstrattoreColtureAGEA"


def main() -> None:
    sep = ";" if platform.system() == "Windows" else ":"
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--windowed", "--onedir",
        "--name", NAME,
        f"--add-data=app.py{sep}.",
        f"--add-data=agea_parser.py{sep}.",
        f"--add-data=matching.py{sep}.",
        f"--add-data=storage.py{sep}.",
        # Streamlit ha risorse statiche (frontend) che PyInstaller non scopre
        # da solo: le includiamo esplicitamente tramite hook standard.
        "--collect-all", "streamlit",
        "--hidden-import", "streamlit.web.bootstrap",
        # app.py/agea_parser.py/matching.py/storage.py sono inclusi sopra come
        # semplici file dati (li esegue Streamlit a runtime, non li importa
        # desktop.py): PyInstaller non analizza i LORO import e quindi non
        # impacchetterebbe da solo fitz/pdfplumber/openpyxl/pandas. Vanno
        # dichiarati esplicitamente.
        "--collect-all", "fitz",
        "--collect-all", "pdfplumber",
        "--collect-all", "openpyxl",
        "--collect-all", "pandas",
        "--hidden-import", "agea_parser",
        "--hidden-import", "matching",
        "--hidden-import", "storage",
        "desktop.py",
    ]
    print(">", " ".join(args))
    subprocess.run(args, check=True)
    print(f"\nFatto. Output in dist/{NAME}/")


if __name__ == "__main__":
    main()
