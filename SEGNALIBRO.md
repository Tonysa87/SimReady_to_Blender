# Segnalibro di progetto

> Ponte tra sessioni di Claude Code: si aggiorna con `/segnalibro` prima di `/clear` o a fine
> giornata. CLAUDE.md lo importa, quindi viene letto all'inizio di ogni sessione.
> Ultimo aggiornamento: **1/11/2026, fine giornata**.

## Il progetto in breve

Rendere i ~2.677 asset NVIDIA SimReady (Hugging Face `nvidia/simready-assets`, CC-BY-4.0)
disponibili nell'**Asset Browser di Blender 5.2 LTS** come libreria remota. Il server locale
(127.0.0.1) pubblica un listing di tutti gli asset con un hash "segnaposto". Quando Blender chiede
un .blend, il server scarica l'asset da HF, lo converte (Blender headless) e lo consegna nella
stessa risposta; poi il listing viene ricaricato con l'hash reale. Un add-on avvierà il server e
gestirà la libreria. Brief completo: `docs/brief.md`.

Fasi: 1 meccanismo ✅ · 2 convertitore ✅ · **3 server completo ⏳** · 4 add-on · 5 robustezza · 6 packaging.

## Stato attuale

- **Fase 1 (meccanismo)**: verificata su Linux e sul Blender Windows di Tonysa87. Strada A confermata.
- **Fase 2 (convertitore)**: `simready/convert/` funziona su 6 asset reali, a Max/2K/1K, in
  0,6–7 s per asset. Verificata su Windows (armadietto, scaffale): download, materiali e reload OK.
- Repository: privato, https://github.com/Tonysa87/SimReady_to_Blender (`main`), push autorizzato.
- Test: `python3 -m unittest discover -s tests` (16 test, OK).

## Decisioni di Tonysa87 (1/11/2026)

1. **Tipo di asset misto**: un solo pezzo → Object; più pezzi → Collection con mesh vere.
2. **Nomi sempre NVIDIA**: nessuna rinomina e nessuna proprietà aggiunta. Object = nome della
   mesh NVIDIA (es. `food_banana_a01_mesh_00`); Collection = nome della cartella; `.001` solo
   dove NVIDIA usa davvero due nomi uguali.
3. **Risoluzione texture**: menu nelle preferenze dell'add-on Max/2K/1K, per tutti gli asset,
   riduzione al momento della conversione.
4. **Progetto compatibile con OpenPBR**: materiali tradotti in una descrizione intermedia con nomi
   OpenPBR, poi in Principled BSDF (Blender 5.2 non ha ancora un nodo OpenPBR).
5. **Modalità di import nell'add-on**: "Pack" (default di Blender) oppure "Append, modificabile"
   (dopo ogni rilascio di un asset SimReady: Make Instances Real + Make Local All, solo sui dati
   della cache SimReady). Verificato a mano da Tonysa87.
6. Cache del server di test in `<cartella dati>\simready_test` (in WSL `/mnt/e/...`).

## Fatti tecnici da non dimenticare

- Libreria remota: si crea solo con `bpy.ops.preferences.asset_library_add(type='REMOTE', ...)`;
  `asset_libraries.new()` crea una libreria locale.
- La cartella di cache di Blender = md5 del **testo dell'URL** (prima della normalizzazione).
  **Mai modificare o ricreare la libreria durante una sessione**: sia C++ sia Python tengono in
  memoria la libreria per URL e serve un riavvio di Blender.
- Drag & drop di un asset non scaricato bloccato da Blender; il download parte dal pulsante.
- Dopo il download Blender considera l'asset verificato senza controllare; il controllo vero
  arriva al reload o al riavvio, per questo serve il reload con l'hash reale.
- Il timeout di 300 s si conta tra un byte e l'altro: la conversione deve produrre il primo byte entro 300 s.
- Metadati e anteprima **dentro il .blend** devono coincidere con il listing (dopo il download
  Blender usa quelli del file).
- L'import di Blender non passa all'USDHook i materiali dentro istanze USD → il wrapper le de-istanzia.
- Normal map SimReady = DirectX (test di integrabilità: `tools/phase2/normalmap_convention.py`).
- Import Pack/Append: comanda il menu "Import Settings" dell'Asset Browser; con "Follow Asset or
  Preferences" il Blender di Tonysa87 ha usato Pack anche con la libreria su Append (da chiarire).
- Dettagli e altri bug: `docs/fase1-report.md`; ricerca materiali: `docs/ricerca-materiali.md`.

## Ultime cose fatte (1/11/2026)

- Fasi 1 e 2 complete; README, brief, segnalibro e CLAUDE.md scritti; repo pubblicato su GitHub.
- Nomi NVIDIA preservati (commit `1aba990`); nodo Normal Map in modalità DirectX.
- Sicurezza: il convertitore rifiuta texture e USD fuori dalla cartella dell'asset.
- Lasciato acceso: server di test sulla porta 8765, se la macchina non è stata riavviata
  (`pgrep -af "^python3 tools/phase2/test_server.py"`).

## Domande aperte per Tonysa87

1. **Porta 8765 fissa**: va bene? (cambiare porta = nuova cartella di cache in Blender)
2. **Asset molto grandi** (1–1,8 GB, conversione forse > 300 s): conversione in background con
   messaggio "riprova", oppure pre-conversione delle categorie scelte?
3. **Pulizia**: cancellare la vecchia cartella di cache di Blender `6f0c46218f1e3d46` (solo dati
   di test, compresa la scimmia finta) in `%LOCALAPPDATA%\Blender Foundation\Blender\Cache\remote-assets\`?
4. Peso dei file: comprimere l'albedo in JPEG? (oggi tutto PNG; scaffale 137 MB)

## Prossimi passi (fase 3 — server completo)

1. Indice da Hugging Face: elenco cartelle (paginazione `Link`), BOM, `root_usds.json`, e
   `base.usda` per conoscere **in anticipo** tipo (Object/Collection) e nome NVIDIA del datablock.
   Cache su disco, ricostruzione solo al cambio di revisione HF, backoff sul rate limit (~3000 req/300 s).
2. Cataloghi dal prefisso (tabella di mapping modificabile, UUID `uuid5` stabili).
3. Anteprime: ridurre i PNG NVIDIA 1024² a 256² (WebP/PNG), on demand e in cache.
4. Download filtrato on demand (radice, `payloads/**`, `Textures`/`textures`, LICENSE) con
   verifica sha256 dal BOM; poi conversione; richieste concorrenti sulla stessa conversione.
5. `/_status` per l'add-on; versione del convertitore nell'hash segnaposto.
6. Prima di scrivere molto codice: proporre il piano a Tonysa87 e fermarsi a fine fase.

## Come riprendere

```bash
cd ~/Projects/nvd-sim-ready && git pull
B=~/blender/blender-5.2.2-linux-x64/blender
python3 -m unittest discover -s tests
# server di test per il Blender Windows (URL http://127.0.0.1:8765/)
nohup python3 tools/phase2/test_server.py --state-dir <cartella dati>/simready_test/phase2 --port 8765 --max-res 2048 &
```
