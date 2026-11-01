# Brief di progetto — Asset NVIDIA SimReady nell'Asset Browser di Blender

> Documento di avvio per Claude Code. Preparato in chat con Claude l'1/11/2026 e aggiorna il brief del 26/10/2026.
> Progetto personale di Tonysa87. Potrebbe essere pubblicato in futuro.
>
> Nota (1/11/2026): copia d'archivio del brief iniziale. Le sezioni 1–2 sono integrali, le 3–11
> sono riassunte. Le decisioni prese dopo, che in alcuni punti lo superano, sono in
> `SEGNALIBRO.md` e in `docs/fase1-report.md`.

## 1. Obiettivo

Rendere i ~2.576 asset del dataset Hugging Face **`nvidia/simready-assets`** (CC-BY-4.0, ~472 GB) disponibili **direttamente nell'Asset Browser nativo di Blender 5.2 LTS**, come una qualsiasi libreria di asset. **Niente pannelli nella sidebar N e nessuna UI di navigazione propria.**

Esperienza voluta:

1. In Preferenze → File Paths compare una libreria "NVIDIA SimReady".
2. Nell'Asset Browser si vedono subito **tutti** gli asset, con anteprime, cataloghi per categoria, ricerca, tag, autore e licenza, anche quelli mai scaricati (icona "online").
3. Al drag & drop, oppure con tasto destro → *Download Asset*, l'asset viene scaricato da Hugging Face, **convertito al volo** da USD a .blend con materiali Principled BSDF e consegnato a Blender. Per l'utente è identico a un asset delle Online Essentials.
4. Non va mai scaricato in anticipo l'intero dataset.

## 2. Idea architetturale

Blender 5.2 ha le **Remote Asset Libraries**: una libreria remota è un server HTTP che espone un *listing* JSON, le anteprime e i file .blend. Questo progetto realizza un **server locale "virtuale"** su `127.0.0.1`:

- il **listing** viene generato dall'indice degli asset su Hugging Face, non da .blend già esistenti;
- quando Blender chiede un .blend che non esiste ancora, il server scarica l'asset da HF, lo converte con un processo Blender headless e lo restituisce nella stessa risposta HTTP;
- un piccolo **add-on/extension** gestisce il ciclo di vita del server, registra la libreria nelle Preferenze e fa risincronizzare il listing dopo le conversioni.

```
Blender (Asset Browser, downloader remoto integrato)
   │  GET _asset-library-meta.json, _v1/*.json, thumbs/*, blend/*.blend?hash=...
   ▼
Server locale (processo separato, Python stdlib, 127.0.0.1:<porta>)
   ├── Indice      ← API HF (cartelle + BOM di ogni asset)  → cache JSON
   ├── Listing v1  ← generato dall'indice + stato conversioni → pagine JSON
   ├── Anteprime   ← .thumbs HF ridotte, generate on-demand  → cache
   └── .blend      ← download filtrato da HF + conversione   → cache .blend
                         │
                         ▼
               Worker di conversione:
               subprocess `blender -b --factory-startup --python convert.py -- <asset> <out>`

Add-on (dentro Blender): avvia/arresta il server, registra la libreria remota,
preferenze (porta, cartella cache, worker, token HF), risincronizza il listing.
```

## 3. Cosa ho verificato nel codice di Blender 5.2 (branch `blender-v5.2-release`)

File da rileggere per conferma: `scripts/modules/_bpy_internal/assets/remote_library/` (`blender_asset_library_openapi.yaml`, `asset_downloader.py`, `listing_downloader.py`, `hashing.py`, `cli_listing_generator.py`), `source/blender/editors/asset/intern/asset_indexer_remote_file_status.cc`, `source/blender/editors/space_file/filelist/filelist.cc`, `filelist_readjob_asset_library_remote.cc`, `source/blender/makesrna/intern/rna_userdef.cc`, `source/blender/editors/asset/intern/asset_ops.cc`.

Fatti rilevanti:

1. Struttura del listing: `_asset-library-meta.json` → `_v1/asset-index.json` → pagine con `assets[]` e `files[]`.
2. Ogni file .blend nel listing richiede `path`, `size_in_bytes`, `hash` (`SHA256:<hex>`) e `blender_version`: il server deve dichiarare un hash anche per i .blend non ancora convertiti.
3. L'hash nell'URL serve solo contro la cache (`GET <url>?hash=<hex>`), non viene verificato al download.
4. Il controllo arriva dopo: per un file già scaricato Blender confronta hash e dimensione (`FileStatusChecker`); se non coincidono l'asset è `NO_MATCH`, utilizzabile ma "outdated or inconsistent".
5. Download: una richiesta alla volta per libreria, timeout 300 s, header `X-Blender`.
6. Registrazione da Python e risincronizzazione con `bpy.ops.asset.library_reload_listing()`; serve "Allow Online Access".
7. Metadati per asset: `catalog_id`, `tags`, `author`, `description`, `license`, `copyright`, `preferred_import_method`, `properties`; `bl_versions.min` obbligatorio.
8. Anteprime: `thumbnail = {url, hash}`, almeno 256×256, WebP o PNG.

