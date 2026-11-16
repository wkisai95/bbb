# bbb · prezzi Airbnb a Bologna

Ogni notte un programma su GitHub legge i prezzi degli appartamenti interi su Airbnb a Bologna, li cataloga
per zona, camere, bagni e ospiti, tiene conto di fiere e concerti e calcola a che prezzo conviene affittare
un appartamento come il tuo. Un'app Android mostra il risultato.

**Installare l'app:** scarica [`bbb.apk`](https://github.com/wkisai95/bbb/raw/apk/bbb.apk) sul telefono e aprilo
(Android 8 o successivo; alla prima installazione Android chiede di consentire le app da questa fonte).
Le versioni sono anche nelle [release](https://github.com/wkisai95/bbb/releases/latest). Dalla 1.0.8 l'app avvisa
quando ne esce una nuova e la scarica con il numero nel nome (per esempio `bbb-1.0.9.apk`); la versione installata
è scritta in fondo alla sezione Dati.

## Cosa trovi nell'app

| Sezione | Cosa fa |
|---|---|
| **Prezzi** | Imposti il tuo appartamento (zona, camere, bagni, ospiti, qualità, prezzo minimo, costi, **portale e commissione**, ospiti che pagano la tassa di soggiorno) e scegli **Massima resa**, **Massima occupazione** o **Aggressiva**, con una tabella che le mette a confronto. Calendario dei prossimi 30, 60, 90 giorni o di un anno con il prezzo da impostare oggi per ogni notte; toccando una notte vedi dove cade rispetto ai comparabili, la probabilità di venderla, **quanto ti resta tolta la commissione, la tassa di soggiorno e chi la riscuote**, il prezzo da mettere sugli altri portali per avere lo stesso netto. I prezzi si possono vedere **al lordo** (quelli da mettere sul portale) o **al netto della commissione**. Esporta in CSV. Con più appartamenti si indica il **proprietario** di ciascuno, e quelli dello stesso proprietario si possono vedere insieme: incasso, commissioni e guadagno sommati, il confronto appartamento per appartamento, i prezzi notte per notte affiancati e un unico CSV. **Resa per proprietario** mostra in una tabella incasso, notti vendute e guadagno previsti di ogni proprietario e il totale, anche sull'anno intero, esportabile in CSV. |
| **Mercato** | Il catalogo: fascia di prezzo e mediana per zona, camere, bagni, ospiti, quartiere. Toccando una riga, l'elenco degli annunci con il link ad Airbnb. In fondo, **chi viene prenotato**: quante prenotazioni sono state rilevate e a che prezzo, rispetto a chi è rimasto libero. |
| **Eventi** | Livello dei prezzi nei prossimi 12 mesi e calendario di fiere, concerti, ponti, con l'aumento dei prezzi misurato o stimato. Puoi aggiungere i tuoi eventi. |
| **Dati** | Stato dell'ultima scansione, precisione del modello, avvio di una scansione, **backup completo in ZIP**, esportazione e importazione delle impostazioni. |

## Le strategie

- **Massima resa.** Per ogni notte sceglie il prezzo che rende massimo il guadagno atteso (prezzo, meno la
  commissione del portale, meno i costi variabili, per la probabilità di vendere), sapendo che nei giorni
  successivi potrai ancora abbassarlo. Lontano dalla data tiene alto, avvicinandosi scende se la notte è
  ancora libera.
- **Massima occupazione.** Il prezzo più alto che lascia almeno il 90% di probabilità di vendere la notte;
  quando non basta, il minimo della fascia.
- **Aggressiva.** Una regola fissa: prezzo di mercato (la mediana degli appartamenti simili per quella notte)
  finché la notte è lontana; se è ancora libera, −10% da 14 giorni prima, −20% da 7 e −30% da 3. Gradini e
  percentuali si cambiano dall'app (Cambia gli sconti) o in `config/settings.yaml` (`aggressive_steps`). Il
  modello ne calcola l'esito atteso come per le altre due, così si possono confrontare.

Nelle prime due il prezzo resta attorno al mercato: tra il 10° e il 90° percentile degli appartamenti simili
per quella notte, e comunque non oltre il 25% sotto o il 40% sopra la loro mediana. L'aggressiva scende sotto
quella fascia di proposito. Nessuna va sotto il prezzo minimo impostato per l'appartamento.

## Come funziona

1. **Scansione** (`bnb/scan.py`, ogni giorno alle 03:23 UTC, con altri due tentativi alle 05:53 e alle 08:23
   se la prima non è partita). Per circa 60 finestre di due notti (tutti i prossimi 60 giorni, poi fine
   settimana, infrasettimanali e date con eventi fino a un anno) legge dalla ricerca di Airbnb i prezzi degli
   appartamenti disponibili, riquadro per riquadro, e **quanti sono ancora liberi**. Per le date entro 30
   giorni legge l'**elenco completo** di chi è ancora libero.
2. **Prenotazioni rilevate** (`bnb/track.py`). La stessa finestra di date torna due giorni dopo: chi era libero
   e non c'è più è stato prenotato per quelle date. Per non scambiare per prenotazione una lettura sfuggita,
   una sparizione conta per intero solo se è confermata dalla lettura successiva; non contano gli annunci
   tolti dal sito (spariscono da tutte le date insieme) né i calendari chiusi in blocco. Da qui vengono il
   ritmo con cui le date si riempiono, il prezzo a cui si viene prenotati e la sensibilità al prezzo.
3. **Catalogo.** Camere, letti e bagni arrivano dalla ricerca; capienza, soggiorno minimo e quartiere dai dati
   aperti di Inside Airbnb o, per gli annunci nuovi, dalla pagina dell'annuncio. La zona viene dalle coordinate.
4. **Prezzo dei comparabili** (`bnb/model.py`). Una regressione separa l'effetto delle caratteristiche (zona,
   camere, bagni, ospiti, recensioni) da quello della data. Finestre sfalsate di un giorno permettono di
   ricavare il prezzo di ogni singola notte.
