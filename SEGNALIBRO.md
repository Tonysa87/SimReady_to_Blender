# Segnalibro di progetto

> Ponte tra sessioni di Claude Code: si aggiorna con `/segnalibro` prima di `/clear` o a fine
> giornata. CLAUDE.md lo importa, quindi viene letto all'inizio di ogni sessione.
> Ultimo aggiornamento: **2/11/2026, mattina** (fase 5: fatti i punti 3 e 2, a metà i punti 1 e 4).

## Il progetto in breve

Rendere i ~2.677 asset NVIDIA SimReady (Hugging Face `nvidia/simready-assets`, CC-BY-4.0)
disponibili nell'**Asset Browser di Blender 5.2 LTS** come libreria remota. Il server locale
(127.0.0.1) pubblica un listing di tutti gli asset con un hash "segnaposto". Quando Blender chiede
un .blend, il server scarica l'asset da HF, lo converte (Blender headless) e lo consegna nella
stessa risposta; poi il listing viene ricaricato con l'hash reale. Un add-on avvierà il server e
gestirà la libreria. Brief completo: `docs/brief.md`.

Fasi: 1 meccanismo ✅ · 2 convertitore ✅ · 3 server completo ✅ (verificato su Windows) · 4 add-on ✅ (verificato su Windows) · **5 robustezza ⏳ (in corso: punti 3 e 2 fatti)** · 6 packaging.

## Stato attuale

- **Fase 3 (server)**: `simready/server/` pubblica tutti i 2.677 asset, 254 cataloghi, anteprime
  WebP; conversione on demand con keep-alive gzip (anche > 300 s). Report: `docs/fase3-report.md`.
- Gira col **Python incluso in Blender** (pxr, OpenImageIO, numpy, certifi; verificato anche il
  `python.exe` del Blender Windows di Tonysa87). Nessuna dipendenza esterna.
