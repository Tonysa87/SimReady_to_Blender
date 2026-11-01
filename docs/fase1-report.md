# Fase 1 — Report: prova del meccanismo e ricognizione del dataset

Data: 1/11/2026 · Blender 5.2.2 LTS (Linux portable in WSL, GUI via WSLg/X11 + llvmpipe)

## Esito in breve

**Strada A confermata.** Il ciclo "hash segnaposto → conversione on-demand → hash reale →
reload del listing" funziona su Blender 5.2.2 senza modifiche. Non serve la strada B.

## 1. Meccanismo remote library: cosa è stato provato

Prototipo: `tools/phase1/proto_server.py` (solo stdlib) con 2 asset finti; il .blend
viene generato alla prima richiesta da un Blender headless (`tools/phase1/fake_convert.py`).
Test automatico: `tools/phase1/gui_driver.py` pilota un Blender con interfaccia.

| Scenario | Risultato |
|---|---|
| Listing con hash segnaposto, validato con il parser di Blender (`ValidatingParser` + modelli OpenAPI) | OK |
| Asset Browser mostra subito tutti gli asset come *online*, anteprime scaricate | OK |
| Download (`asset.asset_download`, lo stesso del pulsante sulla tile) di un asset non convertito | OK: conversione 3,5 s dentro la stessa richiesta HTTP, file salvato |
| Stato subito dopo il download (hash del listing ≠ file) | considerato `MATCH` (Blender non verifica dopo il download) |
| `/_status` → `asset.library_reload_listing` | OK: nuovo listing con hash reale = file su disco |
| Riavvio di Blender (verifica vera hash+dimensione) | `MATCH`, nessun download richiesto |
| Controprova: server che non aggiorna l'hash | dopo reload/riavvio `NO_MATCH` (asset utilizzabile ma segnato da riscaricare) |
| Riconversione (nuova revisione HF): nuovo hash segnaposto | `NO_MATCH` → riscaricato → `MATCH` dopo il reload |
| Cache HTTP: `ETag`/`If-None-Match` sul listing | Blender invia `If-None-Match`, il server risponde `304` |
| Append della Collection dal .blend scaricato | OK |

## 2. Correzioni e aggiunte al brief (§3)

1. **Creare la libreria remota**: `asset_libraries.new()` crea una libreria **locale**;
   `use_remote_url` è in sola lettura. Impostare `remote_url` dopo scarica il listing
   ma l'Asset Browser resta vuoto. Il modo corretto è
   `bpy.ops.preferences.asset_library_add(type='REMOTE', name=..., remote_url=...)`.
2. **Cartella locale**: per le librerie remote Blender sceglie da solo il percorso
   (`<cachedir>/remote-assets/<hash dell'URL>`); `path` viene sovrascritto. Cambiare porta
   significa cambiare URL e quindi cartella (e riscaricare tutto). **La porta va tenuta fissa.**
3. **Drag & drop di un asset non scaricato è bloccato** da Blender ("Asset needs downloading
   first", `wm_dragdrop.cc`), come per le Online Essentials. Prima si scarica (pulsante sulla
   tile / menu contestuale / `asset.assets_download`), poi si trascina.
4. Dopo il download Blender imposta `MATCH` senza controllare (`online_asset_mark_downloaded`,
   con un TODO nel codice). La verifica vera arriva al refresh o al riavvio. Per questo il
   reload del listing con l'hash reale **è obbligatorio**, altrimenti al riavvio l'asset
   risulta da riscaricare.
5. `library_reload_listing` richiede come contesto un Asset Browser che mostri la libreria
   (o la libreria attiva del workspace). Alternativa senza contesto per l'add-on: chiamare
   `bl_pkg.remote_asset_library_sync(url, path)`, la stessa funzione che C++ usa per la
   sincronizzazione (è API interna: da usare con un fallback).
6. Il flag sperimentale `preferences.experimental.use_remote_asset_libraries` è già `True`
   di default in 5.2.2. L'add-on deve comunque controllarlo.
7. All'avvio Blender risincronizza da solo i listing più vecchi di un certo periodo
   (`_remote_asset_library_sync_all_periodic`).
