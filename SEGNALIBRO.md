# Segnalibro di progetto

> Ponte tra sessioni di Claude Code: si aggiorna con `/segnalibro` prima di `/clear` o a fine
> giornata. CLAUDE.md lo importa, quindi viene letto all'inizio di ogni sessione.
> Ultimo aggiornamento: **1/11/2026, sera** (sessione in autonomia mentre Tonysa87 era fuori).

## Il progetto in breve

Rendere i ~2.677 asset NVIDIA SimReady (Hugging Face `nvidia/simready-assets`, CC-BY-4.0)
disponibili nell'**Asset Browser di Blender 5.2 LTS** come libreria remota. Il server locale
(127.0.0.1) pubblica un listing di tutti gli asset con un hash "segnaposto". Quando Blender chiede
un .blend, il server scarica l'asset da HF, lo converte (Blender headless) e lo consegna nella
stessa risposta; poi il listing viene ricaricato con l'hash reale. Un add-on avvierà il server e
gestirà la libreria. Brief completo: `docs/brief.md`.

Fasi: 1 meccanismo ✅ · 2 convertitore ✅ · 3 server completo ✅ (manca la prova su Windows) · **4 add-on ⏳** · 5 robustezza · 6 packaging.

## Stato attuale

- **Fase 3 (server)**: `simready/server/` pubblica tutti i 2.677 asset, 254 cataloghi, anteprime
  WebP; conversione on demand con keep-alive gzip (anche > 300 s). Report: `docs/fase3-report.md`.
- Gira col **Python incluso in Blender** (pxr, OpenImageIO, numpy, certifi; verificato anche il
  `python.exe` del Blender Windows di Tonysa87). Nessuna dipendenza esterna.
- **Acceso sulla porta 8765** il server nuovo, cartella `<cartella dati>\simready_test\phase3`
  (il vecchio server di fase 2 è stato fermato; la sua cartella `phase2` è intatta).
- Test: `python3 -m unittest discover -s tests` (43 test; quelli pxr girano col Python di Blender).
- Repository: privato, https://github.com/Tonysa87/SimReady_to_Blender (`main`), push autorizzato.

## Decisioni di Tonysa87

1/11/2026:
1. **Tipo di asset misto**: un solo pezzo → Object; più pezzi → Collection con mesh vere.
2. **Nomi sempre NVIDIA**: nessuna rinomina e nessuna proprietà aggiunta. Object = nome della
   mesh NVIDIA (es. `food_banana_a01_mesh_00`); Collection = nome della cartella; `.001` solo
   dove NVIDIA usa davvero due nomi uguali.
3. **Risoluzione texture**: menu nelle preferenze dell'add-on Max/2K/1K, per tutti gli asset.
4. **Progetto compatibile con OpenPBR**: descrizione intermedia con nomi OpenPBR → Principled BSDF.
5. **Modalità di import nell'add-on**: "Pack" oppure "Append, modificabile" (Make Instances Real +
   Make Local All, solo sui dati della cache SimReady).
6. Cache del server di test in `<cartella dati>\simready_test` (in WSL `/mnt/e/...`).
7. Sessione serale: mandato a Claude di proseguire in autonomia (ricerche, fino a 3 agenti Opus).
8. Idee di Tonysa87: **indice di partenza** già pronto + controllo periodico delle novità (fatto:
   `--seed-index`, `--check-hours 24`); **cartella dati scelta nell'add-on** (il server ha già
   un'unica `--state-dir`).
9. **L'indice di partenza sta nel repo** (`simready/server/data/seed_index.json.gz`, 2,6 MB), usato
   di default dal server. Aggiornarlo quando cambia la revisione HF (`tools/phase3/make_seed_index.py`).
10. Pulizia della vecchia cache `6f0c46218f1e3d46`: autorizzata e fatta (era solo il prototipo di fase 1).

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
- Dettagli: `docs/fase1-report.md`, `docs/fase3-report.md`, `docs/ricerca-materiali.md`.

## Ultime cose fatte (1/11/2026, sera)

- Server completo in `simready/server/` (commit `978de47`, `27df065`, `d4e4ab4`): client HF con
  rate limit, indice incrementale, cataloghi a gruppi (`catalogs.json`), anteprime WebP, conversioni
  single-flight con file per hash, listing paginato, `/_status`, keep-alive gzip.
- Previsione tipo/nome (`structure.py`, commit `1ce5d05`): 235/236 esatti su conversioni reali.
- Esperimenti in Blender (`tools/phase3/`): conversione di 330 s scaricata bene; fallimento senza
  file rotti; casi di mismatch listing/.blend.
- Revisione avversariale: 10 problemi corretti; value clip USD rimosse (`564c0e4`).
- Convertitore: opzioni `--root`/`--preview`, metadati condivisi in `simready/common.py`, tolte le
  proprietà `simready_*` dagli asset (regola "solo dati NVIDIA").
- Scaricati in `cache/raw/`: base/instances di tutti gli asset e payload di 236 asset (~2 GB, non versionati).
- Server di prova in scratch sulle porte 8780 (lasciato acceso, si può fermare) e 8765 (per Tonysa87).

## Domande aperte per Tonysa87

1. **Copia dei .blend nel server** (spazio doppio con la cache di Blender): Tonysa87 ha risposto "se
   GitHub lo permette caricali", probabile equivoco; chiarito in chat, in attesa di risposta.
2. Porta 8765 fissa (Claude propone di tenerla); albedo in JPEG (rimandato alla fase 5, da misurare).
3. Gruppi dei cataloghi da rivedere a occhio nell'Asset Browser.

## Prossimi passi

1. **Tonysa87, su Windows**: Shift+Refresh della libreria SimReady → 2.677 asset; scaricare e
   trascinare 2–3 asset nuovi; Shift+Refresh senza avvisi (procedura in `docs/fase3-report.md` §6).
2. Raccogliere le risposte alle domande aperte.
3. Fase 4, add-on: avvio del server con il Python di Blender (`sys.executable`, `--parent-pid`),
   registrazione unica della libreria con URL `http://127.0.0.1:<porta>/`, preferenze (cartella dati,
   risoluzione, porta, anteprime in background, import Pack/Append), timer su `/_status` → reload.
4. Verificare se la cache `remote-assets` di Blender segue un'impostazione generale.
5. Prima di scrivere l'add-on: proporre il piano a Tonysa87 e fermarsi a fine fase.

## Come riprendere

```bash
cd ~/Projects/nvd-sim-ready && git pull
B=~/blender/blender-5.2.2-linux-x64/blender
PY=~/blender/blender-5.2.2-linux-x64/5.2/python/bin/python3.13
python3 -m unittest discover -s tests && $PY -s -E -m unittest discover -s tests
# server per il Blender Windows (URL http://127.0.0.1:8765/); controllare se è già acceso:
pgrep -af "simready.server --state-dir"
nohup $PY -s -E -m simready.server --state-dir <cartella dati>/simready_test/phase3 \
  --port 8765 --max-res 2048 --blender $B > <cartella dati>/simready_test/phase3/stdout.log 2>&1 &
```
