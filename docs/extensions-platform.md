# Pubblicazione su extensions.blender.org: materiale pronto

> Preparato da Claude il 3/11/2026. L'invio va fatto da Tonysa87 col suo account Blender ID
> (https://extensions.blender.org/submit/). Le regole citate vengono dai Terms of Service della
> piattaforma (versione del 10/8/2026) e dal manuale di Blender. Le pagine di moderazione non
> erano raggiungibili: le misure di icona e immagini vanno ricontrollate al momento dell'invio.

## 1. Le regole e come le rispetta l'add-on

| Regola (ToS) | Stato |
|---|---|
| 1.1 codice GPL-3.0-or-later | ✅ `license = ["SPDX:GPL-3.0-or-later"]`, file `LICENSE` nello zip |
| 1.2 immagini, font e dati inclusi devono essere **CC0** | ⚠️ l'indice di partenza (`seed_index.json.gz`) deriva dal dataset CC-BY-4.0 → usare lo zip **`-no-seed`** (vedi §2) |
| 1.3 copyright di tutti gli autori nel manifest | ✅ `copyright = ["2026 Tonysa87"]`; non c'è codice di terzi incluso |
| 2.1 niente "Blender" nel nome | ✅ "NVIDIA SimReady Library" (vedi §3 per il marchio NVIDIA) |
| 2.2-2.4 niente logo Blender, niente loghi di terzi senza permesso | ✅ nessun logo; per icona e immagini usare render degli asset, non il logo NVIDIA |
| 3.1 descrizione che dica tutto quello che fa | ✅ testo in §4 (server locale, download, conversione, cartella dati) |
| 3.5-3.6 solo Python, niente bytecode né pacchetti compilati | ✅ usa solo moduli già inclusi in Blender (pxr, OpenImageIO, numpy, zstandard, certifi) |
| 3.7 non peggiorare stabilità/prestazioni | ✅ conversioni in processi separati (`blender -b`), server in un processo separato |
| 3.10 niente chiavi o registrazioni obbligatorie | ✅ il token HF è facoltativo |
| 4.1 permesso di rete dichiarato e motivato | ✅ `network = "Download NVIDIA SimReady assets from Hugging Face"` |
| 4.2 rispettare "Allow Online Access" | ✅ senza online access il server non scarica e l'add-on lo dice nelle preferenze |
| 4.3 servizi esterni senza login | ✅ Hugging Face anonimo |
| 4.5 nessun dato inviato senza autorizzazione | ✅ solo richieste di download a HF (nessuna telemetria) |
| 5.1 niente codice remoto eseguito | ✅ dai file scaricati si leggono solo dati (USD, PNG), mai codice |
| 5.2 nessun componente funzionale da scaricare altrove | ✅ (gli asset sono contenuti, non componenti; da far confermare ai moderatori se chiedono) |
| Manuale: non scrivere nella cartella dell'add-on | ✅ tutto nella Data Folder scelta dall'utente (default: `%LOCALAPPDATA%\SimReady to Blender`) |

Punti che le regole non trattano e che i moderatori potrebbero chiedere (meglio dirli subito nelle
note per la revisione):
- l'add-on avvia **sottoprocessi**: il Python di Blender (`sys.executable`) per il server su
  `127.0.0.1` e `blender -b` per le conversioni;
- crea una **junction/symlink** nella cache di Blender (`remote-assets\<hash>`) verso la Data
  Folder, perché Blender non permette di spostare la cache delle librerie remote.

## 2. Quale zip inviare

```bash
python3 tools/build_extension.py --no-seed-index   # dist/simready_library-<versione>-no-seed.zip (~90 KB)
```

- Senza indice di partenza, al primo avvio il server costruisce l'indice da Hugging Face:
  ~8.000 richieste, **da 13 a ~40 minuti** secondo il rate limit; la libreria si riempie a
  blocchi di 200 asset mentre lavora. Dal secondo avvio è immediata.
- Lo zip completo (con indice, 2,7 MB) resta quello delle release su GitHub.
- Alternativa da decidere (non fatta): al primo avvio scaricare l'indice dalla release GitHub
  invece di costruirlo. Più veloce, ma aggiunge un secondo servizio esterno e va dichiarato.

## 3. Nome e marchio NVIDIA

Le regole parlano solo di loghi e di non far credere a un'approvazione. "NVIDIA SimReady" è il
nome del dataset e serve per farlo trovare, ma è un marchio. Proposte, in ordine di prudenza:
1. **"SimReady Asset Library"** con tagline "Unofficial: NVIDIA SimReady assets in the Asset Browser";
2. lasciare "NVIDIA SimReady Library" e scrivere "unofficial, not affiliated with NVIDIA" in
   cima alla descrizione.

Il manifest oggi ha il nome 2. Se Tonysa87 sceglie 1, basta cambiare `name` e `tagline` (l'`id`
`simready_library` resta, quindi le preferenze degli utenti non si perdono).

## 4. Testo della pagina (inglese)

**Tagline** (≤ 64 caratteri, senza punto finale; quella attuale va bene):
`NVIDIA SimReady assets in the Asset Browser, converted on demand`

**Description**:

> Unofficial add-on, not affiliated with or endorsed by NVIDIA.
>
> Browse the ~2,700 NVIDIA SimReady assets (Hugging Face dataset `nvidia/simready-assets`,
> CC-BY-4.0) in Blender's own Asset Browser, as a remote asset library: thumbnails, catalogs and
> search work like any other library. When you download an asset, it is fetched from Hugging Face
> and converted to .blend on your computer: USD geometry at real-world scale, OmniPBR/OmniGlass
> materials translated to Principled BSDF, textures at Max, 2K or 1K, optional JPEG color textures.
>
> How it works: the add-on starts a small local server (127.0.0.1 only, Blender's own Python)
> that publishes the library to Blender and converts assets with a background Blender process.
> Everything is stored in a Data Folder you choose (index, thumbnails, downloaded assets and
> Blender's download cache). Needs "Allow Online Access"; no account or token needed.
>
> The assets are © NVIDIA Corporation, CC-BY-4.0, and are not included: they are downloaded from
> Hugging Face when you ask for them. Every converted asset carries author, license and a link to
> its source in its asset metadata. Trademarks and logos on the assets are not covered by the license.

**Tag**: `Import-Export`, `Pipeline` (quelli del manifest).
**Website / tracker**: https://github.com/Tonysa87/SimReady_to_Blender

## 5. Immagini

Le misure vanno verificate sul modulo di invio (non erano nelle pagine lette). Di solito servono
un'**icona** quadrata (256×256 PNG), un'**immagine in evidenza** 16:9 (1920×1080) e qualche
**screenshot**.
- Screenshot: vanno fatti sul Blender di Tonysa87 (in WSL vengono neri): Asset Browser con la
  libreria aperta su un catalogo, la barra di stato durante una conversione, le preferenze
  dell'add-on, un asset convertito nel viewport in Material Preview.
- Immagine in evidenza e icona: si possono fare con `tools/render_check.py` + `tools/montage.py`
  da alcuni asset convertiti. Niente logo NVIDIA e niente logo Blender.

## 6. Prima dell'invio

1. Tonysa87 prova lo zip su Windows (vedi le note di rilascio 0.5.0).
2. Scelta del nome (§3) e dello zip (§2).
3. Screenshot e immagini (§5).
4. `python3 tools/build_extension.py --no-seed-index` e invio di quel file.
