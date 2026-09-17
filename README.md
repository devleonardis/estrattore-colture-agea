# Estrattore Colture da Fascicoli AGEA — POC

Tool interno per incrociare il **particellare di progetto** (Excel) con i
**Fascicoli Aziendali AGEA** (PDF, più annualità) e ricostruire, per ogni
particella catastale e per ogni anno, le **colture praticate** con le relative
superfici. Base per il calcolo della PLV ante-operam.

- 100% locale, nessuna API a pagamento, nessun servizio cloud.
- Estrazione PDF **deterministica**: parsing testuale (PyMuPDF) + regex/euristiche
  tarate sulla sezione *"PIANO DI COLTIVAZIONE - PARTICELLE CATASTALI"*. Nessun LLM.
- Il parser **non inventa valori**: ciò che non riesce a interpretare in modo
  affidabile finisce nella sezione *Avvisi*, non nel risultato.

## Avvio

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Si apre su http://localhost:8501. Nella UI:

1. Carica il file Excel del particellare di progetto.
2. Carica N PDF di fascicoli AGEA (drag & drop multiplo).
3. Controlla la tabella di anteprima (stati: `OK` / `parziale` / `particella non
   trovata` / `riga senza foglio/particella`).
4. Scarica il risultato in Excel.

L'anno di ogni fascicolo è dedotto dal **nome file** (`... 2024.pdf`); se manca,
si usano le date di fine coltivazione o la data di stampa. È comunque
correggibile a mano nella UI.

## Smoke test sui documenti reali

```bash
./.venv/bin/python test_e2e.py
```

Usa i 3 fascicoli `DI ROSA ANGELO EMILIO 2023/2024/2025.pdf` in `~/Downloads` e
`demo_particellare.xlsx` (particellare di esempio costruito sulle stesse
particelle del fascicolo, per verificare il join).

`./.venv/bin/python agea_parser.py <file.pdf> ...` stampa l'estrazione grezza di
un singolo PDF.

## Formato degli input

### Excel particellare
Intestazione tipicamente a riga 3, colonne: `foglio | p.lla | superficie |
intestatario`. L'header viene individuato automaticamente (accetta alias:
`fog.`, `particella`, `mappale`, `mq`, `ditta`…). `superficie` in mq, trattata
come stringa. Righe con `foglio`/`p.lla` vuoti (comproprietari) vengono marcate e
non rompono il parsing.

### PDF fascicolo AGEA
Viene letta **solo** la sezione *PIANO DI COLTIVAZIONE - PARTICELLE CATASTALI*.
Ogni blocco: `Comune / Sez / Fog / Part / Sub`, `Occupazione del suolo`
(`005 = GIRASOLE`), `Superficie coltivata (Ha,Aa,Ca)` in formato italiano
(`01,29,33` = 1 ha 29 are 33 ca), date inizio/fine.
Una particella è quasi sempre spezzata in più appezzamenti: il tool **aggrega
per particella + anno**, sommando le superfici per ciascuna coltura.

Il matching è su **Foglio + Particella** (il Comune è estratto e mostrato nel
debug ma non usato per il join: il progetto riguarda un solo comune).

## Output

Excel `colture_per_particella.xlsx`:

| riga_excel | foglio | particella | superficie_progetto | intestatario | colture_2023 | colture_2024 | colture_2025 | stato |

Contenuto delle colonne `colture_<anno>`: solo colture reali (niente tare,
fabbricati, incolti, set-aside), ettari decimali a 2 cifre, formato pronto
per il piano agronomico — es. `girasole 1.95 ha` oppure, con più colture
nella stessa particella/anno, `grano duro 0.32 ha; orzo 0.15 ha`. Le voci non
agricole e il dettaglio grezzo per-appezzamento restano visibili nell'app,
sezione "Dettaglio record estratti (debug parser)".

## Limiti noti del parser

- **Solo il layout AGEA visto nei campioni** (CAA1716, campagne 2023–2025, PDF con
  layer testo). Un CAA diverso o un export con colonne in ordine diverso può
  richiedere ritocchi alle euristiche in `agea_parser.py`. Le righe non
  riconosciute vengono segnalate, non silenziosamente perse.