5. **Eventi.** Elenco curato in `config/events.yaml`, riletto ogni giorno dai calendari di BolognaFiere e
   Bologna Welcome, più le festività e gli eventi che aggiungi. Per le notti già lette vale l'aumento
   misurato; per le altre l'effetto tipico di eventi dello stesso peso, che il modello impara col tempo.
   Se una data costa molto più del normale senza un evento noto, la segnala come «picco rilevato».
6. **Domanda.** Per ogni notte, la probabilità giornaliera che venga prenotata. Entro 30 giorni il ritmo è
   quello misurato seguendo gli alloggi uno per uno; più lontano viene dai conteggi dei liberi, contando come
   prenotazione solo il calo delle ultime settimane (molto prima gli alloggi liberi sono meno perché tanti
   calendari non sono ancora aperti, e quello non viene scambiato per domanda). Il ritmo "tipico" si stima
   sulle notti feriali senza eventi; fine settimana, eventi e feste corrono di più, e di quanto lo dice la
   loro disponibilità rispetto al tipico. Nei primi giorni, prima che ci siano confronti tra letture della
   stessa data, questa stima è la parte più incerta del modello.
7. **Sensibilità al prezzo.** Tra gli alloggi liberi a una lettura, guarda chi è stato prenotato prima della
   successiva rispetto al proprio prezzo, a parità di zona, taglia e qualità.
8. **App.** Scarica `public/model.json` dal ramo `data` e fa i conti sul telefono (`app/web/pricing.js`,
   identico a `bnb/pricing.py`: un test confronta i risultati).

**Migliora da solo.** Ogni stima parte da un valore prudente e si sposta verso i dati man mano che si
accumulano: la curva di prenotazione osservata sostituisce quella di partenza in 14 giorni; l'attendibilità
considera piena la domanda dopo 21 giorni di dati e lo storico dopo 45. L'app mostra
un'**attendibilità** (bassa, media, alta) e in Dati l'errore del modello giorno per giorno.

## Commissioni e tassa di soggiorno

Il prezzo consigliato è quello che vede l'ospite. Il guadagno è calcolato dopo la commissione del portale su
cui vendi, e la massima resa ne tiene conto quando sceglie il prezzo. I valori di partenza, che puoi cambiare
appartamento per appartamento (Prezzi → Modifica) o per tutti in `config/settings.yaml`:

| Portale | Commissione | Con IVA 22% | Tassa di soggiorno |
|---|---|---|---|
| Airbnb | 15,5% (commissione unica, trattenuta dal compenso) | 18,9% | la riscuote Airbnb dall'ospite e la versa al Comune |
| Booking.com | 16,5% (15% più 1,5% per i pagamenti) | 20,1% | la incassi tu dall'ospite e la versi al Comune |
| Vrbo | 8% (indicativa: verifica sul tuo contratto) | 9,8% | la incassi tu |
| Prenotazione diretta | 0% | – | la incassi tu |

**Tassa di soggiorno a Bologna, tariffe 2026** (delibera DG/PRO/2025/283, in vigore fino al 31 dicembre 2026):

- quando l'affitto lo incassa un portale che riscuote l'imposta (Airbnb): **10,5% del prezzo, al massimo 7 €
  a persona a notte**;
- negli altri casi la riscuote chi ospita, a persona a notte secondo il prezzo a persona: 4 € fino a 30,99 €,
  5,80 € fino a 70,99 €, 6,50 € fino a 120,99 €, 7 € oltre;
- non la pagano i minori di 14 anni e i residenti a Bologna; si applica solo alle prime notti consecutive di
  un soggiorno (cinque secondo il regolamento: controlla sul sito del Comune).

La tassa non è un tuo ricavo e non entra nel guadagno: l'app la mostra per sapere quanto paga in più l'ospite
e, sui portali che non la riscuotono, quanto devi incassare e versare tu. Le tariffe cambiano ogni anno:
vanno aggiornate in `config/settings.yaml` (sezione `fees`). Restano a tuo carico la dichiarazione
dell'imposta al Comune e le imposte sul reddito (cedolare secca), che questo programma non calcola.

