# Dati raccolti (ramo `data`)

Questo ramo è scritto in automatico dalla scansione giornaliera. Non modificarlo a mano.

| Percorso | Contenuto |
|---|---|
| `data/listings.csv` | catalogo degli annunci: zona, quartiere, camere, letti, bagni, ospiti, recensioni |
| `data/prices/AAAA-MM-GG.csv.gz` | prezzi letti quel giorno: annuncio, check-in, notti, prezzo totale, prezzo pieno se scontato |
| `data/counts/AAAA-MM-GG.csv` | alloggi disponibili per finestra di date: intera città, per taglia e per riquadro |
| `data/runs/AAAA-MM-GG.json` | diario della scansione: richieste, errori, durata |
| `data/history/` | storico: livello dei prezzi per notte, previsioni fatte, qualità del modello giorno per giorno |
| `data/events_auto.json` | eventi letti dai calendari pubblici |
| `public/model.json` | modello di prezzo usato dall'app |
| `public/listings.json` | catalogo compatto per la pagina Mercato dell'app |
| `public/status.json` | stato dell'ultima esecuzione |

I prezzi sono in euro, per l'intero soggiorno campione, tutto incluso per l'ospite (pulizie e commissioni),
tassa di soggiorno esclusa. `orig` è il prezzo pieno quando Airbnb mostra uno sconto.