- **PDF scansionati / immagine**: non gestiti (nessun OCR). Serve un PDF con testo
  selezionabile.
- **Righe "macrouso" (codici `666 = SEMINATIVI`, `651 = COLTIVAZIONI ARBOREE`)**:
  nel 2025 il fascicolo elenca sia il macrouso sia il dettaglio delle colture per
  la stessa particella → verrebbe contato due volte. Quando esiste il dettaglio,
  il macrouso viene **scartato**; quando esiste **solo** il macrouso, viene tenuto
  con etichetta `(coltura non specificata)` e viene emesso un avviso.
- **Doppia coltura nello stesso anno** (avvicendamento/secondo raccolto, es.
  particella 14/51 nel 2025 con GRANO + GIRASOLE): entrambe vengono riportate
  così come dichiarate nel fascicolo. Nessuna logica per "quale sia la principale".
- **Sub particella**: estratto come campo ma non usato nel match né
  nell'aggregazione (nei campioni è sempre vuoto in questa sezione).
- **Quote di comproprietà** nell'intestatario: trattate come stringa libera,
  nessun calcolo aritmetico (come da specifica POC).
- **Superficie**: piccole differenze (ordine dei centiare) tra il totale
  aggregato e i riepiloghi del fascicolo sono possibili sulle voci non agricole
  (TARE, fabbricati) per via degli arrotondamenti riga-per-riga di AGEA.
- **Anno**: se il nome file non contiene `20xx` e il PDF non ha date utili,
  il fascicolo va assegnato manualmente all'anno nella UI.
