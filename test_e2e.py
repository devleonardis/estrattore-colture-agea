"""
Smoke test end-to-end sui documenti reali (3 fascicoli DI ROSA ANGELO EMILIO).

Non e' un test unitario esaustivo: verifica che la pipeline giri, che il parser
estragga righe da tutti e 3 gli anni e che il join produca le colonne colture.

Uso:  ./.venv/bin/python test_e2e.py [cartella_pdf] [excel]
Default: ~/Downloads con i nomi dei file di esempio + demo_particellare.xlsx
"""
import sys
from pathlib import Path

from agea_parser import parse_pdf, aggrega
from matching import leggi_particellare, costruisci_tabella, esporta_excel

DL = Path.home() / "Downloads"
PDFS = [DL / f"DI ROSA ANGELO EMILIO {y}.pdf" for y in (2023, 2024, 2025)]
XLS = Path(__file__).with_name("demo_particellare.xlsx")


def main() -> int:
    pdfs = PDFS
    xls = XLS
    if len(sys.argv) > 1:
        pdfs = sorted(Path(sys.argv[1]).glob("*.pdf"))
    if len(sys.argv) > 2:
        xls = Path(sys.argv[2])

    missing = [p for p in [*pdfs, xls] if not p.exists()]
    if missing:
        print("FILE MANCANTI:", *missing, sep="\n  ")
        return 2

    prs = [parse_pdf(str(p)) for p in pdfs]
    for r in prs:
        print(f"{r.file:40s} anno={r.anno} righe={len(r.records):3d} avvisi={len(r.warnings)}")
        assert r.records, f"nessun record estratto da {r.file}"

    agg, agg_warn = aggrega(prs)
    assert agg, "aggregazione vuota"

    rows, w = leggi_particellare(xls.read_bytes())
    assert rows, "particellare vuoto"
    anni = sorted({r.anno for r in prs})
    df, note = costruisci_tabella(rows, prs, anni)

    pop = df[[c for c in df.columns if c.startswith("colture_")]].astype(bool).any(axis=1).sum()
    print(f"\nparticellare: {len(df)} righe, {pop} con almeno una coltura valorizzata")
    print(df.to_string(index=False))
    assert pop >= 1, "nessuna particella incrociata: join rotto?"

    out = Path("test_output.xlsx")
    out.write_bytes(esporta_excel(df))
    print(f"\nexport -> {out} ({out.stat().st_size} byte)")
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
