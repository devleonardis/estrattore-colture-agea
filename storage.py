"""
Cache locale su SQLite dei risultati di parsing dei PDF.

Scopo nel POC: evitare di ri-parsare lo stesso fascicolo ad ogni interazione di
Streamlit. La chiave è lo sha256 del file, quindi la cache è valida anche se il
PDF viene rinominato. Nessun dato sensibile oltre al contenuto già fornito
dall'utente; il file .sqlite resta in locale.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from agea_parser import ColturaRecord, ParseResult

# NON accanto a __file__: nell'app impacchettata (PyInstaller) i sorgenti
# vivono in una cartella temporanea di sola lettura (e macOS, per un'app
# scaricata non firmata, la esegue pure da un percorso "translocato"
# diverso ad ogni avvio) — scriverci fallisce con
# "sqlite3.OperationalError: unable to open database file". Usiamo una
# cartella nella home dell'utente, stabile e scrivibile sia da sorgente sia
# da eseguibile, condivisa con il marcatore del tutorial (vedi app.py).
DB_PATH = Path.home() / ".agea_estrattore" / "agea_cache.sqlite"


def _ensure_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _conn() -> sqlite3.Connection:
    _ensure_dir()
    c = sqlite3.connect(DB_PATH)
    c.execute(
        """CREATE TABLE IF NOT EXISTS parse_cache (
               sha256    TEXT PRIMARY KEY,
               file_name TEXT,
               anno      INTEGER,
               anno_fonte TEXT,
               records   TEXT,
               warnings  TEXT,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    return c


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get(sha: str) -> ParseResult | None:
    with _conn() as c:
        row = c.execute(
            "SELECT file_name, anno, anno_fonte, records, warnings "
            "FROM parse_cache WHERE sha256=?",
            (sha,),
        ).fetchone()
    if not row:
        return None
    file_name, anno, anno_fonte, records_j, warnings_j = row
    res = ParseResult(file=file_name, anno=anno, anno_fonte=anno_fonte)
    res.records = [ColturaRecord(**d) for d in json.loads(records_j)]
    res.warnings = json.loads(warnings_j)
    return res


def put(sha: str, res: ParseResult) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO parse_cache "
            "(sha256, file_name, anno, anno_fonte, records, warnings) "
            "VALUES (?,?,?,?,?,?)",
            (
                sha,
                res.file,
                res.anno,
                res.anno_fonte,
                json.dumps([asdict(r) for r in res.records], ensure_ascii=False),
                json.dumps(res.warnings, ensure_ascii=False),
            ),
        )
