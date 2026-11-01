# Fase 3 — Report: server completo

Data: 1/11/2026 · Blender 5.2.2 LTS · dataset `nvidia/simready-assets` revisione `4763fbf…` (29/10/2026)

## Esito in breve

Il server pubblica **tutti i 2.677 asset** come libreria remota di Blender, con 254 cataloghi e
anteprime WebP. Converte ogni asset alla prima richiesta e serve anche conversioni più lunghe
di 300 s, grazie al keep-alive gzip. Il listing passa la validazione del parser di Blender.
Il ciclo completo è stato provato con Blender in WSL (anche con GUI) e con curl.
**Verificato sul Blender Windows di Tonysa87** la sera dell'1/11 (sezione 6).

## 1. Architettura (`simready/server/`)

| Modulo | Cosa fa |
|---|---|
| `hf.py` | client Hugging Face (solo stdlib): richieste fissate a una revisione, ritmo dall'header `RateLimit`, backoff su 429/5xx, download verificati (dimensione + sha256 del BOM) e atomici; il token non va mai al CDN |
| `index.py` | indice per asset: file da scaricare, anteprima, impronta del contenuto, tipo/nome previsti. Incrementale: si riscaricano solo le cartelle con un `oid` diverso |
| `structure.py` | previsione di tipo (Object/Collection) e nome NVIDIA da `base.usda` (+ `instances.usda` se piccolo) |
| `catalogs.py` + `catalogs.json` | prefisso → gruppo (tabella modificabile), UUID `uuid5` stabili |
| `thumbs.py` | anteprime NVIDIA 1024² → WebP 256² con OpenImageIO, on demand o tutte in background |
| `jobs.py` | conversioni: download filtrato → `blender -b` → .blend con nome legato all'hash; una sola conversione per asset anche con richieste concorrenti |
| `listing.py` | meta → indice → pagine da 500 asset; hash segnaposto = contenuto + risoluzione + versione del convertitore |
| `app.py` | HTTP su 127.0.0.1, ETag/304, `/_status` per l'add-on, keep-alive gzip |
| `__main__.py` | avvio, log, watchdog sul PID di Blender, arresto dei convertitori all'uscita |