### 3.1 Strategia "hash segnaposto → hash reale"

Prima della conversione il listing dichiara un hash segnaposto deterministico e una dimensione stimata. Alla prima richiesta il server converte, calcola hash e dimensione reali, aggiorna il listing e serve il file. L'add-on, avvisato tramite `/_status`, chiama `library_reload_listing` e lo stato passa a `MATCH`.

## 4. Il dataset

- Una cartella per asset; il prefisso del nome (`food_`, `kitchenware_`, `ppe_`, …) è la base dei cataloghi.
- Struttura: `com.nvidia.simready.packaging.json`, `.metadata/` (LICENSE, BOM, root_usds, conformance), `simready_usd/` (USD radice, `payloads/`, `Textures/`, `source_assets/` da non scaricare, `.thumbs/256x256/`).
- Da scaricare: USD radice, `payloads/**`, `Textures/*.png`, `.metadata/LICENSE.txt`.
- API HF senza login: `GET https://huggingface.co/api/datasets/nvidia/simready-assets/tree/main` (paginazione nell'header `Link`); download da `.../resolve/main/<path>`; revisione da `/api/datasets/nvidia/simready-assets/revision/main`.
- Codice riutilizzabile: add-on [albarmuzaqi/simready-asset-browser](https://github.com/albarmuzaqi/simready-asset-browser) (GPL-3.0-or-later), da citare se riusato.
- Licenza: ogni pacchetto è CC-BY-4.0; marchi e loghi non coperti.

## 5. Conversione USD → .blend

Download filtrato e verificato con il BOM; ispezione con `pxr`; `usd_import` con istanze realizzate e fisica disattivata; materiali Principled BSDF (preview USD o ricostruzione da OmniPBR; ORM R=AO G=Roughness B=Metallic; normal map DirectX); asset con metadati (autore NVIDIA, CC-BY-4.0, catalogo, tag, anteprima); texture impacchettate; log per asset; errori come 5xx chiari. Nome asset nel listing = nome nel .blend.

## 6. Server locale

Processo separato con il Python di Blender, solo standard library, solo `127.0.0.1`, porta configurabile, lockfile, watchdog sul PID di Blender. Endpoint: meta/indice/pagine (ETag → 304), `/thumbs/`, `/blend/` (conversione on-demand, richieste concorrenti sulla stessa conversione), `/_status`. Indice ricostruito solo al cambio di revisione HF. Cataloghi da tabella di mapping, UUID deterministici (`uuid5`).

## 7. Add-on (extension)

Nessun pannello N. Preferenze: porta, cartella cache, worker, token HF, avvio automatico, avvia/arresta, ricostruisci indice, svuota cache, stato. All'attivazione avvia il server e registra la libreria. Timer su `/_status` → `library_reload_listing`. Rispetta `bpy.app.online_access`. `blender_manifest.toml`: permessi `network` e `files`, `blender_version_min = "5.2.0"`, `SPDX:GPL-3.0-or-later`; compatibile con 5.3. Opzione per aggiungere la cache convertita come libreria locale.

## 8. Ambiente di sviluppo

Claude Code in Ubuntu 26.04 su WSL2; Blender di Tonysa87 su Windows 11. Blender 5.2 Linux portable per i test headless. Dal Blender Windows `127.0.0.1` raggiunge il server in WSL2. **Commit git: autore sempre Tonysa87, Claude solo come `Co-Authored-By`.**

## 9. Fasi di lavoro

1. Ricognizione e prova del meccanismo (report a Tonysa87).
2. `convert.py` su 3–5 asset con confronto visivo.
3. Server completo.
4. Add-on.
5. Robustezza.
6. Packaging come extension, README con attribuzione CC-BY a NVIDIA.

Strada B (ripiego): listing con soli asset già convertiti e coda di prefetch.

## 10. Vincoli

Blender 5.2 LTS ufficiale; nessuna dipendenza esterna a runtime; mai scaricare `source_assets/` né le `.thumbs/` delle texture; nessun file fuori dalla cartella cache; server solo su `127.0.0.1`; fermarsi a fine fase per il report.

## 11. Riferimenti

- Dataset: https://huggingface.co/datasets/nvidia/simready-assets
- Remote Asset Libraries (manuale 5.2): https://docs.blender.org/manual/en/5.2/files/asset_libraries/remote_asset_libraries.html
- Remote Asset Libraries (sviluppatori): https://developer.blender.org/docs/features/asset_system/remote_asset_libraries/
- Blog Blender: https://code.blender.org/2026/07/remote-asset-libraries/
- Sorgenti Blender: https://github.com/blender/blender
- Add-on esistente: https://github.com/albarmuzaqi/simready-asset-browser
- Linee guida materiali SimReady: https://docs.omniverse.nvidia.com/simready/latest/simready-asset-creation/material-best-practices.html
- Add-on SimReady ufficiale NVIDIA: https://github.com/NVIDIA/simready-blender-add-on
