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

## 1. Asset enormi ⏳ (prime misure)

- Dimensioni dei download (indice): mediana 35 MB, 90° percentile 260 MB, 99° 753 MB; 86 asset
  sopra 500 MB, 8 sopra 1 GB (il più grande `equipment_robot_trolley_a01`, 1,77 GB, 42 file).
- `equipment_robot_trolley_a01` a 2K (WSL, 32 core, rete di casa): download ~170 s (~10 MB/s),
  conversione 18 s (import USD 5,9 s, salvataggio riproducibile 0,45 s), **picco di memoria di
  Blender 2,0 GB**, .blend di 96 MB. È quasi tutta geometria: nessuna texture.
  Il keep-alive gzip ha tenuto aperta la risposta per tutti i 190 s.
- Da fare: un asset grande *con molte texture* a Max e a 2K (memoria e dimensione del .blend),
  rivedere la stima dello spazio su disco (oggi: file mancanti + 2 × asset + 256 MB) con i numeri veri.

## 4. Albedo in JPEG ⏳

- Nel dataset le albedo sono ~65 GB su 221 GB di PNG (29%): il guadagno massimo possibile riguarda
  solo questa parte. Script pronto: `tools/phase5/measure_jpeg.py` (PNG vs JPEG q90/q95, 4:4:4 e
  4:2:0, PSNR e SSIM, albedo con trasparenza escluse). Prima prova su 48 albedo interrotta per
  tempo: è lento sulle 4K (da far girare in parallelo o su un campione).