8. **L'URL deve contenere `http://`**: con `127.0.0.1:8765/` la sincronizzazione Python salta
   la libreria ("can only handle http://, https://") e l'Asset Browser mostra solo
   "Could not read asset listing". L'add-on deve registrare l'URL completo.
9. **La cartella di cache dipende dal testo esatto dell'URL, preso prima della normalizzazione**
   (`BKE_preferences_remote_asset_library_url_set`): `http://127.0.0.1:8765` e
   `http://127.0.0.1:8765/` vengono salvati entrambi come `…/`, ma finiscono in cartelle
   diverse (`573c4e13…` e `ef7615a0…`). Ogni modifica dell'URL o ricreazione della libreria
   "dimentica" i download fatti. Durante il test su Windows sono nate così 5 cartelle per lo
   stesso server. L'add-on deve registrare la libreria una volta sola, con l'URL canonico
   `http://127.0.0.1:<porta>/`, e non modificarlo mai.
10. **Bug di Blender 5.2.2 (riprodotto su Linux, `tools/phase1/probe_lifecycle.py`)**:
   `AssetLibraryService` tiene in memoria per tutta la sessione le librerie remote, indicizzate per URL
   (`get_preferences_remote_asset_library`). Se nella stessa sessione si **rimuove e si aggiunge di
   nuovo** la libreria, o se ne **modifica l'URL**, la vista "All Libraries" e il pulsante Download
   (che cerca l'asset nella lista globale "All") usano la copia vecchia:
   - dopo rimozione e nuova aggiunta: "All" vuota, `No asset found at path ""`;
   - dopo la modifica dell'URL: "All" mostra lo stato della cartella precedente,
     "Asset … doesn't need downloading", e la vista della libreria mostra il globo.
   Il refresh non basta; **riavviare Blender risolve**. Per l'add-on: registrare la libreria una
   volta sola, non toccarla mai durante la sessione e, se l'utente la rimuove, chiedere un riavvio.
11. **Due librerie con lo stesso URL non possono coesistere** (verificato sul Blender Windows di Tonysa87):
    il downloader Python (`asset_downloader.py`, `_asset_downloaders[url]`) tiene un solo
    downloader per URL e verifica che la cartella locale non cambi. Se due librerie hanno lo stesso
    URL (normalizzato) ma cartelle diverse, ad esempio per uno spazio nel testo, i download
    falliscono con `AssertionError: This code assumes that remote asset libraries do not move on
    the local disk`. Resta così fino al riavvio. L'add-on deve garantire una sola libreria per URL.
12. **Metodo di import al trascinamento** (`ED_fileselect_asset_import_method_get`): comanda
    il menu "Import Settings" dell'Asset Browser. Solo se è su "Follow Preferences" si usano il
    metodo preferito dell'asset (metadati) o, in mancanza, quello della libreria. Le texture
    restano comunque impacchettate nel file di scena, perché il .blend remoto deve essere
    autosufficiente; si estraggono con File → External Data → Unpack Resources.
13. **Da verificare (fase 4)**: sul Blender di Tonysa87, con libreria su Append e Asset Browser su
    "Follow Preferences", l'armadietto risulta importato come Pack. Il codice letto
    (`get_import_method` → `PreferencesRemoteAssetLibrary::import_method`) farebbe prevedere Append.
    Procedura consigliata per gli asset composti già importati come istanza:
    Object → Apply → "Instance Empty to Collection" (oppure "Make Instances Real").
14. Gli URL dei .blend (`?hash=...`) cambiano a ogni nuovo hash, quindi niente cache vecchie.
   Il meta viene richiesto con `?s=<timestamp/60>`.

## 3. Rischi e note per le fasi successive

- **Ordine degli eventi**: il reload va fatto solo dopo che la conversione è finita (evento
  `converted` in `/_status`). Un reload durante la conversione legge un listing ancora vecchio.
- **Dimensione stimata ≠ reale**: serve solo per la barra di avanzamento, nessun problema.
- **Timeout di 300 s**: è il *read timeout* di `requests`, cioè il tempo massimo senza
  ricevere byte (`_bpy_internal/http/downloader.py`). Il server deve quindi mandare il primo
  byte entro 300 s, e un .blend non si può trasmettere prima che la conversione finisca.
  Per gli asset grandi serve una strategia "conversione che continua in background + 503 con
  messaggio chiaro + nuovo tentativo", oppure una pre-conversione mirata.
- Gli screenshot automatici in WSLg non funzionano (immagine nera / grab rifiutato).
  La verifica visiva va fatta sul Blender Windows.

## 4. Il dataset (ricognizione su tutti i metadati + 6 asset scaricati)

Revisione HF `4763fbfaa4f8e97658e68b57d01b4c86659e08b5` (29/10/2026). Script in `tools/recon/`,
dati in `cache/index/` e `cache/raw/`.

- **2.677 asset** (non ~2.576), più solo `README.md` e `.gitattributes`. 222 prefissi
  (food 538, kitchenware 179, box 154, handTool 97, …). Tagliando al primo `_` i prefissi
  camelCase restano interi.
- **Due generazioni di layout**: B (2.541, radice `.usd` binaria, physics in `payloads/`) e
  A (136, radice `.usda`, physics in `runnables/physics/`). Il percorso della radice va **sempre**
  letto da `root_usds.json`: in 2 casi le maiuscole sono diverse dal nome della cartella.
- **Il BOM elenca dimensione e sha256 di ogni file di contenuto** (sha256 = oid LFS): basta
  per la dimensione stimata, l'impronta per l'hash segnaposto e la verifica dei download.
- **Peso utile** (radice + `payloads/**` + texture + LICENSE): 252 GB su 490 (51,5%).
  Mediana 35,6 MB per asset, p90 260 MB, massimo 1,77 GB (`equipment_robot_trolley_a01`).
- **Anteprime**: `simready_usd/.thumbs/256x256/<radice>.png` presente per 2677/2677, ma in
  realtà sono **PNG 1024×1024**: vanno ridotte.
- **Rate limit HF**: circa 3.000 richieste ogni 300 s (bucket "resolvers"). Servono backoff e
  cache per file.
- Varie: in 8 asset la cartella si chiama `textures` minuscolo; 28 texture sono `.jpg`;
  6 asset non hanno texture; i nomi delle texture non sono uniformi
  (`_alb/_Albedo/_albedo…`) → leggere i percorsi dall'USD, non dai pattern.
- Dataset card: solo `license: cc-by-4.0`. Il LICENSE di ogni asset è il testo CC-BY-4.0
  standard (§2b: marchi esclusi).

### Materiali e unità

- Tutti i materiali visivi hanno una rete **`UsdPreviewSurface`** accanto all'MDL OmniPBR
  (45 asset sono solo preview). **L'import nativo di Blender 5.2 la converte già bene**
  (verificato anche da me sulla banana): albedo sRGB → Base Color; ORM Non-Color → Separate
  Color (G → Roughness, B → Metallic); normal → Normal Map; tiling con Mapping.
- ORM confermato dalle statistiche dei canali: R = AO, G = roughness, B = metallic (metalli B≈1).
- `metersPerUnit = 1`, `upAxis = Z`: le dimensioni importate coincidono al millimetro.
- Le parti sono prim `instanceable` → in Blender diventano istanze di collection (prototipi
  in una collection nascosta). Con `support_scene_instancing=False` si ottengono mesh vere.
- Varianti di fisica `PhysX/MuJoCo/Newton`: nel layout B **PhysX è attivo di default**.
  L'importer non permette di scegliere le varianti → vanno impostate prima con `pxr`.

### Dove il preview non basta (lavoro della fase 2)

- `diffuse_tint` esiste solo nell'MDL: il nastro trasportatore "paintedBlue" diventa grigio.
- Vetro (OmniGlass): il preview è opaco.
- Rack: nell'MDL l'influenza della roughness è 0, il preview usa comunque l'ORM.
- Normal map: OpenGL o DirectX ancora da stabilire visivamente (l'MDL ha `flip_tangent_v=1`).
- Per la fase 2 si parte quindi dall'import nativo e si correggono i materiali leggendo i
  parametri OmniPBR/OmniGlass con `pxr`. Niente ricostruzione da zero.

## 5. Verifica visiva da fare sul Blender Windows (≈5 minuti)

Il server di prova è attivo in WSL su `127.0.0.1:8765` e Windows lo raggiunge. Nel Blender
5.2 di Windows:

1. Preferenze → System → *Allow Online Access* attivo.
2. Preferenze → File Paths → Asset Libraries → **+ → Add Remote Asset Library**, URL
   `http://127.0.0.1:8765/`.
3. Asset Browser → libreria "NVIDIA SimReady (phase-1 prototype)": devono comparire 2 asset
   con anteprima colorata (rossa/blu) e icona online, nei cataloghi Fake/Cubes e Fake/Monkeys.
4. Passa sopra la scimmia → pulsante di download: circa 3 s di attesa (conversione), poi
   trascinala nella vista 3D.
5. Ricarica il listing (Shift+clic su refresh) e controlla che l'icona di avviso non compaia.

(Se il server non risponde, va riavviato: `python3 tools/phase1/proto_server.py --state-dir <dir>`.)

## 5b. Esito della verifica su Windows (1/11/2026, ore 17:00–17:15)

Ciclo completo confermato sul Blender 5.2 di Tonysa87: listing → download con conversione al volo
(3,5–7 s) → drag & drop (Pack e Append) → Shift+refresh → hash reale = file → nessun avviso.
Anche l'aggiornamento (nuova versione → "outdated or inconsistent" → nuovo download → reload) funziona.

Requisiti emersi dal test, validi per `convert.py`:
- **I metadati dentro il .blend devono coincidere con quelli del listing** (catalogo, tag, autore,
  descrizione). Dopo il download Blender mostra l'asset con i metadati letti dal .blend: senza
  `catalog_id` l'asset finiva in "Unassigned" e spariva dal catalogo filtrato.
- **L'anteprima va incorporata nel .blend** (`id.preview_ensure()` + `image_pixels_float`): dopo il
  download Blender non usa più la thumbnail del server (`asset_representation.cc`).
- `AssetMetaData.catalog_simple_name` è in sola lettura.
- Tra il download e il reload Blender mostra ancora i metadati e l'anteprima vecchi: il reload
  automatico dell'add-on deve arrivare pochi secondi dopo ogni conversione.
- Un errore di conversione arriva a Blender come HTTP 502 e compare nel suo log come
  "Could not download file … 502".

## 6. Proposta per le fasi successive

- **Fase 2 – `convert.py`**: import nativo con varianti di fisica disattivate via `pxr` (layer
  di sessione), correzioni OmniPBR (tint, glass/opacity, influenze), verifica delle normal map,
  Collection marcata come asset, texture impacchettate, confronto con le anteprime NVIDIA.
- Decisioni prese:
  1. **Tipo di asset (deciso da Tonysa87 l'1/11/2026): misto.** Asset di un solo pezzo → asset
     **Object** (al trascinamento con Append arriva solo il modello, sotto il mouse). Asset
     composti → asset **Collection** con mesh vere dentro (niente istanze annidate). Al rilascio
     Blender crea un empty che istanzia la collection; togliendo "Instance" nel pannello
     "Add Collection" arrivano gli oggetti veri, spostati nel punto di rilascio
     (`OBJECT_OT_collection_external_asset_drop`).
  2. **Risoluzione delle texture (deciso da Tonysa87 l'1/11/2026):** menu a tendina nelle preferenze
     dell'add-on, valido per tutti gli asset: **Max (originale) / 2K / 1K**, con riduzione al
     momento della conversione. Il dataset arriva al massimo a 4K (campione: 1K–4K, alcune a 16 bit).
     Cambiare l'impostazione cambia gli hash, quindi gli asset già scaricati risultano "da aggiornare".
  3. **Modalità di import nell'add-on (deciso da Tonysa87 l'1/11/2026):** preferenza "Import degli asset
     SimReady" con due voci. "Pack" lascia il comportamento di Blender. "Append, modificabile": dopo
     ogni rilascio di un asset SimReady l'add-on esegue Make Instances Real + Make Local (All), solo
     sui dati che provengono dalla cache della libreria SimReady. Verificato a mano da Tonysa87: dopo i
     due passaggi l'armadietto è completamente modificabile. Opzione aggiuntiva da valutare:
     estrarre le texture in una cartella.
- Decisioni ancora da prendere:
  2. Asset sopra ~300 s di conversione (i più grandi, 1–1,8 GB): conversione in background con
     errore "riprova tra poco", oppure pre-conversione delle categorie scelte?
  3. Porta fissa: va bene la 8765?
