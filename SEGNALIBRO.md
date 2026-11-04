# Segnalibro di progetto

> Ponte tra sessioni di Claude Code: si aggiorna con `/segnalibro` prima di `/clear` o a fine
> giornata. CLAUDE.md lo importa, quindi viene letto all'inizio di ogni sessione.
> Ultimo aggiornamento: **4/11/2026, sera** (test su Windows superato, **release v0.5.0 pubblicata**).
> **Il progetto è fermo alla 0.5**: tutte le fasi chiuse, restano solo cose facoltative (vedi Prossimi passi).

## Il progetto in breve

Rendere i ~2.677 asset NVIDIA SimReady (Hugging Face `nvidia/simready-assets`, CC-BY-4.0)
disponibili nell'**Asset Browser di Blender 5.2 LTS** come libreria remota. Il server locale
(127.0.0.1) pubblica un listing di tutti gli asset con un hash "segnaposto". Quando Blender chiede
un .blend, il server scarica l'asset da HF, lo converte (Blender headless) e lo consegna nella
stessa risposta; poi il listing viene ricaricato con l'hash reale. Un add-on avvierà il server e
gestirà la libreria. Brief completo: `docs/brief.md`.

Fasi: 1 meccanismo ✅ · 2 convertitore ✅ · 3 server completo ✅ (verificato su Windows) · 4 add-on ✅ (verificato su Windows) · 5 robustezza ✅ · 6 packaging ✅ (release 0.5.0 pubblicata il 4/11/2026).

## Stato attuale

- **Fase 3 (server)**: `simready/server/` pubblica tutti i 2.677 asset, 254 cataloghi, anteprime
  WebP; conversione on demand con keep-alive gzip (anche > 300 s). Report: `docs/fase3-report.md`.
- Gira col **Python incluso in Blender** (pxr, OpenImageIO, numpy, certifi; verificato anche il
  `python.exe` del Blender Windows di Tonysa87). Nessuna dipendenza esterna.
- La porta 8765 è del server avviato dall'add-on su Windows; in WSL per lo sviluppo si usa la 8790.
- Test: `python3 -m unittest discover -s tests` (72 test; quelli pxr/zstd/Blender girano col Python di Blender).
  Test GUI dell'add-on: `tools/gui_test_addon.py` (14/14 sullo zip 0.5.0, profilo pulito con Data Folder).
- **Fase 5 chiusa**, report: `docs/fase5-report.md`. **Add-on 0.5.0**: zip in `dist/`, nella cartella
  di consegna su Windows e nella **release v0.5.0 pubblicata** (Latest); installato sul Blender di Tonysa87.
- Materiale per extensions.blender.org: `docs/extensions-platform.md` (lo invia Tonysa87, se vuole).
- Repository **pubblico**: https://github.com/Tonysa87/SimReady_to_Blender (`main`), push autorizzato.
  Release **v0.5.0** con lo zip: https://github.com/Tonysa87/SimReady_to_Blender/releases/tag/v0.5.0.
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

4/11/2026:
17. Test su Windows della 0.5.0 superato; il test "rete staccata" **non si fa e non importa**: Tonysa87 non
    vuole staccare nulla dal PC (la ripresa resta coperta da `tests/test_hf.py`). Release pubblicata,
    **progetto fermo alla 0.5**.

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
- Cambiando Texture Size o JPEG il listing si aggiorna, ma Blender non segna l'asset già scaricato e il
  drag usa la copia vecchia finché non si fa Shift+refresh o un riavvio: solo allora compare il warning.
- Dettagli: `docs/fase1-report.md`, `docs/fase3-report.md`, `docs/ricerca-materiali.md`.

## Ultime cose fatte (4/11/2026)

- **Test su Windows dello zip 0.5.0** con Tonysa87, asset `sofa_soft_b02`, nessun errore nel log:
  - installazione sopra la 0.4.1: preferenze tenute, server ripartito da solo;
  - download: conversione 20 s, 124,8 MB, hash reale nel listing;
  - JPEG: 100,7 MB (−19%), nessuna differenza a vista anche da vicino;
  - riconversione da zero: stesso hash (`ce45eae1…`) e stessa dimensione.
- README (`5a6c4da`): 0.5.0 testata su Windows, nota sul cambio di impostazioni, JPEG tra le modifiche CC-BY.
- **Release v0.5.0 pubblicata**: tag su `5a6c4da`, zip pubblico uguale a `dist/`.
- In WSL nulla acceso. Su Windows il server è dell'add-on; JPEG lasciata spenta, divano in cache in PNG.

## Domande aperte per Tonysa87

1. Extensions: nome ("NVIDIA SimReady Library" o "SimReady Asset Library", vedi
   `docs/extensions-platform.md` §3), screenshot dal suo Blender, invio.
2. Porta 8765 fissa (Claude propone di tenerla); gruppi dei cataloghi da rivedere a occhio.

## Prossimi passi

Nulla di obbligatorio: il progetto è fermo alla 0.5. Se Tonysa87 vuole riprenderlo:

1. Extensions: seguire `docs/extensions-platform.md` §6.
2. Manutenzione: quando cambia la revisione HF, rigenerare l'indice (`tools/make_seed_index.py`).

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
