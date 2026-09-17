"""
Parser deterministico (nessun LLM) per la sezione
"PIANO DI COLTIVAZIONE - PARTICELLE CATASTALI" dei Fascicoli Aziendali AGEA.

Estrae, per ogni riga della tabella:
    comune, foglio, particella, sub, codice/descrizione occupazione del suolo,
    superficie coltivata (Ha,Aa,Ca), date inizio/fine coltivazione.

Poi aggrega per (foglio, particella, anno) sommando le superfici per coltura.

Robustezza: il layout AGEA cambia leggermente da anno ad anno / da CAA a CAA,
quindi NON ci basiamo su posizioni di riga fisse ma su ancore testuali:
 - la riga "NNN = DESCRIZIONE" (occupazione del suolo) come inizio-blocco
 - il progressivo "N)" come fine-blocco
 - il pattern superficie "dd,dd,dd" (Ha,Aa,Ca) come ultimo valore prima del progressivo
Le righe che non rispettano gli assunti vengono raccolte in `warnings`, non inventate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

# ---------------------------------------------------------------------------
# Regex / costanti
# ---------------------------------------------------------------------------
RE_OCCUPAZIONE = re.compile(r"^(\d{1,4})\s*=\s*(\S.*?)\s*$")          # "005 = GIRASOLE"
RE_SUP = re.compile(r"^(\d{1,3}),(\d{2}),(\d{2})$")                    # "01,29,33"
RE_PROG = re.compile(r"^(\d{1,4})\)$")                                 # "4)"
RE_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
RE_FOG = re.compile(r"^\d{1,4}$")
RE_PART = re.compile(r"^\d{1,5}$")
RE_YEAR = re.compile(r"(20\d{2})")

SECTION_START = "PIANO DI COLTIVAZIONE - PARTICELLE CATASTALI"
# rumore che puo' comparire tra occupazione e superficie
NOISE_EXACT = {
    "NO", "SI", "N.D.", "ND", "Seminativo", "NON IRRIGATO", "IRRIGATO",
    "000", "TRADIZIONALE",
}

# Codici "macrouso": righe di riepilogo che sommano gli appezzamenti sottostanti.
# Se per la stessa particella/anno esiste anche il dettaglio delle colture,
# vanno SCARTATE per non contare due volte la superficie.
MACRO_CODES = {"666", "651"}
MACRO_LABEL = {
    "666": "SEMINATIVI (coltura non specificata)",
    "651": "COLTIVAZIONI ARBOREE (coltura non specificata)",
}

# Codici che NON vanno mai considerati "dettaglio" di un macrouso: sono
# colture/usi di un'altra categoria (arborea, non agricola) che possono
# comparire nella stessa particella/anno senza sostituire il riepilogo
# 666/651 (es. un oliveto non spiega il dettaglio del seminativo).
NON_DETTAGLIO_CODES = {
    "420", "410",  # arboree (olivo, vite): non sostituiscono un macro seminativo
    "650", "660", "690", "770", "780", "788", "156", "157",  # non agricolo/altro
}

# Codici "occupazione del suolo" che NON sono colture (non producono PLV):
# tare, fabbricati, incolti, boschi, siepi, margini, canali, superfici
# ritirate dalla produzione (set-aside). Esclusi dalla vista semplificata
# "colture per PLV"; restano visibili nel dettaglio grezzo (debug).
NON_COLTURA_CODES = {
    "156", "157", "214", "650", "660", "690", "770", "780",
    "785", "786", "788", "789", "791",
}

# Nomi semplificati (minuscolo, come nel formato di lavoro dello studio) per
# i codici colturali osservati nei fascicoli reali. Un codice non presente
# qui usa come fallback la descrizione AGEA in minuscolo (vedi nome_coltura_plv).
CROP_NAME_MAP = {
    "002": "grano duro",
    "005": "girasole",
    "420": "olivo",
    "410": "vite",
    "800": "erbaio",
    "870": "orzo",
    "020": "pisello",
    "544": "cece",
    "379": "trifoglio",
    "065": "pascolo polifita",
    "054": "pascolo arborato (tara 50%)",
    "063": "pascolo polifita (roccia affiorante, tara 20%)",
    "666": "seminativi (non specificata)",
    "651": "colture arboree (non specificata)",
}


def nome_coltura_plv(code: str, desc: str) -> str:
    if code in CROP_NAME_MAP:
        return CROP_NAME_MAP[code]
    return desc.lower()

# Frasi che AGEA stampa quando un appezzamento e' oggetto di un conflitto tra
# piu' atti (domande sovrapposte) e per questo la superficie coltivata NON
# viene riportata nel fascicolo stesso (non e' un mancato riconoscimento del
# parser: il dato non e' proprio presente nel PDF).
CONFLITTO_ATTI_MARKERS = (
    "SUP. RICHIESTA > SUP.",
    "SUPERO TRA PIU' ATTI",
    "AMBITO DI PIU' ATTI",
)


@dataclass
class ColturaRecord:
    file: str
    anno: int
    comune: str
    foglio: int
    particella: int
    sub: str
    coltura_code: str
    coltura: str
    superficie_ca: int          # superficie in centiare (1 ha = 10.000 ca)
    data_inizio: str | None
    data_fine: str | None
    progressivo: str | None
    is_macro: bool = False


@dataclass
class ParseResult:
    file: str
    anno: int
    anno_fonte: str             # come e' stato determinato l'anno
    records: list[ColturaRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utility superfici
# ---------------------------------------------------------------------------
def sup_to_ca(ha: int, aa: int, ca: int) -> int:
    return ha * 10_000 + aa * 100 + ca


def ca_to_str(ca_tot: int) -> str:
    """Centiare -> 'H,AA,CA' (formato italiano AGEA)."""
    ha, rem = divmod(ca_tot, 10_000)
    aa, ca = divmod(rem, 100)
    return f"{ha},{aa:02d},{ca:02d}"


def ca_to_ha_float(ca_tot: int) -> float:
    return round(ca_tot / 10_000, 4)


# ---------------------------------------------------------------------------
# Estrazione testo
# ---------------------------------------------------------------------------
def _pdf_lines(path: str) -> list[str]:
    doc = fitz.open(path)
    out: list[str] = []
    for page in doc:
        for raw in page.get_text().splitlines():
            out.append(raw.strip())
    doc.close()
    return out


def _guess_year(path: str, lines: list[str]) -> tuple[int | None, str]:
    m = RE_YEAR.search(Path(path).stem)
    if m:
        return int(m.group(1)), "nome file"
    # fallback: anno piu' frequente nelle date fine coltivazione
    years: dict[int, int] = {}
    for ln in lines:
        d = RE_DATE.match(ln)
        if d:
            y = int(d.group(3))
            years[y] = years.get(y, 0) + 1
    if years:
        y = max(years, key=years.get)
        return y, "date coltivazione"
    for ln in lines:
        if ln.startswith("Data Stampa:"):
            m = RE_YEAR.search(ln)
            if m:
                return int(m.group(1)), "data stampa"
    return None, "sconosciuto"


# ---------------------------------------------------------------------------
# Parsing sezione PARTICELLE CATASTALI
# ---------------------------------------------------------------------------
def _looks_like_catasto_anchor(lines: list[str], i: int) -> tuple[str, str, str] | None:
    """
    `i` indicizza una riga "NNN = DESC". Verifica che sia una riga della tabella
    PARTICELLE CATASTALI risalendo a: <comune> <foglio> <particella> [sub].
    Ritorna (comune, foglio, particella) oppure None.
    """
    j = i - 1
    # salta eventuale sub molto corto? di norma qui non c'e' sub; i campi
    # immediatamente sopra sono particella e foglio.
    prev = [x for x in lines[max(0, i - 6):i] if x != ""]
    if len(prev) < 3:
        return None
    part, fog, comune = prev[-1], prev[-2], prev[-3]
    if not (RE_PART.match(part) and RE_FOG.match(fog)):
        return None
    if int(part) == 0:
        return None
    # comune: deve contenere lettere e non essere un numero / ISOLA / progressivo
    if not re.search(r"[A-Za-zÀ-ÿ]", comune):
        return None
    if comune.isdigit() or RE_PROG.match(comune):
        return None
    return comune, fog, part


def _scarta_dichiarazioni_future(res: ParseResult) -> None:
    """Rimuove, IN PLACE, le righe di una campagna futura gia' dichiarate
    nello stesso fascicolo insieme alla coltura corrente.

    Un fascicolo puo' riportare, per la stessa particella, sia la coltura
    della campagna in corso/chiusura (inizio a novembre dell'anno PRIMA di
    quello del fascicolo) sia una gia' dichiarata per la campagna successiva
    (inizio a novembre dell'anno STESSO del fascicolo o oltre) — anche con lo
    stesso codice-coltura (es. grano su grano). Non sono due colture
    praticate nello stesso anno: la seconda e' un impegno futuro. Quando per
    una particella coesiste almeno una riga "corrente" (inizio < anno
    fascicolo) con una o piu' righe "future" (inizio >= anno fascicolo), le
    righe future vengono scartate. Se una particella ha SOLO righe "future"
    (nessuna corrente con cui competere), vengono mantenute: e' l'unico dato
    disponibile per quella particella in questo fascicolo.
    """
    if not res.anno:
        return

    by_parcel: dict[tuple[int, int], list[ColturaRecord]] = {}
    for rec in res.records:
        if rec.data_inizio and not rec.is_macro:
            by_parcel.setdefault((rec.foglio, rec.particella), []).append(rec)

    to_drop: list[ColturaRecord] = []
    for group in by_parcel.values():
        correnti = [r for r in group if int(r.data_inizio.rsplit("/", 1)[-1]) < res.anno]
        future = [r for r in group if int(r.data_inizio.rsplit("/", 1)[-1]) >= res.anno]
        if correnti and future:
            to_drop.extend(future)

    if not to_drop:
        return
    drop_ids = {id(r) for r in to_drop}
    res.records = [r for r in res.records if id(r) not in drop_ids]
    for r in to_drop:
        res.warnings.append(
            f"Foglio {r.foglio} part. {r.particella} coltura '{r.coltura_code}={r.coltura}': "
            f"riga esclusa, e' una dichiarazione per una campagna successiva "
            f"(inizio {r.data_inizio}) gia' presente nel fascicolo {res.file} "
            f"insieme alla coltura dell'annualita' corrente."
        )


def parse_pdf(path: str) -> ParseResult:
    lines = _pdf_lines(path)
    year, year_src = _guess_year(path, lines)
    res = ParseResult(file=Path(path).name, anno=year or 0, anno_fonte=year_src)
    if year is None:
        res.warnings.append(
            "Impossibile determinare l'anno della campagna: rinominare il file "
            "includendo l'anno (es. '... 2024.pdf')."
        )

    # individua l'inizio della sezione; se l'header non e' presente nel flusso
    # testuale (capita per l'ordine di estrazione), si parte da 0 e ci si affida
    # al riconoscimento d'ancora catastale.
    start = 0
    for idx, ln in enumerate(lines):
        if SECTION_START in ln:
            start = 0  # le righe possono precedere l'header: scansione completa
            break

    seen_section = any(SECTION_START in ln for ln in lines)
    if not seen_section:
        res.warnings.append(
            "Sezione 'PIANO DI COLTIVAZIONE - PARTICELLE CATASTALI' non trovata "
            "nel PDF: nessuna coltura estratta da questo file."
        )
        return res

    n = len(lines)
    i = start
    while i < n:
        m = RE_OCCUPAZIONE.match(lines[i])
        if not m:
            i += 1
            continue
        anchor = _looks_like_catasto_anchor(lines, i)
        if anchor is None:
            i += 1
            continue
        comune, fog, part = anchor
        code, desc = m.group(1), m.group(2).strip()
        is_macro = code in MACRO_CODES
        if is_macro:
            desc = MACRO_LABEL.get(code, desc)

        # finestra fino al progressivo "N)" (o max 30 righe)
        window_end = min(n, i + 32)
        prog = None
        sup_ca: int | None = None
        dates: list[str] = []
        last_sup_before_prog: int | None = None
        conflitto_atti = False
        k = i + 1
        while k < window_end:
            ln = lines[k]
            pm = RE_PROG.match(ln)
            sm = RE_SUP.match(ln)
            dm = RE_DATE.match(ln)
            if any(marker in ln for marker in CONFLITTO_ATTI_MARKERS):
                conflitto_atti = True
            if sm:
                last_sup_before_prog = sup_to_ca(
                    int(sm.group(1)), int(sm.group(2)), int(sm.group(3))
                )
            if dm:
                dates.append(f"{dm.group(1)}/{dm.group(2)}/{dm.group(3)}")
            # una nuova occupazione prima del progressivo => blocco senza superficie
            if k > i and RE_OCCUPAZIONE.match(ln) and _looks_like_catasto_anchor(lines, k):
                break
            if pm:
                prog = pm.group(1)
                sup_ca = last_sup_before_prog
                break
            k += 1

        if sup_ca is None:
            if conflitto_atti:
                res.warnings.append(
                    f"Foglio {int(fog)} part. {int(part)} coltura '{code}={desc}': "
                    f"AGEA non riporta la superficie coltivata perche' la particella e' "
                    f"in conflitto tra piu' atti/domande (superficie richiesta > "
                    f"superficie eligibile, o 'in supero'); riga ignorata. Verificare "
                    f"manualmente sul fascicolo originale."
                )
            else:
                res.warnings.append(
                    f"Foglio {int(fog)} part. {int(part)} coltura '{code}={desc}': "
                    f"superficie coltivata non riconosciuta, riga ignorata."
                )
            i += 1
            continue

        data_inizio = dates[0] if dates else None
        data_fine = dates[1] if len(dates) > 1 else None

        res.records.append(
            ColturaRecord(
                file=res.file,
                anno=year or 0,
                comune=comune,
                foglio=int(fog),
                particella=int(part),
                sub="",
                coltura_code=code,
                coltura=desc,
                superficie_ca=sup_ca,
                data_inizio=data_inizio,
                data_fine=data_fine,
                progressivo=prog,
                is_macro=is_macro,
            )
        )
        i = k + 1 if prog else i + 1

    _scarta_dichiarazioni_future(res)

    if not res.records:
        res.warnings.append(
            "Nessuna riga catastale estratta pur essendo presente la sezione: "
            "il layout di questo PDF potrebbe differire da quelli gestiti."
        )
    return res


# ---------------------------------------------------------------------------
# Aggregazione
# ---------------------------------------------------------------------------
@dataclass
class Aggregato:
    foglio: int
    particella: int
    anno: int
    per_coltura_ca: dict[str, int] = field(default_factory=dict)
    per_codice_ca: dict[str, int] = field(default_factory=dict)
    files: set[str] = field(default_factory=set)

    def testo(self) -> str:
        """Vista grezza: tutte le voci (colture + tare/fabbricati/ecc.), formato Ha,Aa,Ca."""
        parti = [
            f"{col} {ca_to_str(ca)} ha"
            for col, ca in sorted(self.per_coltura_ca.items(), key=lambda t: -t[1])
        ]
        return "; ".join(parti)

    def testo_plv(self) -> str:
        """Vista per PLV: solo colture reali, ettari decimali (2 cifre), niente
        tare/fabbricati/incolti/set-aside — formato pronto per il piano agronomico.
        Residui che arrotondano a 0.00 ha vengono omessi (rumore di poligoni
        marginali), a meno che siano l'unica voce disponibile."""
        voci = sorted(self.per_codice_ca.items(), key=lambda t: -t[1])
        rilevanti = [(n, c) for n, c in voci if ca_to_ha_float(c) >= 0.005]
        if not rilevanti and voci:
            rilevanti = voci[:1]
        return "; ".join(f"{nome} {ca_to_ha_float(ca):.2f} ha" for nome, ca in rilevanti)

    def ha_coltura(self) -> bool:
        return bool(self.per_codice_ca)


def aggrega(
    results: list[ParseResult],
) -> tuple[dict[tuple[int, int, int], Aggregato], list[str]]:
    """Aggrega i record per (foglio, particella, anno).

    Gestione macrouso: se per una particella/anno esistono sia righe di dettaglio
    coltura sia righe macrouso (666/651), le macrouso vengono ignorate (doppio
    conteggio); se esistono SOLO righe macrouso, vengono tenute con etichetta
    esplicita e viene emesso un avviso.

    L'anno di ogni riga e' quello dichiarato dal fascicolo di origine (vedi
    parse_pdf); le righe di impegni pluriennali dichiarati in anticipo sono
    gia' state escluse a monte, in parse_pdf.
    """
    warnings: list[str] = []

    # raggruppa i record grezzi
    grezzi: dict[tuple[int, int, int], list[ColturaRecord]] = {}
    for r in results:
        for rec in r.records:
            grezzi.setdefault((rec.foglio, rec.particella, rec.anno), []).append(rec)

    agg: dict[tuple[int, int, int], Aggregato] = {}
    for key, recs in grezzi.items():
        fg, pl, an = key
        has_detail = any(
            not x.is_macro and x.coltura_code not in NON_DETTAGLIO_CODES
            for x in recs
        )
        a = Aggregato(fg, pl, an)
        for rec in recs:
            if rec.is_macro and has_detail:
                continue  # riepilogo ridondante: usiamo il dettaglio
            if rec.is_macro:
                warnings.append(
                    f"Foglio {fg} part. {pl} anno {an}: disponibile solo il "
                    f"macrouso '{rec.coltura}', il dettaglio delle colture non e' "
                    f"riportato nel fascicolo."
                )
            a.per_coltura_ca[rec.coltura] = (
                a.per_coltura_ca.get(rec.coltura, 0) + rec.superficie_ca
            )
            if rec.coltura_code not in NON_COLTURA_CODES:
                nome = nome_coltura_plv(rec.coltura_code, rec.coltura)
                a.per_codice_ca[nome] = a.per_codice_ca.get(nome, 0) + rec.superficie_ca
            a.files.add(rec.file)
        agg[key] = a
    return agg, warnings


if __name__ == "__main__":
    import sys

    for p in sys.argv[1:]:
        r = parse_pdf(p)
        print(f"\n=== {r.file}  anno={r.anno} ({r.anno_fonte})  righe={len(r.records)}")
        for w in r.warnings:
            print("  ! ", w)
        agg, aw = aggrega([r])
        for w in aw:
            print("  ! ", w)
        for (fg, pl, an), a in sorted(agg.items()):
            print(f"  {fg}/{pl:05d} {an}: {a.testo()}")
