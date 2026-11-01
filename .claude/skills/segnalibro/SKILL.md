---
name: segnalibro
description: Aggiorna SEGNALIBRO.md con lo stato del progetto, le ultime cose fatte e i prossimi passi, poi commit e push. Da usare prima di /clear, a fine giornata o quando Tonysa87 chiede di salvare il punto.
---

# Aggiornare il segnalibro

Il file `SEGNALIBRO.md` (radice del progetto) è il ponte tra sessioni: CLAUDE.md lo importa, quindi
la prossima sessione parte da lì. Deve restare **breve e attuale** (circa 150 righe al massimo):
non è un diario.

1. Rileggi `SEGNALIBRO.md` e ricostruisci cosa è successo in questa sessione (conversazione,
   `git log --oneline` dall'ultimo aggiornamento, `git status`).
2. Aggiorna le sezioni:
   - **Ultimo aggiornamento**: data assoluta (es. 2/11/2026) e momento della giornata.
   - **Stato attuale** e la riga delle fasi (✅ / ⏳).
   - **Decisioni di Tonysa87**: aggiungi solo decisioni esplicite di Tonysa87, con la data. Non togliere
     quelle vecchie se non le ha cambiate lui.
   - **Fatti tecnici da non dimenticare**: solo cose non ovvie scoperte in sessione e ancora
     valide. Togli quelle superate.
   - **Ultime cose fatte**: **sostituisci** con quelle della sessione appena conclusa (3–8 punti,
     con i commit più importanti). Indica cosa è rimasto acceso o a metà (server, file temporanei).
   - **Domande aperte per Tonysa87**: togli quelle a cui ha risposto (riportando la risposta tra le
     decisioni) e aggiungi le nuove.
   - **Prossimi passi**: lista ordinata e concreta, da cui la prossima sessione può partire subito.
   - **Come riprendere**: comandi aggiornati, se sono cambiati.
3. Se sono cambiati dei comportamenti documentati, aggiorna anche `README.md` o `docs/`.
4. Commit con messaggio `Segnalibro: <sintesi in poche parole>`, autore Tonysa87 (già
   configurato) e riga finale `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; poi `git push`.
5. Rispondi a Tonysa87 in italiano con 3–5 righe: cosa hai salvato e qual è il primo passo per la
   prossima sessione.
