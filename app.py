"""
Estrattore Colture da Fascicoli AGEA - POC Streamlit (100% locale, nessuna API).

Avvio:  streamlit run app.py
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from agea_parser import ca_to_str, parse_pdf
from matching import (
    costruisci_tabella,
    esporta_excel,
    leggi_particellare,
)
import storage

st.set_page_config(page_title="Estrattore Colture AGEA", layout="wide")

# Marcatore persistente (fuori dal progetto: sopravvive a un rebuild/pull e,
# nell'app desktop, all'estrazione in una cartella temporanea diversa ad ogni
# avvio) per mostrare il tutorial una sola volta, alla primissima apertura.
TUTORIAL_MARKER = Path.home() / ".agea_estrattore" / "tutorial_visto"


@st.dialog("Come funziona l'Estrattore Colture AGEA", width="large")
def mostra_tutorial() -> None:
    st.markdown(
        """
#### 1. Cosa caricare
- **Particellare di progetto** (`.xlsx`): foglio, particella, superficie, intestatario.
- **Fascicoli Aziendali AGEA** (`.pdf`): le ultime 3 annualità di ogni azienda
  che possiede/conduce le particelle del progetto — carica pure tutti i PDF
  che hai, anche di anni o aziende diverse, il tool prende solo quello che serve.

#### 2. Cosa fa il tool
Legge in ogni fascicolo la sezione *"Piano di coltivazione - Particelle
catastali"*, somma le superfici per particella e anno, e incrocia il
risultato con il particellare sulla coppia **Foglio + Particella**
(elaborazione locale, nessun dato inviato altrove).

#### 3. Cosa aspettarti in output
Una tabella con una riga per particella del progetto e una colonna per
ciascun anno trovato nei fascicoli caricati, es.:

| foglio | particella | colture_2023 | colture_2024 | colture_2025 | stato |
|---|---|---|---|---|---|
| 64 | 37 | girasole 1.95 ha | girasole 1.95 ha | grano duro 2.20 ha | OK |

- **stato = OK**: coltura trovata per tutti gli anni.
- **stato = parziale**: manca qualche anno (fascicolo non caricato per
  quell'annualità, o particella non dichiarata quell'anno).
- **stato = particella non trovata nei fascicoli**: nessuno dei PDF caricati
  dichiara quella particella — probabilmente manca il fascicolo di quel
  proprietario/conduttore.

Scarichi il risultato in Excel con il bottone in fondo alla pagina.

