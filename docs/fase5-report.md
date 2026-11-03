# Fase 5 — robustezza

Piano approvato da Tonysa87 il 2/11/2026 (ordine: conversione deterministica → errori di rete →
asset enormi → albedo in JPEG).

## 1. Conversione deterministica ✅

**Problema** (limite noto della fase 4): riconvertendo un asset (per esempio dopo aver svuotato la
cache di Blender) il .blend aveva un hash diverso, quindi un altro Blender con la copia vecchia lo
vedeva "da aggiornare" e ogni riconversione costava un reload.

**Cause trovate** confrontando due conversioni dello stesso asset blocco per blocco (dopo la
decompressione zstd) e leggendo i nomi dei campi dalla DNA del file:

| campo | perché cambia | sorgente Blender 5.2 |
|---|---|---|
| `ID.session_uid` | contatore della sessione | azzerato da Blender quando legge il file (`readfile.cc`) |
| `bNode.identifier` | casuale, seme preso dall'orologio | `node_unique_id()` in `node.cc` |
| `Mesh.face_sets_color_seed` | hash del secondo corrente | `mesh.cc` (serve solo ai colori dei face set in sculpt) |
| `ScrEdge.v1/v2` | vertici di un bordo della UI ordinati per indirizzo di memoria | `BKE_screen_sort_scrvert` |

Tutto il resto (geometria, texture compresse, anteprima, metadati, puntatori scritti) era già
identico. Anche la compressione zstd di Blender è multithread e cambiava di qualche byte.

**Soluzione** (`simready/convert/blend_normalize.py`, chiamato da `convert_asset.save_reproducible`):
il convertitore salva il .blend non compresso, poi

1. mette a 0 il `session_uid` di ogni ID ("non assegnato", come fa Blender in lettura);
2. numera i nodi 1..n nell'ordine della lista, albero per albero (unici per albero, come richiesto);
   solo per alberi di nodi shader/frame/reroute/group input-output e senza `nested_node_refs`
   (nodi che potrebbero riferirsi ad altri per identificatore restano come sono);
3. mette a 0 il seme dei face set;
4. ordina i due vertici dei bordi della UI per valore scritto;
5. comprime nel formato zstd "seekable" di Blender (frame da 1 MiB, livello 3, tabella in coda),
   in modo deterministico. `zstandard` c'è nel Python di Blender su Linux e su Windows.

Gli offset dei campi si leggono dalla DNA del file stesso: con un'altra versione di Blender un
campo mancante viene saltato. Se qualcosa va storto il convertitore torna al salvataggio normale di
Blender (con un avviso nel report). `CONVERTER_VERSION` non cambia: il contenuto è lo stesso, quindi
le copie già in cache restano valide.

**Verifiche**

- 10 asset scelti a caso (5 Collection con 6-9 mesh, 5 Object, 10-116 MB): convertiti dal server di
  sviluppo, poi `state.json` e .blend cancellati, server riavviato, riconvertiti: **10 su 10 con lo
  stesso sha256**.
- Contenuto identico al salvataggio normale di Blender (banana, mobiletto, occhiali): stessi oggetti,
  matrici, attributi delle mesh, nodi, link, valori, immagini impacchettate, metadati e anteprima.
- Dimensione uguale (±0,3%); il salvataggio costa 0,04-0,2 s.
- Test: `tests/test_blend_normalize.py` (stesso file da due esecuzioni di Blender, file riletto
  correttamente, tabella seekable come la legge `filereader_zstd.cc`).
- Nota: gli operatori BMesh (es. `primitive_uv_sphere_add`) non sono riproducibili nell'ordine dei
  corner; l'import USD costruisce le mesh direttamente e lo è.

## 2. Errori di rete ✅

- **Ripresa dei download** (`hf.py`): se la connessione cade, il file riprende dal byte a cui si era
  fermato (richiesta `Range`, verificata sul CDN di HF: risponde 206 con `Content-Range`). Se il
  server ignora il Range si riparte da zero e l'avanzamento viene corretto. Si rinuncia dopo 4
  tentativi di fila senza byte nuovi. Nota: `http.client` non solleva `IncompleteRead` quando la
  connessione si chiude prima di `Content-Length`, restituisce `b""`: anche quello conta come caduta.
- **Tipo di errore** su ogni `HFError`: `offline`, `rate_limit`, `server`, `auth`, `not_found`,
  `http`, `integrity`, `cancelled`. Senza DNS o con connessione rifiutata il client rinuncia in
  pochi secondi invece di aspettare un minuto.
