# Segnalibro di progetto

> Ponte tra sessioni di Claude Code: si aggiorna con `/segnalibro` prima di `/clear` o a fine
> giornata. CLAUDE.md lo importa, quindi viene letto all'inizio di ogni sessione.
> Ultimo aggiornamento: **3/11/2026, sera** (fase 5 chiusa, fase 6 pronta: manca il test su Windows di Tonysa87).
> **Si riprende la sera del 4/11/2026** da qui: prima cosa, chiedere a Tonysa87 l'esito del test dello zip 0.5.0.

## Il progetto in breve

Rendere i ~2.677 asset NVIDIA SimReady (Hugging Face `nvidia/simready-assets`, CC-BY-4.0)
disponibili nell'**Asset Browser di Blender 5.2 LTS** come libreria remota. Il server locale
(127.0.0.1) pubblica un listing di tutti gli asset con un hash "segnaposto". Quando Blender chiede
un .blend, il server scarica l'asset da HF, lo converte (Blender headless) e lo consegna nella
stessa risposta; poi il listing viene ricaricato con l'hash reale. Un add-on avvierà il server e
gestirà la libreria. Brief completo: `docs/brief.md`.

Fasi: 1 meccanismo ✅ · 2 convertitore ✅ · 3 server completo ✅ (verificato su Windows) · 4 add-on ✅ (verificato su Windows) · 5 robustezza ✅ · **6 packaging ⏳ (fatto tutto quello che dipende da Claude; release 0.5.0 in bozza)**.

## Stato attuale

- **Fase 3 (server)**: `simready/server/` pubblica tutti i 2.677 asset, 254 cataloghi, anteprime
  WebP; conversione on demand con keep-alive gzip (anche > 300 s). Report: `docs/fase3-report.md`.
- Gira col **Python incluso in Blender** (pxr, OpenImageIO, numpy, certifi; verificato anche il
  `python.exe` del Blender Windows di Tonysa87). Nessuna dipendenza esterna.
- La porta 8765 è del server avviato dall'add-on su Windows; in WSL per lo sviluppo si usa la 8790.
- Test: `python3 -m unittest discover -s tests` (72 test; quelli pxr/zstd/Blender girano col Python di Blender).
  Test GUI dell'add-on: `tools/gui_test_addon.py` (14/14 sullo zip 0.5.0, profilo pulito con Data Folder).
- **Fase 5 chiusa**, report: `docs/fase5-report.md`. **Add-on 0.5.0**: zip in `dist/` e nella cartella
  di consegna su Windows; **release v0.5.0 in bozza** su GitHub (la pubblica Tonysa87 dopo il test).
- Materiale per extensions.blender.org: `docs/extensions-platform.md` (lo invia Tonysa87, se vuole).
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
   di default dal server. Aggiornarlo quando cambia la revisione HF (`tools/make_seed_index.py`).
10. Pulizia della vecchia cache `6f0c46218f1e3d46`: autorizzata e fatta (era solo il prototipo di fase 1).
11. **Niente copia dei .blend nel server** (lo consegna e lo cancella, tiene solo hash/dimensione).
12. **La cache di Blender va nella cartella scelta dall'utente**: Blender non permette di spostarla
    (percorso fisso in `remote_library.cc`/`appdir.cc`), quindi l'add-on crea una junction
    `remote-assets\<hash URL>` → `<cartella dati>\blender-cache`.
13. Fase 4: Claude sviluppa e audita l'add-on in autonomia, senza approvazione del piano; Tonysa87 lo testa su Windows.
14. Tarda sera: chiuso il lavoro su FTC, **la prossima sessione prosegue con la fase 5**.

2/11/2026:
15. Piano della fase 5 approvato ("vai pure come ritieni opportuno"): ordine 3 → 2 → 1 → 4.

