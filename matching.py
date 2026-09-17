"""Incrocio particellare di progetto (Excel) <-> colture estratte dai fascicoli AGEA."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pandas as pd

from agea_parser import Aggregato, ParseResult, aggrega

HEADER_ALIASES = {
    "foglio": {"foglio", "fog", "fog."},
    "particella": {"p.lla", "plla", "particella", "part", "part.", "p.la", "mappale"},
    "superficie": {"superficie", "sup", "sup.", "superficie (mq)", "mq"},
    "intestatario": {"intestatario", "ditta", "proprietario", "intestatari"},
}


@dataclass
class ProgettoRow:
    idx: int
    foglio: int | None
    particella: int | None
    superficie: str
    intestatario: str
    raw_foglio: str
    raw_particella: str


def _norm_int(v) -> int | None:
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() == "nan":
        return None
    m = re.search(r"\d+", s)
    return int(m.group(0)) if m else None


def _find_header_row(df: pd.DataFrame) -> int:
    wanted = {"foglio", "particella"}
    for i in range(min(15, len(df))):
        cells = {str(c).strip().lower() for c in df.iloc[i].tolist()}
        hits = set()
        for key, aliases in HEADER_ALIASES.items():
            if cells & aliases:
                hits.add(key)
        if wanted <= hits:
            return i
    return 2  # default previsto dal formato tipico (intestazione a riga 3)


def leggi_particellare(file_bytes: bytes) -> tuple[list[ProgettoRow], list[str]]:
    warnings: list[str] = []
    raw = pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str)
    hrow = _find_header_row(raw)
    header = [str(c).strip().lower() for c in raw.iloc[hrow].tolist()]

    colmap: dict[str, int] = {}
    for j, name in enumerate(header):
        for key, aliases in HEADER_ALIASES.items():
            if name in aliases and key not in colmap:
                colmap[key] = j
    for key in ("foglio", "particella"):
        if key not in colmap:
            warnings.append(
                f"Colonna '{key}' non trovata nell'intestazione (riga {hrow + 1}); "
                f"controllare il file."
            )

    rows: list[ProgettoRow] = []
    for i in range(hrow + 1, len(raw)):
        vals = raw.iloc[i].tolist()

        def get(key: str) -> str:
            j = colmap.get(key)
            if j is None or j >= len(vals):
                return ""
            v = vals[j]
            return "" if v is None or str(v).lower() == "nan" else str(v).strip()

        rf, rp = get("foglio"), get("particella")
        rint, rsup = get("intestatario"), get("superficie")
        if not any([rf, rp, rint, rsup]):
            continue  # riga vuota
        rows.append(
            ProgettoRow(
                idx=i + 1,
                foglio=_norm_int(rf),
                particella=_norm_int(rp),
                superficie=rsup,
                intestatario=rint,
                raw_foglio=rf,
                raw_particella=rp,
            )
        )
    return rows, warnings


def costruisci_tabella(
    prog_rows: list[ProgettoRow],
    parse_results: list[ParseResult],
    anni: list[int],
) -> tuple[pd.DataFrame, list[str]]:
    agg, agg_warn = aggrega(parse_results)

    out_records = []
    note: list[str] = list(agg_warn)
    for pr in prog_rows:
        rec: dict[str, object] = {
            "riga_excel": pr.idx,
            "foglio": pr.raw_foglio,
            "particella": pr.raw_particella,
            "superficie_progetto": pr.superficie,
            "intestatario": pr.intestatario,
        }
        trovata_almeno_uno = False
        for anno in anni:
            key = (pr.foglio, pr.particella, anno)
            a: Aggregato | None = agg.get(key)
            if a and a.per_coltura_ca:
                rec[f"colture_{anno}"] = a.testo_plv()
                trovata_almeno_uno = True
            else:
                rec[f"colture_{anno}"] = ""
        if pr.foglio is None or pr.particella is None:
            rec["stato"] = "riga senza foglio/particella (comproprietario?)"
        elif not trovata_almeno_uno:
            rec["stato"] = "particella non trovata nei fascicoli"
        else:
            mancanti = [a for a in anni if not rec[f"colture_{a}"]]
            rec["stato"] = (
                "OK" if not mancanti
                else "parziale (mancano: " + ", ".join(map(str, mancanti)) + ")"
            )
        out_records.append(rec)

    cols = [
        "riga_excel", "foglio", "particella", "superficie_progetto", "intestatario",
        *[f"colture_{a}" for a in anni], "stato",
    ]
    df = pd.DataFrame(out_records, columns=cols)
    return df, note


def esporta_excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        df.to_excel(xl, index=False, sheet_name="colture_per_particella")
        ws = xl.sheets["colture_per_particella"]
        for col in ws.columns:
            width = max((len(str(c.value)) for c in col if c.value is not None), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(60, max(12, width + 2))
    return buf.getvalue()