- La porta 8765 è del server avviato dall'add-on su Windows; in WSL per lo sviluppo si usa la 8790.
- Test: `python3 -m unittest discover -s tests` (69 test; quelli pxr/zstd/Blender girano col Python di Blender).
- **Fase 5** in corso, report: `docs/fase5-report.md`. Conversioni riproducibili (stesso hash) ed
  errori di rete gestiti sono su `main`, **non ancora in uno zip per Windows** (l'add-on resta 0.4.1).
- Repository **pubblico**: https://github.com/Tonysa87/SimReady_to_Blender (`main`), push autorizzato.
  Release **v0.4.1** con lo zip: https://github.com/Tonysa87/SimReady_to_Blender/releases/tag/v0.4.1.
  La vecchia storia (con dati personali) resta nel repo privato `SimReady_to_Blender_old_private`.

## Decisioni di Tonysa87

1/11/2026:
1. **Tipo di asset misto**: un solo pezzo → Object; più pezzi → Collection con mesh vere.
2. **Nomi NVIDIA**, nessuna proprietà aggiunta. Collection = nome della cartella, parti con i nomi
   NVIDIA delle mesh. **Object (aggiornato la sera dell'1/11): oggetto e mesh = nome dell'asset**
   (es. `food_banana_a01`), perché 215 asset avevano la mesh `body_01_mesh_00`; il nome NVIDIA
   della mesh resta nel report di conversione. `.001` solo dove NVIDIA ripete davvero un nome.
3. **Risoluzione texture**: menu nelle preferenze dell'add-on Max/2K/1K, per tutti gli asset.
4. **Progetto compatibile con OpenPBR**: descrizione intermedia con nomi OpenPBR → Principled BSDF.
5. **Modalità di import nell'add-on**: "Pack" oppure "Append, modificabile" (Make Instances Real +
   Make Local All, solo sui dati della cache SimReady).
6. Data Folder dell'add-on scelta da Tonysa87: `<cartella dati>\simready_cache\` (texture Max);
   le cose di sviluppo stanno in `<cartella dati>\simready_dev\dev_files\`.
7. Sessione serale: mandato a Claude di proseguire in autonomia (ricerche, fino a 3 agenti Opus).
8. Idee di Tonysa87: **indice di partenza** già pronto + controllo periodico delle novità (fatto:
   `--seed-index`, `--check-hours 24`); **cartella dati scelta nell'add-on** (il server ha già
   un'unica `--state-dir`).
9. **L'indice di partenza sta nel repo** (`simready/server/data/seed_index.json.gz`, 2,6 MB), usato
   di default dal server. Aggiornarlo quando cambia la revisione HF (`tools/phase3/make_seed_index.py`).
10. Pulizia della vecchia cache `6f0c46218f1e3d46`: autorizzata e fatta (era solo il prototipo di fase 1).
11. **Niente copia dei .blend nel server** (lo consegna e lo cancella, tiene solo hash/dimensione).
12. **La cache di Blender va nella cartella scelta dall'utente**: Blender non permette di spostarla
    (percorso fisso in `remote_library.cc`/`appdir.cc`), quindi l'add-on crea una junction
    `remote-assets\<hash URL>` → `<cartella dati>\blender-cache`.
13. Fase 4: Claude sviluppa e audita l'add-on in autonomia, senza approvazione del piano; Tonysa87 lo testa su Windows.
14. Tarda sera: chiuso il lavoro su FTC, **la prossima sessione prosegue con la fase 5**.

2/11/2026:
15. Piano della fase 5 approvato ("vai pure come ritieni opportuno"): ordine 3 → 2 → 1 → 4.

## Fatti tecnici da non dimenticare

- Libreria remota: si crea solo con `bpy.ops.preferences.asset_library_add(type='REMOTE', ...)`.
  Cartella di cache di Blender = md5 del testo dell'URL. **Mai modificare o ricreare la libreria
  durante una sessione** (serve un riavvio di Blender).
- Blender scarica **un file alla volta per libreria**, timeout 300 s *tra un byte e l'altro*,
  e accetta `Content-Encoding: gzip`. Keep-alive = blocco deflate vuoto `00 00 00 ff ff` (ripetuto
  fino a ~8 KB: Cancel viene controllato ogni 8 KB). Un `flush(Z_SYNC_FLUSH)` ripetuto non emette nulla.
- Se tipo/nome del listing non coincidono col .blend, il server deve correggere **tipo, nome e hash**
  (solo l'hash lascia una voce fantasma). Verificato in GUI.
- Dopo il download Blender considera l'asset verificato senza controllare; il controllo vero arriva
  al reload o al riavvio, per questo serve il reload con l'hash reale. Metadati e anteprima **dentro
  il .blend** devono coincidere col listing.
- Le anteprime nel listing hanno un hash usato solo come chiave di cache (`?hash=`), non verificato.
- La radice USD è l'unico `.usd(a)` in `simready_usd/` (vero per tutti i 2.677).
- Rate limit HF anonimo: bucket "api" 500/300 s, "resolvers" 3.000/300 s (header `RateLimit`).
- Il Python di Blender in WSL non trova i certificati di sistema: usare certifi. Avviare il server
  con `-s -E -m simready.server` dalla radice del repo (`-I` esclude la cartella corrente).
- L'import di Blender non passa all'USDHook i materiali dentro istanze USD → il wrapper le de-istanzia.
- Normal map SimReady = DirectX. Import Pack/Append: comanda il menu "Import Settings" dell'Asset Browser.
- .blend non riproducibili per 4 campi: `ID.session_uid`, `bNode.identifier` (casuale), `Mesh.face_sets_color_seed`
  (secondo corrente), `ScrEdge.v1/v2`; più lo zstd multithread. `blend_normalize.py` li sistema leggendo la DNA
  del file. Gli operatori BMesh (es. sfera UV) non sono riproducibili: nei test usare `from_pydata`.
- `http.client` non solleva `IncompleteRead` se la connessione cade prima di `Content-Length`: restituisce `b""`.
  Il CDN di HF (`cas-bridge`/`cdn.hf.co`) accetta `Range` anche dopo il redirect (206).
- Dettagli: `docs/fase1-report.md`, `docs/fase3-report.md`, `docs/ricerca-materiali.md`.

## Ultime cose fatte (2/11/2026, mattina)

- **Conversioni riproducibili** (`67bdb86`): `simready/convert/blend_normalize.py` + `save_reproducible`;
  10 asset casuali riconvertiti da zero = stesso sha256, contenuto identico al salvataggio di Blender.
  `CONVERTER_VERSION` non cambiato (le cache esistenti restano valide).
- **Errori di rete** (`b62bfbb`): download ripresi con Range, errori con `kind` e messaggio per l'utente
  (mostrato dall'add-on), attesa del rate limit visibile, controllo spazio su disco, pulizia all'avvio.
  Provato anche "senza internet" (proxy finto): errore chiaro in 3 s, libreria navigabile.
- **Asset enormi** (prima misura): `equipment_robot_trolley_a01` (1,77 GB) → download ~170 s,
  conversione 18 s, picco RAM 2 GB, .blend 96 MB (solo geometria).
- **JPEG**: script `tools/phase5/measure_jpeg.py` pronto, prima prova interrotta per tempo (lento sulle 4K).
- Nulla acceso: server di sviluppo (8790) e misura fermati. Dati di prova nello scratchpad della sessione (temporanei).

## Domande aperte per Tonysa87

1. Porta 8765 fissa (Claude propone di tenerla); albedo in JPEG (rimandato alla fase 5, da misurare).
2. Gruppi dei cataloghi da rivedere a occhio nell'Asset Browser.

## Prossimi passi

1. **Fase 5, punto 1 (asset enormi)**: convertire un asset grande *con molte texture* a Max e a 2K
   (memoria, tempo, dimensione del .blend) e rivedere la stima dello spazio su disco in `jobs._space_needed`.
2. **Fase 5, punto 4 (JPEG)**: far girare `measure_jpeg.py` su un campione (o in parallelo), poi
   proporre a Tonysa87 numeri e 2-3 render affiancati: decide lui.
3. Zip 0.5.0 dell'add-on per il test su Windows di Tonysa87: messaggi d'errore in Blender (provare a
   staccare la rete durante un download), riconversione con stesso hash.
4. Fase 6 (packaging): pulizia degli script di sviluppo, eventuale Blender Extensions, nuova release.

## Come riprendere

```bash
cd ~/Projects/nvd-sim-ready && git pull
B=~/blender/blender-5.2.2-linux-x64/blender
PY=~/blender/blender-5.2.2-linux-x64/5.2/python/bin/python3.13
python3 -m unittest discover -s tests && $PY -s -E -m unittest discover -s tests
# server per il Blender Windows (URL http://127.0.0.1:8765/); controllare se è già acceso:
pgrep -af "simready.server --state-dir"
nohup $PY -s -E -m simready.server --state-dir /tmp/claude-1000/devserver \
  --port 8790 --max-res 2048 --blender $B > /tmp/claude-1000/devserver.log 2>&1 &   # la 8765 ora è dell'add-on su Windows
```
