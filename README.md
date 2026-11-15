# bbb · prezzi Airbnb a Bologna

Ogni notte un programma su GitHub legge i prezzi degli appartamenti interi su Airbnb a Bologna, li cataloga
per zona, camere, bagni e ospiti, tiene conto di fiere e concerti e calcola a che prezzo conviene affittare
un appartamento come il tuo. Un'app Android mostra il risultato.

**Installare l'app:** scarica [`bbb.apk`](https://github.com/wkisai95/bbb/raw/apk/bbb.apk) sul telefono e aprilo
(Android 8 o successivo; alla prima installazione Android chiede di consentire le app da questa fonte).
Le versioni sono anche nelle [release](https://github.com/wkisai95/bbb/releases/latest).

## Cosa trovi nell'app

| Sezione | Cosa fa |
|---|---|
| **Prezzi** | Imposti il tuo appartamento (zona, camere, bagni, ospiti, qualità, prezzo minimo, costi) e scegli **Massima resa** o **Massima occupazione**. Calendario dei prossimi 30, 60 o 90 giorni con il prezzo da impostare oggi per ogni notte; toccando una notte vedi dove cade rispetto ai comparabili, la probabilità di venderla e come scende il prezzo se resta libera. Esporta in CSV. |
| **Mercato** | Il catalogo: fascia di prezzo e mediana per zona, camere, bagni, ospiti, quartiere. Toccando una riga, l'elenco degli annunci con il link ad Airbnb. |
| **Eventi** | Livello dei prezzi nei prossimi 12 mesi e calendario di fiere, concerti, ponti, con l'aumento dei prezzi misurato o stimato. Puoi aggiungere i tuoi eventi. |
| **Dati** | Stato dell'ultima scansione, precisione del modello, avvio di una scansione, **backup completo in ZIP**, esportazione e importazione delle impostazioni. |

## Le due strategie

- **Massima resa.** Per ogni notte sceglie il prezzo che rende massimo il margine atteso (prezzo meno costi
  variabili, per la probabilità di vendere), sapendo che nei giorni successivi potrai ancora abbassarlo.
  Lontano dalla data tiene alto, avvicinandosi scende se la notte è ancora libera.
- **Massima occupazione.** Il prezzo più alto che lascia almeno il 90% di probabilità di vendere la notte;
  quando non basta, il minimo della fascia.

In entrambi i casi il prezzo resta attorno al mercato: tra il 10° e il 90° percentile degli appartamenti
simili per quella notte, e comunque non oltre il 25% sotto o il 40% sopra la loro mediana.

## Come funziona

1. **Scansione** (`bnb/scan.py`, ogni giorno alle 03:23 UTC). Per circa 60 finestre di due notti (tutti i
   prossimi 60 giorni, poi fine settimana, infrasettimanali e date con eventi fino a un anno) legge dalla
   ricerca di Airbnb i prezzi degli appartamenti disponibili, riquadro per riquadro, e **quanti sono ancora
   liberi**. A quattro anticipi fissi (1, 7, 14, 28 giorni) legge l'elenco completo di chi è ancora libero.
2. **Catalogo.** Camere, letti e bagni arrivano dalla ricerca; capienza, soggiorno minimo e quartiere dai dati
   aperti di Inside Airbnb o, per gli annunci nuovi, dalla pagina dell'annuncio. La zona viene dalle coordinate.
3. **Prezzo dei comparabili** (`bnb/model.py`). Una regressione separa l'effetto delle caratteristiche (zona,
   camere, bagni, ospiti, recensioni) da quello della data. Finestre sfalsate di un giorno permettono di
   ricavare il prezzo di ogni singola notte.
4. **Eventi.** Elenco curato in `config/events.yaml`, riletto ogni giorno dai calendari di BolognaFiere e
   Bologna Welcome, più le festività e gli eventi che aggiungi. Per le notti già lette vale l'aumento
   misurato; per le altre l'effetto tipico di eventi dello stesso peso, che il modello impara col tempo.
   Se una data costa molto più del normale senza un evento noto, la segnala come «picco rilevato».
5. **Domanda.** Dai conteggi degli alloggi liberi ricava il ritmo con cui ogni data si riempie e la
   probabilità giornaliera che una notte libera venga prenotata. Conta come prenotazione solo il calo
   nelle ultime settimane prima della data: molto più lontano gli alloggi liberi sono meno perché tanti
   calendari non sono ancora aperti, e quello non viene scambiato per domanda.
6. **Sensibilità al prezzo.** Confrontando due letture complete della stessa data, vede chi è sparito
   (prenotato) rispetto al proprio prezzo relativo.
7. **App.** Scarica `public/model.json` dal ramo `data` e fa i conti sul telefono (`app/web/pricing.js`,
   identico a `bnb/pricing.py`: un test confronta i risultati).

**Migliora da solo.** Ogni stima parte da un valore prudente e si sposta verso i dati man mano che si
accumulano: la curva di prenotazione osservata sostituisce quella di partenza in 14 giorni; l'attendibilità
considera piena la domanda dopo 21 giorni di dati e lo storico dopo 45. L'app mostra
un'**attendibilità** (bassa, media, alta) e in Dati l'errore del modello giorno per giorno.

## I dati e il backup

