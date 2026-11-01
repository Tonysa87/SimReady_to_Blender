# SimReady to Blender

Gli asset NVIDIA **SimReady** ([`nvidia/simready-assets`](https://huggingface.co/datasets/nvidia/simready-assets),
~2.677 asset, CC-BY-4.0) direttamente nell'**Asset Browser di Blender 5.2 LTS**, come una
libreria remota qualsiasi. Si vedono tutti subito, con anteprime e cataloghi; un asset viene
scaricato da Hugging Face e **convertito in .blend al momento del download**.

> Stato: **fase 3 completata** (server con tutti i 2.677 asset, da verificare sul Blender Windows).
> L'add-on (fase 4) è da fare. Dettagli in `SEGNALIBRO.md` e `docs/fase3-report.md`.

## Come funziona

```
Blender (Asset Browser, libreria remota)
   │  GET _asset-library-meta.json, _v1/*.json, thumbs/*, blend/*.blend?hash=...
   ▼
Server locale 127.0.0.1:8765 (Python di Blender: stdlib + pxr + OpenImageIO)
   ├── listing generato dall'indice NVIDIA (hash "segnaposto" per i .blend non ancora convertiti)
   ├── alla prima richiesta di un .blend: download da Hugging Face → conversione headless → risposta
   └── /_status: l'add-on ricarica il listing con l'hash reale → asset "verificato" in Blender
```

La conversione (`simready/convert/convert_asset.py`, eseguita da `blender -b`):

1. livello USD "wrapper": varianti di fisica disattivate, istanze USD rese normali prim;
2. import USD nativo di Blender per geometria, UV e scala (metri);
3. **materiali convertiti da noi** tramite `bpy.types.USDHook`: OmniPBR, OmniGlass e
   UsdPreviewSurface → descrizione intermedia con nomi **OpenPBR** → Principled BSDF.
   La semantica segue il codice MDL di NVIDIA (tinta, influenze, ORM, opacità); le normal map
   SimReady sono **DirectX** (verificato) e usano il nodo Normal Map in modalità DirectX;
4. asset di un solo pezzo → **Object**, asset composti → **Collection**; nomi sempre quelli NVIDIA;
5. texture ridotte a Max/2K/1K e impacchettate; metadati e anteprima NVIDIA incorporati.

## Struttura

| Percorso | Contenuto |
|---|---|
| `simready/convert/` | convertitore USD → .blend (futuro cuore dell'add-on) |
| `simready/server/` | server della libreria remota: indice HF, cataloghi, anteprime, conversioni, listing |
| `simready/server/data/seed_index.json.gz` | indice di partenza dei 2.677 asset (nomi, dimensioni e sha256 dei file NVIDIA, CC-BY-4.0); si rigenera con `tools/phase3/make_seed_index.py` |
| `tests/` | test di materiali, server e previsione della struttura |
| `tools/phase1/` | prototipo del meccanismo di libreria remota e test GUI |
| `tools/phase2/` | conversione batch, render di confronto, test normal map, server di test con asset reali |
| `tools/phase3/` | esperimenti keep-alive/mismatch in Blender, validazione della previsione |
| `tools/recon/` | script di ricognizione del dataset Hugging Face |
| `docs/brief.md` | brief iniziale del progetto |
| `docs/fase1-report.md` | esito delle fasi 1–2, comportamenti e bug di Blender 5.2, decisioni |
| `docs/fase3-report.md` | esito della fase 3: server, indice, keep-alive, previsione |
| `docs/ricerca-materiali.md` | ricerca MDL → Blender, mappatura OmniPBR, fonti |
| `SEGNALIBRO.md` | stato attuale, ultime cose fatte, prossimi passi |
| `cache/`, `out/` | dati NVIDIA scaricati e file generati — **non versionati** |

## Uso (sviluppo, Linux/WSL)

Blender 5.2 Linux in `~/blender/blender-5.2.2-linux-x64/blender` (`$B` negli esempi).

```bash
# Test delle mappature
python3 -m unittest discover -s tests

# Convertire un asset già scaricato in cache/raw/<asset>
$B -b --factory-startup --python simready/convert/convert_asset.py -- \
   --asset-dir cache/raw/food_banana_a01 --out out/test/banana.blend \
   --max-res 2048 --report out/test/banana.json

# Conversione dei 6 asset di prova a Max/2K/1K
python3 tools/phase2/convert_batch.py

# Server completo (Blender Windows → URL http://127.0.0.1:8765/), col Python di Blender
PY=~/blender/blender-5.2.2-linux-x64/5.2/python/bin/python3.13
$PY -s -E -m simready.server --state-dir <cartella> --port 8765 --max-res 2048 --blender $B
```

In Blender: *Preferences → File Paths → Asset Libraries → + → Add Remote Asset Library*,
URL `http://127.0.0.1:8765/` (senza spazi). Una sola libreria per URL; non modificarla durante
la sessione (vedi i bug documentati in `docs/fase1-report.md`).

## Licenze

- Codice: GPL-3.0-or-later (file `LICENSE` da aggiungere).
- Asset: © NVIDIA Corporation, [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/).
  L'indice di partenza (`simready/server/data/`) contiene solo metadati derivati dal dataset
  NVIDIA (nomi, dimensioni, impronte dei file), stessa licenza e attribuzione.
  I marchi e i loghi presenti sugli asset non sono coperti dalla licenza. Gli asset non sono
  inclusi in questo repository: vengono scaricati da Hugging Face sul computer dell'utente.