## Altri portali

Lo scanner legge solo Airbnb. Il 16 novembre 2026 è stata provata, con una sola richiesta per sito e senza
forzare nulla, la pagina di ricerca degli altri portali (`tools/probe_sites.py`, si rilancia da Actions →
Prova dei portali):

| Portale | Esito |
|---|---|
| Booking.com | risponde con una verifica anti-robot |
| Vrbo | «Sei un robot?» |
| Expedia, Hotels.com | il loro `robots.txt` vieta la pagina di ricerca: non richiesta |
| Tripadvisor | verifica anti-robot |
| HomeToGo, CaseVacanza.it | verifica anti-robot |
| Agoda | risponde, ma la pagina è vuota: i prezzi arrivano solo eseguendo il sito in un browser |
| Holidu | risponde. Per Bologna dichiara circa 570 alloggi; quelli della pagina letta erano tutti di grandi gestori professionali. Le sue condizioni vietano la copia automatica dei contenuti: non è stato aggiunto |

Superare quelle verifiche vorrebbe dire aggirare una protezione, e questo programma non lo fa. Gli accessi
ufficiali esistono ma sono riservati: quello di Booking.com (Demand API) richiede un contratto da partner
affiliato. Le commissioni e la tassa di soggiorno degli altri portali entrano comunque nel calcolo, e la scheda
di ogni notte dice che prezzo mettere altrove per avere lo stesso netto.

## I dati e il backup

Tutto ciò che viene raccolto è sul ramo [`data`](https://github.com/wkisai95/bbb/tree/data): un file di
prezzi e uno di conteggi per ogni giorno (più gli alloggi visti liberi senza prezzo, in `data/seen/`), il
catalogo, lo storico, il modello. A ogni scansione viene
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
  mappa, parametri del modello (obiettivo di occupazione, sconto e rincaro massimi, quota di costi),
  commissioni dei portali e tariffe della tassa di soggiorno.
- `config/events.yaml`: eventi curati, con peso da 1 (minimo) a 5 (Cosmoprof, Cersaie).
- `config/zones.json`: le 23 zone (centri e nomi). Sono approssimate: ogni annuncio va alla zona più vicina,
  e Airbnb sposta le coordinate di 100-150 metri.

## Limiti da conoscere

- **Airbnb non offre un'interfaccia pubblica per questi dati.** Il programma legge la ricerca del sito come
  farebbe un visitatore, una richiesta alla volta con una pausa tra l'una e l'altra, circa 1.300 al giorno
  in una quarantina di minuti.
  Le condizioni d'uso di Airbnb non consentono la raccolta automatica: può smettere di funzionare o essere
  bloccato. In quel caso la scansione si ferma da sola (nessun tentativo di aggirare blocchi) e l'app lo segnala.
- **Il calendario dei singoli annunci non viene letto.** Occupazione e prenotazioni sono ricavate da chi
  risulta ancora libero per ogni data: una stima del mercato, non un dato contabile. Un alloggio che sparisce
  è quasi sempre stato prenotato, ma da fuori non si distingue da una data bloccata dal proprietario o da un
  soggiorno minimo alzato; e qualche alloggio sfugge alla lettura (per questo serve la conferma due giorni
  dopo). Le prenotazioni si vedono solo per le date entro 30 giorni.
- **I prezzi sono quelli chiesti, non quelli incassati**, per soggiorni di due notti, pulizie e commissioni
  incluse, tassa di soggiorno esclusa.
- **Probabilità e incassi attesi sono stime** che dipendono da ipotesi sul comportamento degli ospiti. Nei
  primi giorni contano molto i valori di partenza; guardare l'attendibilità. Il ritmo di prenotazione è
  quello medio del mercato: chi resta libero fino all'ultimo è spesso chi chiede troppo, quindi per un
  alloggio a prezzo giusto la probabilità reale sotto data tende a essere un po' più alta di quella indicata.
- **GitHub può saltare o sospendere le esecuzioni programmate.** Nelle ore di punta una scansione può partire
  in ritardo o non partire affatto; nei repository pubblici il programma giornaliero viene disattivato dopo
  60 giorni senza attività. Per questo i tentativi sono tre al giorno (i successivi si fermano subito se la
  scansione di oggi c'è già), e i salvataggi quotidiani sul ramo `data` dovrebbero contare come attività, ma
  non è garantito: se l'app avvisa che la scansione non gira da giorni, apri Actions → Scansione su GitHub e
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
.github/workflows/probe.yml     prova di raggiungibilità degli altri portali (solo a mano)
```

```
pip install -r requirements.txt pytest
python -m pytest tests -q            # 45 test, mezzo minuto
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
- Tassa di soggiorno: [Bologna Welcome](https://www.bolognawelcome.com/it/informazioni/tassa-di-soggiorno) e
  [Comune di Bologna](https://www.comune.bologna.it/servizi/tasse-tributi/imposta-di-soggiorno).
- Carattere dell'app: Archivo (Omnibus-Type, licenza OFL).