- **Particelle in conflitto tra più atti** (AGEA stampa *"PART. NELL'AMBITO DI
  PIU' ATTI CON SUP. RICHIESTA > SUP. ELIGIBILE"* o *"PARTICELLA IN SUPERO TRA
  PIU' ATTI..."*): per queste righe il fascicolo stesso **non riporta** la
  superficie coltivata (non è un limite del parser: il dato non è nel PDF). Il
  tool lo segnala con un avviso dedicato e scarta la riga invece di stimarla —
  va controllata a mano. Osservato su 30 fascicoli reali: ~80 righe su 2200+
  estratte (~3.5%), concentrate in pochi fascicoli (es. OCCHIONERO MARIELLA
  2025, particelle con vigneto).
- **Comune multi-riga**: gestito il caso `SAN MARTINO IN\nPENSILIS`; comuni con
  formattazioni molto anomale potrebbero non essere catturati (non impatta il
  join, che usa solo foglio+particella).

## Validazione su dati reali

Testato sui 30 fascicoli reali (9 aziende, campagne 2023-2026) forniti in
`FASCICOLI AZIENDALI/` incrociati con `DD PIANO PARTICELLARE - SAN MARTINO IN
PENSILIS (4).xlsx`, che riporta già, per un sottoinsieme di particelle, la
coltura "attesa" per anno (colonne aggiunte a mano dallo studio). Spot-check
su 33 combinazioni (particella, anno) coperte da 13 particelle di 5 aziende
diverse: **28/33 corrispondenza esatta**, 3 entro 0.03 ha di scarto
(arrotondamento), 2 corrette ma visualizzate dopo una voce non agricola più
estesa nella stessa cella (solo questione di ordinamento, il valore c'è).
Su 102 righe del particellare con foglio/particella valorizzati, 52 sono
state incrociate con almeno un anno di coltura; 50 restano "non trovate"
(proprietari/conduttori di cui non è stato fornito il fascicolo, o
particelle non dichiarate da nessuno dei fascicoli caricati).

Durante questa validazione sono stati scoperti e corretti tre problemi reali
(non ipotetici) nei dati AGEA:
- **Dichiarazioni per la campagna successiva incluse nello stesso fascicolo**:
  un fascicolo "2025" può contenere, per la stessa particella, sia la coltura
  in corso (inizio novembre 2024) sia una già dichiarata per il 2026 (inizio
  novembre 2025) — anche con lo stesso codice-coltura. Sommarle avrebbe
  raddoppiato la superficie. Il tool tiene solo la dichiarazione corrente
  quando ne esiste una in concorrenza per la stessa particella nello stesso
  fascicolo (altrimenti tiene l'unica disponibile).
- **Riepilogo "macrouso" soppresso per errore**: la presenza di un oliveto
  (voce permanente, senza date) nella stessa particella non deve far scartare
  il riepilogo `666 = SEMINATIVI` quando è l'unico dato disponibile per la
  parte seminativa — solo un dettaglio-coltura della STESSA categoria
  (es. grano, girasole) lo sostituisce.
- **Conflitto tra più atti**: per le particelle con dichiarazioni sovrapposte
  tra più atti AGEA non stampa affatto la superficie coltivata (vedi sotto) —
  ora riconosciuto e segnalato esplicitamente invece di essere scartato come
  "riga non riconosciuta" generica.

## Nota sui file di esempio forniti (prime prove, 1 solo fascicolo)

I 3 fascicoli `DI ROSA ANGELO EMILIO` dichiarano la conduzione delle particelle
**64/51, 64/55, 64/56, 64/59, 64/62, 66/7, 14/23, 14/51** (Ururi per il foglio
14). Il particellare `DD PIANO PARTICELLARE - SAN MARTINO IN PENSILIS` riguarda i
fogli **63 / 64 / 66** con particelle diverse: **l'intersezione con questo
singolo fascicolo è vuota**, quindi caricando solo questi file tutte le righe
risultano *"particella non trovata"* — è corretto. Servono i fascicoli degli
altri conduttori (DI ROSA Peppino, OCCHIONERO, PELLEGRINO, FRATE, BRUNETTI…) per
popolare il particellare di progetto. `demo_particellare.xlsx` serve solo a
mostrare il join funzionante sulle particelle effettivamente presenti nel
fascicolo di esempio.

## App desktop (Windows / Mac, senza hosting)

`desktop.py` avvia lo stesso server Streamlit in un processo locale e lo mostra
in una **finestra nativa** (via [pywebview](https://pywebview.flowrl.com/),
WebKit su macOS / WebView2 su Windows) invece che nel browser — nessun server
remoto, nessun canone.

```bash
pip install -r requirements.txt   # include pywebview + pyinstaller
python desktop.py                 # prova la finestra nativa da sorgente
python build_app.py               # genera l'eseguibile in dist/
```

**Attenzione**: PyInstaller non fa cross-compiling — `build_app.py` va
eseguito **su macOS** per ottenere `.app` e **su Windows** per ottenere
`.exe`. Per farlo senza possedere un PC Windows, il workflow
[`.github/workflows/build-desktop.yml`](.github/workflows/build-desktop.yml)
compila entrambi sui runner gratuiti di GitHub Actions (Actions → *build-desktop*
→ *Run workflow*, oppure un tag `v*`): produce due artifact scaricabili, zero
hosting, la CI serve solo a generare il file. Verificato in locale: l'app
`.app` (~200 MB, include l'intero runtime Python) si apre, il server risponde
e il processo si chiude in modo pulito.

Distribuzione: basta copiare la cartella `dist/EstrattoreColtureAGEA(.app|/)`
sul PC di destinazione — non serve Python installato. Al primo avvio macOS
mostra l'avviso "sviluppatore non identificato" (tasto destro → Apri la prima
volta) e Windows SmartScreen un avviso simile ("Ulteriori informazioni" →
"Esegui comunque"): per un tool interno tra colleghi è la via più rapida;
firmare il pacchetto (Apple Developer $99/anno, certificato di code-signing
Windows) evita l'avviso ma non è necessario per un POC.

## Struttura

| file | ruolo |
|---|---|
| `app.py` | UI Streamlit |
| `agea_parser.py` | estrazione PDF + aggregazione per particella/anno |
| `matching.py` | lettura Excel particellare + join + export |
| `storage.py` | cache SQLite dei parsing (chiave sha256 del PDF) |
| `test_e2e.py` | smoke test sui documenti reali |
| `desktop.py` | wrapper finestra nativa (pywebview) attorno al server Streamlit |
| `build_app.py` | genera l'eseguibile desktop con PyInstaller |
