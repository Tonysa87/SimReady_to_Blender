# Fase 4 — Report: add-on

Data: 1/11/2026 (sera) · sviluppato e revisionato in autonomia (decisione di Tonysa87), **verificato da Tonysa87 su Windows**.

## Cosa fa

Estensione Blender 5.2 (`addon/` + pacchetto `simready/`, zip costruito da `tools/build_extension.py`):

- **Server**: avviato come processo figlio col Python di Blender (`sys.executable -s -E -m simready.server`),
  si ferma con Blender (handler `exit_pre` + controllo del PID). Se sulla porta risponde già un altro
  server (es. quello di sviluppo in WSL) lo usa così com'è; "Stop" nelle preferenze lo spegne.
- **Libreria**: registrata una volta sola (`http://127.0.0.1:<porta>/`); mai modificata durante la
  sessione, nemmeno il metodo di import. Se viene rimossa, l'add-on chiede di riavviare Blender.
- **Niente Shift+Refresh**: dopo ogni asset consegnato l'add-on ricarica il listing
  (`bl_pkg.remote_asset_library_sync`), con nuovo tentativo se una sincronizzazione è già in corso.
- **Cache di Blender nella cartella dati**: la cartella fissa di Blender
  (`…\Cache\remote-assets\<md5 URL>`) diventa una junction verso `<cartella dati>\blender-cache\<md5 URL>`.
  Si attiva solo dopo aver scelto la cartella e al riavvio successivo; la cache esistente viene spostata.
- **Append, modificabile**: handler `blend_import_pre/post`; dopo un rilascio SimReady fa
  Make Instances Real sulle nuove istanze e rende locali **solo** i dati della cache SimReady.
- **Server senza copia**: consegna il .blend e lo cancella; il listing tiene l'hash vero.
- **Nomi**: asset di un pezzo = oggetto e mesh col nome dell'asset (es. `food_banana_a01`).
- Preferenze: cartella dati, texture Max/2K/1K, import Pack/Append modificabile, anteprime in
  background, avvio automatico, porta, token HF. Stato del server e conversioni nella barra di stato.

## Verifiche

- Test GUI in Blender Linux (`tools/phase4/gui_test_addon.py`): **15/15**. Server avviato
  dall'add-on, libreria unica, cache collegata, download con reload automatico e hash vero, copia del
  server cancellata, istanze rese reali (Link e Append), libreria estranea lasciata collegata,
  server fermato all'uscita.
- Junction vere su NTFS: test di `cache_link` col Python di Windows (8/8).
- Server avviato col `python.exe` del Blender Windows di Tonysa87: indice, anteprime, conversione con
  `blender.exe` (4,5 s), consegna gzip, spegnimento con `/_control/shutdown`.
- Revisione avversariale: 11 problemi, tutti corretti. Il più grave: `make_local(type='ALL')`
  avrebbe reso locali anche le librerie non SimReady dell'utente.

## Prova su Windows (Tonysa87, 1/11 sera, versione 0.4.1)

- Primo tentativo: la porta 8765 era ancora occupata dal server di sviluppo in WSL; il server di Windows
  usciva con "porta occupata" e l'add-on non riprovava più → corretto in 0.4.1 (nuovo tentativo ogni 15 s).
- Server avviato da Blender (cartella dati `<cartella dati>\simready_cache\`, texture Max),
  anteprime generate in background, nessuna finestra di console.
- `engineComponent_cylinder_piston_a01`: conversione in 7 s, copia del server cancellata, listing ricaricato
  dall'add-on 3 s dopo (hash nel listing di Blender = file), nessun avviso.
- "Append, editable": empty genitore con i due pezzi veri, locali, modificabili in Edit Mode. Tonysa87 tiene l'empty.
- Riavvio: cache di Blender trasformata in junction verso `simready_cache\blender-cache\ef7615a0e6baf832`,
  pistone spostato e ancora "scaricato", nessun avviso.

## Limiti noti

- ~~Un asset riconvertito (cache di Blender svuotata) ha un hash diverso~~: risolto nella fase 5,
  la conversione ora è bit-per-bit identica (`docs/fase5-report.md`).
- La migrazione della cache tra dischi diversi (C: → E:) avviene all'avvio di Blender e lo
  rallenta per qualche secondo (≈ 0,5 GB nel caso di Tonysa87).
- Cambiare porta o cartella dati richiede il riavvio di Blender.
- Disinstallando l'add-on la junction resta (la cache continua a funzionare finché esiste la cartella).
