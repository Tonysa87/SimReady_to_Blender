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