#### 4. Se qualcosa non torna
Il parser **non inventa mai un valore**: quando non riesce a leggere una riga
in modo affidabile (es. una particella in conflitto tra più atti, per cui
AGEA stesso non stampa la superficie), la salta e te lo scrive nella sezione
**Avvisi**, con foglio/particella/coltura interessati — va controllata a mano
sul fascicolo originale. Il dettaglio grezzo riga-per-riga (comprese le voci
non agricole come tare e fabbricati, escluse dal risultato perché non
producono PLV) resta consultabile in fondo, in *"Dettaglio record estratti"*.
        """
    )
    if st.button("Ho capito, inizia", type="primary"):
        st.rerun()


col_title, col_info = st.columns([8, 1])
with col_title:
    st.title("Estrattore Colture da Fascicoli AGEA — POC")
with col_info:
    st.write("")  # allinea verticalmente il bottone al titolo
    if st.button("ℹ️ Tutorial", use_container_width=True):
        mostra_tutorial()

st.caption(
    "Incrocia il particellare di progetto (Excel) con i Fascicoli Aziendali AGEA (PDF) "
    "e ricostruisce le colture per particella e per anno. Elaborazione locale, "
    "parser deterministico (nessun LLM)."
)

if not TUTORIAL_MARKER.exists():
    TUTORIAL_MARKER.parent.mkdir(parents=True, exist_ok=True)
    TUTORIAL_MARKER.write_text("1")
    mostra_tutorial()

RE_YEAR = re.compile(r"(20\d{2})")

col1, col2 = st.columns(2)
with col1:
    xls = st.file_uploader("1) Particellare di progetto (.xlsx)", type=["xlsx", "xls"])
with col2:
    pdfs = st.file_uploader(
        "2) Fascicoli Aziendali AGEA (.pdf) — selezione multipla",
        type=["pdf"],
        accept_multiple_files=True,
    )

if not xls or not pdfs:
    st.info("Carica l'Excel del progetto e almeno un PDF di fascicolo per procedere.")
    st.stop()

# --- parsing PDF -----------------------------------------------------------
parse_results = []
with st.spinner("Lettura fascicoli PDF…"):
    tmpdir = Path(tempfile.mkdtemp())
    for up in pdfs:
        data = up.getbuffer()
        sha = storage.file_sha256(bytes(data))
        cached = storage.get(sha)
        if cached is not None:
            cached.file = up.name
            parse_results.append(cached)
            continue
        p = tmpdir / up.name
        p.write_bytes(data)
        res = parse_pdf(str(p))
        storage.put(sha, res)
        parse_results.append(res)

st.subheader("Fascicoli caricati")
anni_override: dict[str, int] = {}
fasc_rows = []
for r in parse_results:
    guess = r.anno if r.anno else None
    fasc_rows.append(
        {
            "file": r.file,
            "anno rilevato": r.anno or "—",
            "fonte anno": r.anno_fonte,
            "righe estratte": len(r.records),
            "avvisi": len(r.warnings),
        }
    )
st.dataframe(pd.DataFrame(fasc_rows), use_container_width=True, hide_index=True)

with st.expander("Correggi manualmente l'anno di un fascicolo (se necessario)"):
    for r in parse_results:
        default = r.anno if r.anno else 2024
        val = st.number_input(
            f"{r.file}", min_value=2000, max_value=2100, value=int(default), step=1,
            key=f"anno_{r.file}",
        )
        if val != r.anno:
            for rec in r.records:
                rec.anno = int(val)
            r.anno = int(val)

# avvisi dei parser
tutti_avvisi = [f"[{r.file}] {w}" for r in parse_results for w in r.warnings]

# --- lettura Excel -------------------------------------------------------
prog_rows, xls_warn = leggi_particellare(xls.getbuffer())
tutti_avvisi += [f"[{xls.name}] {w}" for w in xls_warn]

anni = sorted({r.anno for r in parse_results if r.anno})
if not anni:
    st.error("Nessun anno determinabile dai fascicoli. Correggi gli anni qui sopra.")
    st.stop()

# --- tabella finale ----------------------------------------------------
df, note = costruisci_tabella(prog_rows, parse_results, anni)
tutti_avvisi += note

st.subheader(f"Risultato — {len(df)} particelle di progetto, anni {min(anni)}–{max(anni)}")

n_ok = (df["stato"] == "OK").sum()
n_none = (df["stato"] == "particella non trovata nei fascicoli").sum()
n_part = df["stato"].str.startswith("parziale").sum()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Complete", int(n_ok))
c2.metric("Parziali", int(n_part))
c3.metric("Non trovate", int(n_none))
c4.metric("Colonne anno", len(anni))


def _color(v: str):
    if v == "OK":
        return "background-color:#183d18"
    if str(v).startswith("parziale"):
        return "background-color:#4d3d10"
    if "non trovata" in str(v):
        return "background-color:#4d1616"
    return "background-color:#333010"


st.dataframe(
    df.style.map(_color, subset=["stato"]),
    use_container_width=True,
    hide_index=True,
)

st.download_button(
    "⬇️ Scarica risultato (Excel)",
    data=esporta_excel(df),
    file_name="colture_per_particella.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

if tutti_avvisi:
    st.subheader(f"Avvisi ({len(tutti_avvisi)})")
    st.warning(
        "Il parser NON inventa valori: quanto segue è stato saltato o va verificato "
        "a mano sul fascicolo originale."
    )
    for w in tutti_avvisi:
        st.write("• ", w)

with st.expander("Dettaglio record estratti (debug parser)"):
    det = [
        {
            "file": rec.file, "anno": rec.anno, "comune": rec.comune,
            "foglio": rec.foglio, "particella": rec.particella,
            "coltura": f"{rec.coltura_code} = {rec.coltura}",
            "sup (Ha,Aa,Ca)": ca_to_str(rec.superficie_ca),
            "inizio": rec.data_inizio, "fine": rec.data_fine,
            "macrouso": rec.is_macro,
        }
        for r in parse_results for rec in r.records
    ]
    st.dataframe(pd.DataFrame(det), use_container_width=True, hide_index=True)