- **Messaggi per l'utente** (`jobs.describe`): l'evento `error` ora ha `kind`, `message` (frase breve
  in inglese, mostrata dall'add-on nella barra di stato per 60 s e nelle preferenze per un'ora) e
  `detail` (testo tecnico). Errori del convertitore: disco pieno, memoria esaurita (exit -9 /
  `0xC0000017`), timeout, altrimenti l'ultima riga d'errore del log di Blender.
- **Rate limit visibile**: durante l'attesa il job mostra "Hugging Face rate limit, waiting N s".
- **Spazio su disco**: prima del download e prima della conversione il server controlla lo spazio
  libero (file mancanti + 2 × dimensione dell'asset + 256 MB) e, se non basta, lo dice con i numeri.
- **Pulizia all'avvio**: `.part`, `.tmp.blend`, `.raw`, .blend non più registrati in `state.json`;
  i download di job falliti restano 7 giorni (un nuovo tentativo li riusa, senza riscaricarli).
- **Senza internet** (provato con un proxy inesistente): la libreria resta completa dall'indice
  salvato, l'add-on dice che il controllo degli aggiornamenti non è riuscito, e chiedere un asset
  nuovo dà in 3 s "Hugging Face is unreachable: check the internet connection and try again".
- Test: `tests/test_hf.py` (finto HF: cadute, Range ignorato, 503, 429, checksum sbagliato, 404,
  rete assente, annullamento; spazio su disco, messaggi del convertitore, pulizia).

### 1b. Riproducibilità: la numerazione dei blocchi (3/11/2026) ✅

Convertendo tre volte `structure_wall_entrance_a01` a 2K, una conversione su tre aveva un hash
diverso. Il contenuto era identico; cambiavano solo gli "indirizzi" (`old`) di alcuni blocchi di
dati delle mesh e i puntatori verso di loro.

**Causa** (`writefile.cc`, Blender 5.2): ogni ID numera i suoi blocchi a partire da un hash del
proprio nome, ma la mappa indirizzo di memoria → numero è unica per tutto il file e non viene
svuotata tra un ID e l'altro. Se un buffer temporaneo scritto per una mesh riusa la memoria di un
buffer già scritto per una mesh precedente, prende il numero di quella e tutti i numeri successivi
della mesh slittano. Dipende dall'allocatore di memoria: capita ogni tanto, più facilmente sui file
grandi. Anche i puntatori `sharing_info` (scritti come numeri senza blocco, poi ignorati e ricreati
in lettura) vengono dalla stessa sequenza.

**Soluzione** (`blend_normalize.renumber_data`, passo 5): il lettore di Blender risolve i puntatori
ai dati solo all'interno dello stesso ID (`read_data_into_datamap` + `oldnewmap_clear`), quindi
ogni ID con i suoi blocchi DATA è una "sezione" a sé. In ogni sezione i blocchi ricevono numeri
nuovi che dipendono solo dal numero dell'ID (stabile, viene dal nome) e dalla loro posizione;
ogni parola a 64 bit allineata della sezione uguale a un vecchio numero viene riscritta (campi
puntatore delle struct e array di puntatori; un falso positivo richiederebbe una coincidenza su
60 bit). I `sharing_info` diventano numeri deterministici. I numeri nuovi sono unici in tutto il
file. Unico effetto collaterale: dati davvero condivisi tra due ID (implicit sharing) dopo la
lettura non sono più condivisi in memoria; negli asset convertiti non succede (ogni mesh ha i
suoi array).

**Verifiche**: le due conversioni diverse, normalizzate, sono identiche byte per byte; il file
rinumerato riletto da Blender dà lo stesso contenuto dell'originale (mesh e attributi con hash
dei valori, oggetti, matrici, materiali, nodi, link, immagini impacchettate, metadati e anteprima)
e nessun errore di "corruption". 4 conversioni a 2K e 2 con JPEG dell'asset grande: stesso hash.
Test nuovo: `test_shifted_data_addresses` (sposta i numeri di un ID e controlla che dopo
`normalize()` i byte coincidano e che il file si legga). Costo: ~0,3 s su un file di 300 MB.

## 3. Asset enormi ✅

- Dimensioni dei download (indice): mediana 35 MB, 90° percentile 260 MB, 99° 753 MB; 86 asset
  sopra 500 MB, 8 sopra 1 GB (il più grande `equipment_robot_trolley_a01`, 1,77 GB, 42 file).
- Misure (WSL, 32 core, macchina scarica; download ~5-10 MB/s dalla rete di casa):