3/11/2026:
16. "Vorrei lasciarti finire in autonomia il progetto." In dettaglio:
    - **JPEG: decide Claude** → fatto come opzione "JPEG Color Textures", **spenta di default**;
    - **release finale in bozza**: la pubblica Tonysa87 dopo aver provato lo zip su Windows;
    - **Blender Extensions**: Claude prepara il materiale, l'invio (col suo account) lo fa Tonysa87.

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
- In più (a volte, sui file grandi) **i numeri `old` dei blocchi DATA**: la mappa indirizzo→numero di
  `writefile.cc` è unica per il file, un buffer temporaneo che riusa memoria di un ID precedente ne prende
  il numero. Il lettore risolve i puntatori ai dati solo dentro lo stesso ID → `renumber_data` rinumera per
  ID; anche i `sharing_info` (numeri senza blocco, ignorati in lettura).
- `Image.file_format = 'JPEG'` + `pack()` impacchetta comunque PNG: il JPEG va scritto con OpenImageIO e
  ricaricato; poi rimettere `filepath_raw` **e** `packed_files[0].filepath` (se no c'è il percorso temporaneo).
- numpy in N processi paralleli: impostare `OPENBLAS_NUM_THREADS=1` (altrimenti 16 × 32 thread, carico 290).
- extensions.blender.org: dati inclusi solo **CC0** (ToS 1.2) → lo zip `--no-seed-index`; senza indice
  il primo avvio costruisce l'indice da HF (13-40 min).
- `http.client` non solleva `IncompleteRead` se la connessione cade prima di `Content-Length`: restituisce `b""`.
  Il CDN di HF (`cas-bridge`/`cdn.hf.co`) accetta `Range` anche dopo il redirect (206).
- Dettagli: `docs/fase1-report.md`, `docs/fase3-report.md`, `docs/ricerca-materiali.md`.

## Ultime cose fatte (3/11/2026)

- **Riproducibilità corretta** (`f6900de`): trovata una conversione a 2K su tre con hash diverso
  (numeri dei blocchi, vedi sopra); ora 4 asset multi-mesh × 3 conversioni e l'asset grande × 4 = stesso hash.
- **JPEG** (`bd9c948`): 150 albedo a caso, q95 4:4:4 = 26-30% del PNG, PSNR mediano 46 dB, render
  identici (59 dB). Opzione in add-on/server/convertitore; .blend −23% a 2K, −28% a Max. Chiave di stato
  `<asset>@2048-jpeg`: con PNG le cache esistenti restano valide.
- **Asset enormi**: `structure_wall_entrance_a01` (1,1 GB di texture) a Max 12 s, 4,4 GB RAM, 875 MB;
  a 2K 29 s, 1,7 GB, 252 MB. Stima spazio disco controllata: prudente, lasciata com'è.
- **Fase 6** (`0089dd0`, `be553fe`): `tools/` ripulito (prototipi solo nella storia, tag v0.4.1), README
  aggiornato, versione 0.5.0, `--no-seed-index`, Start/Check Updates rifiutati senza Online Access.
- Zip 0.5.0 in `dist/` e nella cartella di consegna; release v0.5.0 in **bozza** con note.
- Nulla acceso. Dati di prova (download e .blend) nello scratchpad della sessione, temporanei.

## Domande aperte per Tonysa87

1. Prova dello zip 0.5.0 su Windows, poi pubblicare la bozza della release v0.5.0.
2. Extensions: nome ("NVIDIA SimReady Library" o "SimReady Asset Library", vedi
   `docs/extensions-platform.md` §3), screenshot dal suo Blender, invio.
3. Porta 8765 fissa (Claude propone di tenerla); gruppi dei cataloghi da rivedere a occhio.

## Prossimi passi

1. Test di Tonysa87 su Windows (0.5.0): installare sopra la 0.4.1 (preferenze tenute), scaricare un
   asset, staccare la rete durante un download grande (deve riprendere o dare un errore chiaro),
   provare "JPEG Color Textures", riconvertire un asset (stesso hash).
2. Se va bene: pubblicare la release v0.5.0 (`gh release edit v0.5.0 --draft=false`) e aggiornare il README.
3. Se Tonysa87 vuole Extensions: seguire `docs/extensions-platform.md` §6.
4. Manutenzione: quando cambia la revisione HF, rigenerare l'indice (`tools/make_seed_index.py`).

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