Gira con il **Python incluso in Blender**: ha già pxr, OpenImageIO, numpy e certifi, anche nel
`python.exe` di Windows (verificato sull'installazione di Tonysa87). Nessuna dipendenza esterna.

```bash
PY=~/blender/blender-5.2.2-linux-x64/5.2/python/bin/python3.13
$PY -s -E -m simready.server --state-dir <cartella> --port 8765 --max-res 2048 \
    --blender ~/blender/blender-5.2.2-linux-x64/blender [--seed-index index.json.gz] [--prefetch-thumbs]
```

Cartella dati (un'unica cartella, sarà scelta nelle preferenze dell'add-on):
`index/`, `thumbs/`, `raw/` (svuotata dopo ogni conversione), `blend/res<N>/`, `state.json`, `logs/`.

## 2. Indice da Hugging Face

- Per asset servono il BOM, `base.usda` e `instances.usda` (se < 2 MB): **in media 2,96 richieste,
  circa 60 KB**. La radice USD si ricava dal BOM (unico `.usd(a)` in `simready_usd/`, vero per
  tutti i 2.677). `root_usds.json` si scarica solo nei casi ambigui.
- Primo indice da zero: circa 8.000 richieste, limitato da HF a ~3.000 ogni 5 minuti, quindi
  **~13 minuti**. Durante la costruzione il listing si riempie a blocchi di 200.
  Con un **indice di partenza** (`--seed-index`, 2,6 MB compresso) la libreria è completa
  subito; poi il controllo su HF costa 4 richieste.
- Controllo di una nuova revisione all'avvio e ogni 24 ore (`--check-hours`).
- File scaricati per la conversione: tutto `simready_usd/` tranne `source_assets/`, `.thumbs/`,
  `.simready/`, più la LICENSE (= il 51% del dataset, mediana 35 MB per asset).

## 3. Previsione di tipo e nome (decisione di Tonysa87: Object se un pezzo, Collection se più pezzi)

Il listing deve dichiarare tipo e nome **prima** della conversione. `structure.py` compone con pxr
i layer piccoli (geometria sostituita da segnaposto, fisica disattivata, istanze rimosse come nel
convertitore) e applica le regole di import di Blender 5.2 (purpose, visibilità, prim attivi).

- **Validazione su 236 asset convertiti davvero** (un asset per ognuno dei 196 prefissi + casi
  strani): **235 su 236 esatti** per tipo, nome e numero di mesh. L'errore è in uno degli 8 asset
  senza `base.usda`, che ricevono un valore di ripiego a bassa confidenza.
- Su tutto il dataset: 1.661 Object e 1.016 Collection; 2.582 previsioni ad alta confidenza.
- Se la previsione è sbagliata: dopo la conversione il server corregge tipo, nome e hash nel
  listing. Al reload di Blender la voce si aggiorna e non restano doppioni. Verificato in GUI.
  Correggere solo l'hash invece lascia una voce fantasma: per questo il server corregge tutto.
- Script: `tools/phase3/validate_structure.py`.

## 4. Conversioni oltre i 300 s: keep-alive gzip (domanda aperta 2 → risolta)

Blender interrompe un download dopo 300 s **senza byte**. Accetta però risposte
`Content-Encoding: gzip` e le decomprime da solo. Se il .blend non è pronto entro 4 s, il server:
1. risponde subito `200` con gzip e transfer-encoding chunked;
2. ogni 15 s manda un blocco deflate vuoto (circa 8 KB; il pulsante Cancel di Blender si
   controlla ogni 8 KB ricevuti);
3. quando il file è pronto, lo manda compresso e chiude il flusso gzip.

Se la conversione fallisce, la connessione viene interrotta prima della chiusura del flusso:
Blender segna il download come fallito, **non tiene file rotti**, e l'asset resta scaricabile.

Verificato con il downloader di Blender (headless) e in GUI: conversione finta di **330 s**,
16 keep-alive, file identico (sha256), append riuscito, hash del listing coincidente dopo il reload.
Script: `tools/phase3/longconv_server.py`, `run_headless.py`, `gui_driver3.py`.

Note emerse:
- Blender scarica **un file alla volta per libreria**: un asset enorme blocca la coda.
- Durante l'attesa la barra di avanzamento resta ferma.
- Il reload del listing funziona anche durante un download lungo.

## 5. Robustezza e sicurezza

Revisione avversariale (agente separato): 10 problemi trovati, tutti corretti. I principali:
- un asset malformato faceva fallire l'intero indice;
- il listing parziale veniva pubblicato una volta sola;
- su Windows un `.blend` veniva sostituito mentre era in invio (ora ogni versione ha un nome per hash);
- i convertitori restavano orfani all'uscita del server;
- c'era una proprietà personalizzata in più negli asset, contraria alla regola "solo dati NVIDIA".

Protezioni: solo `127.0.0.1`; `Host` deve essere locale (contro il DNS rebinding); `/blend/` richiede
l'header `X-Blender` (un sito web non può avviare conversioni); percorsi e nomi dal dataset validati;
le value clip USD vengono rimosse prima della composizione.

Test: `python3 -m unittest discover -s tests`, 43 test (con il Python di Blender anche quelli pxr).

## 6. Da verificare sul Blender Windows di Tonysa87

Il server di prova sulla porta **8765** ora è quello nuovo (cartella `<cartella dati>\simready_test\phase3`).
Nessuna modifica alla libreria in Blender: stesso URL.

1. Asset Browser → libreria SimReady → **Shift+clic su Refresh**: devono comparire 2.677 asset
   nei cataloghi `SimReady/<gruppo>/…`, con anteprime che arrivano mentre si scorre (la prima
   volta circa 1 s ciascuna).
2. Scaricare un asset qualsiasi mai provato (prima conversione: da qualche secondo a qualche minuto),
   trascinarlo nella scena, poi Shift+Refresh: nessun avviso.
3. I 6 asset della fase 2 risultano "da aggiornare" (convertitore nuovo): è previsto.

Esito (1/11, sera): tutto OK. Motore `engineAssembly_v6_engine_a01`: 829 MB da HF, conversione 125 s,
334 MB consegnati; materiali corretti, dimensioni al millimetro (pezzo `engine_01_mesh_00`
0,984 × 1,086 × 0,854 m). Shift+Refresh: solo le pagine cambiate riscaricate, nessun avviso.
Da migliorare: la barra resta a 0% durante la conversione e la dimensione stimata è quella dei file
originali (il .blend finale è più piccolo, la barra si ferma presto e salta a "completato").

## 7. Domande aperte per Tonysa87

1. **Indice di partenza**: incluso nel pacchetto dell'add-on (non in git, dati NVIDIA con attribuzione)?
2. **Copia dei .blend nel server**: ogni asset occupa spazio due volte (cartella nostra + cache di
   Blender). Opzione "tieni una copia": accesa (più veloce) o spenta (metà spazio)?
3. Porta 8765 fissa, pulizia della vecchia cache `6f0c46218f1e3d46`, albedo in JPEG: ancora aperte.
4. Gruppi dei cataloghi (`simready/server/catalogs.json`): da rivedere a occhio nell'Asset Browser.

## 8. Prossimo passo: fase 4 (add-on)

Avvio/arresto del server con il Python di Blender, registrazione della libreria (una sola volta,
URL canonico), preferenze (cartella dati, risoluzione, porta, anteprime in background, import Pack/Append),
timer su `/_status` → reload del listing dopo ogni conversione.
