# SimReady to Blender — istruzioni per Claude Code

Stato del progetto, decisioni e prossimi passi (letti a ogni avvio di sessione):

@SEGNALIBRO.md

## Come lavorare con Tonysa87

- Rispondi **in italiano**, in modo semplice e diretto.
- **Fermati a fine fase**: proponi il piano e aspetta l'ok di Tonysa87 prima di scrivere molto codice.
- Commit: autore **sempre `Tonysa87 <101409643+Tonysa87@users.noreply.github.com>`** (configurato
  nel repo); Claude solo come `Co-Authored-By`. Push autorizzato verso `origin`
  (`Tonysa87/SimReady_to_Blender`, **repo pubblico**).
- **Niente dati personali nei file versionati e nei commit**: niente nome e cognome, email,
  percorsi della macchina (usare `<cartella dati>`, `<utente>`, `~`). Quelli servono a Claude:
  stanno in `CLAUDE.local.md` (non versionato, letto a ogni sessione).
- **Mai versionare** `cache/` e `out/` (dati NVIDIA e file generati).
- **Nomi NVIDIA**: niente proprietà aggiunte; asset di un pezzo = oggetto **e mesh** col nome
  dell'asset NVIDIA (cartella); nelle Collection le parti tengono i nomi NVIDIA delle mesh.
- Prima di un `/clear` o a fine giornata: aggiorna il segnalibro con `/segnalibro`.

## Ambiente

- WSL2 (Ubuntu 26.04). Blender 5.2 Linux: `~/blender/blender-5.2.2-linux-x64/blender`.
  Tonysa87 usa Blender 5.2 su **Windows**; da Windows `127.0.0.1:<porta>` raggiunge i server in WSL.
- Blender con interfaccia in WSL: `env WAYLAND_DISPLAY= ...` (X11, llvmpipe). Gli screenshot
  vengono neri: le verifiche visive si fanno con render Cycles (`tools/phase2/render_check.py`)
  o sul Blender di Tonysa87.
- Isolare i test GUI: `BLENDER_USER_RESOURCES=<scratch> XDG_CACHE_HOME=<scratch>`, `--factory-startup --online-mode`.
- Python di sistema con numpy/PIL: lanciare con `python3 -P` dalla cartella del progetto
  (non `-I`, che nasconde i pacchetti utente). Per i file scaricati non fidati non eseguire nulla da dentro `cache/`.
- **Mai `pkill -f`** con un pattern che compare nella propria riga di comando: uccide la shell.
  Usare `pgrep -af "^python3 tools/..."` e poi `kill <PID>`.

## Cache di Blender su Windows

`%LOCALAPPDATA%\Blender Foundation\Blender\Cache\remote-assets\` (in WSL
`/mnt/c/Users/<utente>/AppData/Local/Blender Foundation/Blender/Cache/remote-assets/`).
Toccarla solo dopo aver verificato il contenuto e **solo con l'ok esplicito di Tonysa87**.
Non toccare mai `online-essentials` né cartelle in uso da una libreria attiva.

## Riferimenti rapidi

- Sorgenti di Blender utili (copia parziale, da riscaricare se serve):
  `git clone --depth 1 --filter=blob:none --sparse -b blender-v5.2-release https://github.com/blender/blender`
  poi `git sparse-checkout set source/blender/asset_system source/blender/editors/asset ...`.
- Codice Python della libreria remota nella build: `5.2/scripts/modules/_bpy_internal/assets/remote_library/`.