| asset | download | texture | conversione | picco RAM | .blend |
|---|---|---|---|---|---|
| `equipment_robot_trolley_a01` a 2K | 1,77 GB, ~170 s | nessuna | 18 s | 2,0 GB | 96 MB |
| `structure_wall_entrance_a01` a Max | 1,11 GB, ~360 s | 45 (1,06 GB di PNG, molte 4K) | 12 s | 4,4 GB | 875 MB |
| idem a 2K | | | 29 s | 1,7 GB | 252 MB |
| idem a Max + JPEG | | 14 albedo in JPEG | 15 s | 3,9 GB | 629 MB (−28%) |
| idem a 2K + JPEG | | | 28 s | 1,5 GB | 194 MB (−23%) |

- A Max Blender tiene in memoria tutte le texture decompresse (45 × 4K ≈ 3 GB): per i pochi asset
  così grandi servono 4-5 GB di RAM liberi durante la conversione. A 2K il tempo va quasi tutto
  in `Image.scale` (~0,6 s per texture 4K, un thread).
- La prima misura a 2K era di 4 min 25 s solo perché la macchina era sovraccarica (un'altra misura
  in parallelo): a macchina scarica sono 29 s.
- **Spazio su disco**: la stima resta "file mancanti + 2 × dimensione dell'asset + 256 MB". Picco
  vero del muro a Max: download 1,11 GB + .blend non compresso (~0,9 GB) + .blend compresso
  0,875 GB ≈ 2,9 GB, stima 3,6 GB: corretta e prudente. Per gli asset di sola geometria (carrello:
  stima 5,6 GB, picco ~2 GB) è molto prudente, ma sbaglia solo dalla parte sicura e il messaggio
  dice i numeri: lasciata così.

## 4. Texture colore in JPEG ✅ (opzione, PNG resta il default)

Decisione presa da Claude (mandato di Tonysa87 del 3/11/2026), sui numeri qui sotto.

- Nel dataset le albedo sono ~64 GB su 221 GB di PNG (29%).
- Campione: 150 albedo prese a caso, una per asset (1,38 GB; `tools/measure_jpeg.py`, ora con
  `--jobs`; un thread numpy per processo, altrimenti 16 processi × 32 thread bloccano la macchina).
  149 opache, 1 con trasparenza vera.

| | dimensione rispetto al PNG | PSNR mediano | PSNR minimo | SSIM minimo |
|---|---|---|---|---|
| Max, q90 4:4:4 | 18% | 43,8 dB | 30,8 dB | 0,910 |
| Max, **q95 4:4:4** | **30%** | **45,9 dB** | 35,7 dB | 0,967 |
| 2K, q95 4:4:4 | 26% | 46,3 dB | 36,4 dB | 0,963 |
| 2K, q95 4:2:0 | 21% | 43,6 dB | 28,4 dB | 0,963 |

- Il caso peggiore (acciaio spazzolato, rumore fine) a 1:1 non si distingue dal PNG; la differenza
  ×10 è rumore uniforme. Render Cycles del muro a 2K, PNG contro JPEG: PSNR 59 dB, identici a occhio.
- 16 albedo su 149 sono quasi a tinta unita: lì il PNG è più piccolo del JPEG.
- **Scelte**: q95, 4:4:4 (il 4:2:0 risparmia poco e peggiora i bordi di colore); solo texture sRGB
  (albedo, emissione) **senza trasparenza vera**; JPEG solo se è al massimo l'80% del PNG; normal,
  ORM, opacità restano sempre PNG. JPEG scritto con OpenImageIO dai soli pixel (niente metadati),
  quindi riproducibile; il percorso del file impacchettato è quello del PNG con `.jpg`.
- **Opzione "JPEG Color Textures"** nelle preferenze dell'add-on (server: `--color-jpeg`),
  **disattivata di default**: chi la attiva ha .blend del 23-28% più piccoli (anche i file di scena
  con gli asset impacchettati). Ha la sua chiave di stato (`<asset>@2048-jpeg`), la sua cartella
  (`blend/res2048-jpeg/`) e i suoi hash segnaposto; con PNG chiavi e hash restano quelli di prima,
  quindi le conversioni già fatte restano valide. Cambiarla segna gli asset scaricati come
  aggiornabili, come la risoluzione.
- Test: `tests/test_color_jpeg.py` (rumore → JPEG, tinta unita → PNG, trasparenza → PNG, alfa
  tutto a 255 → JPEG, riduzione, stesso risultato due volte) e `test_jpeg_profile` nel server.