Tutto ciò che viene raccolto è sul ramo [`data`](https://github.com/wkisai95/bbb/tree/data): un file di
prezzi e uno di conteggi per ogni giorno, il catalogo, lo storico, il modello. A ogni scansione viene
aggiornato anche un archivio unico:
[`bbb-backup.zip`](https://github.com/wkisai95/bbb/releases/download/backup/bbb-backup.zip)
(lo stesso che scarichi dall'app in Dati → Scarica il backup completo). Sono CSV e JSON leggibili con Excel
o qualunque programma.

## Avviare una scansione e salvare eventi dall'app

Per leggere i prezzi l'app non ha bisogno di nulla. Per **avviare una scansione** o **salvare su GitHub gli
eventi** che aggiungi serve un token: su github.com → Settings → Developer settings → Fine-grained tokens →
Generate new token, solo sul repository `bbb`, con permessi *Actions: Read and write* e *Contents: Read and
write*. Incollalo in Dati → Token di accesso. In alternativa la scansione si avvia da
[Actions → Scansione → Run workflow](https://github.com/wkisai95/bbb/actions/workflows/scan.yml).

## Regolazioni

- `config/settings.yaml`: date lette, pause tra le richieste, tetti di richieste e di minuti, riquadri della
  mappa, parametri del modello (obiettivo di occupazione, sconto e rincaro massimi, quota di costi).
- `config/events.yaml`: eventi curati, con peso da 1 (minimo) a 5 (Cosmoprof, Cersaie).
- `config/zones.json`: le 23 zone (centri e nomi). Sono approssimate: ogni annuncio va alla zona più vicina,
  e Airbnb sposta le coordinate di 100-150 metri.

## Limiti da conoscere

- **Airbnb non offre un'interfaccia pubblica per questi dati.** Il programma legge la ricerca del sito come
  farebbe un visitatore, una richiesta alla volta con una pausa tra l'una e l'altra, circa mille al giorno.
  Le condizioni d'uso di Airbnb non consentono la raccolta automatica: può smettere di funzionare o essere
  bloccato. In quel caso la scansione si ferma da sola (nessun tentativo di aggirare blocchi) e l'app lo segnala.
- **Il calendario dei singoli annunci non viene letto.** L'occupazione è ricavata da quanti alloggi risultano
  ancora liberi per ogni data: è una stima del mercato, non un dato contabile.
- **I prezzi sono quelli chiesti, non quelli incassati**, per soggiorni di due notti, pulizie e commissioni
  incluse, tassa di soggiorno esclusa.
- **Probabilità e incassi attesi sono stime** che dipendono da ipotesi sul comportamento degli ospiti. Nei
  primi giorni contano molto i valori di partenza; guardare l'attendibilità. Il ritmo di prenotazione è
  quello medio del mercato: chi resta libero fino all'ultimo è spesso chi chiede troppo, quindi per un
  alloggio a prezzo giusto la probabilità reale sotto data tende a essere un po' più alta di quella indicata.
- **GitHub può saltare o sospendere le esecuzioni programmate.** Nelle ore di punta una scansione può partire
  in ritardo o non partire affatto; nei repository pubblici il programma giornaliero viene disattivato dopo
  60 giorni senza attività. I salvataggi quotidiani sul ramo `data` dovrebbero contare come attività, ma non è
  garantito: se l'app avvisa che la scansione non gira da giorni, apri Actions → Scansione su GitHub e
  riattivala, oppure avviala dall'app.
- Solo **appartamenti interi**; le date degli eventi vengono da calendari pubblici e vanno verificate
  (quella di Ultimo allo stadio, giugno 2027, è riportata in modo discorde dalle fonti).

## Per chi mette le mani nel codice

```
bnb/            scanner, modello, prezzi, pubblicazione (Python)
app/web/        interfaccia dell'app (HTML, CSS, JavaScript senza dipendenze)
android/        guscio Android (Kotlin) che include app/web nell'APK
config/         impostazioni, zone, eventi, confini dei quartieri
tests/          test, compresa una prova d'insieme su un mercato simulato di cui si conosce la verità
.github/workflows/scan.yml      scansione giornaliera, ramo data, backup
.github/workflows/android.yml   build e firma dell'APK, release
```

```
pip install -r requirements.txt pytest
python -m pytest tests -q            # 32 test, una ventina di secondi
python -m bnb all --out out          # scansione + modello + file per l'app + backup
python -m bnb model --out out        # solo ricalcolo dai dati già raccolti
```

La chiave che firma l'APK viene creata alla prima build e conservata in una release in bozza (`chiave-firma`,
visibile solo a chi ha accesso in scrittura): non pubblicarla e non cancellarla, altrimenti gli aggiornamenti
dell'app non si installano più sopra la versione precedente.

## Fonti

- Prezzi e disponibilità: ricerca pubblica di airbnb.com, tramite la libreria open source
  [pyairbnb](https://github.com/johnbalvin/pyairbnb).
- Catalogo iniziale, capienze, calendari per la taratura: [Inside Airbnb](https://insideairbnb.com/get-the-data/)
  (CC BY 4.0). Confini dei quartieri: Comune di Bologna, via Inside Airbnb.
- Eventi: [BolognaFiere](https://bolognafiere.it/calendario/),
  [Bologna Welcome](https://www.bolognawelcome.com/it/blog/concerti-da-vedere-a-bologna).
- Carattere dell'app: Archivo (Omnibus-Type, licenza OFL).
