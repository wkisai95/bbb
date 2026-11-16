# App Tasse proprietario

Calcola quanto deve pagare di tasse un proprietario che affitta con P.IVA in regime forfettario
(ATECO 55.20.42, coefficiente 40%, INPS commercianti, imposta sostitutiva 5% o 15%), al netto delle commissioni
dei portali e della gestione (20% sul lordo, modificabile).

**Installare:** scarica [`tasse.apk`](https://github.com/wkisai95/bbb/raw/apk-tasse/tasse.apk) (Android 8 o successivo).

## Da dove prende i dati

Dai file cifrati che l'app bbb salva in `config/cifrati` ([formato](../docs/cifratura.md)), con il codice o l'invito
`bbb1:…` che bbb dà a ogni proprietario. Il codice del gestore mostra tutti i proprietari.

- **Collegare:** in bbb, Dati → File cifrati → condividi il codice del proprietario e scegli *Tasse proprietario*
  nel menu Condividi, oppure incolla l'invito nell'app.
- **Aggiornamento:** l'app rilegge il file all'apertura e ogni 5 minuti; bbb lo riscrive quando cambi un appartamento
  e ogni giorno con i prezzi nuovi.
- **Cosa usa:** per ogni appartamento le stime a 30, 60, 90 e 365 giorni (`incasso_lordo`, `commissioni`, `costi`,
  `tassa_soggiorno_da_versare`) della strategia scelta. Le tasse dell'anno si calcolano sui 365 giorni.

## Cartelle

| | |
|---|---|
| `web/` | l'interfaccia (`index.html`, `tasse.js`); `cifrato.js` è quello di `app/web`, copiato nell'APK in compilazione |
| `android/` | il guscio Android (`com.wkisai.tasse`) |

L'APK lo compila `.github/workflows/tasse.yml` a ogni modifica di `tasse/` o di `app/web/cifrato.js`.
