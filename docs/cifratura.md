# Formato bbb-cifrato, versione 1

Questo documento spiega come sono cifrati i file con appartamenti, proprietari e stime di guadagno che l'app bbb
salva nella repository, così che un'altra app (per esempio quella dei proprietari, che mostra quanto incasseranno
e quanta tassa di soggiorno dovranno versare) possa aprirli.

Il formato non usa un cifrario inventato: usa **AES-256-GCM** per cifrare e **HKDF-SHA256** per ricavare le chiavi,
due standard che esistono già in tutte le piattaforme (Android, iOS, browser, Python, Java). Quello che il formato
definisce è come si scrivono i codici, come se ne ricavano chiave e nome del file, e com'è fatto il file salvato.

## In breve

1. Ogni persona che deve leggere un file ha un **codice** di 24 caratteri, per esempio `7KQ2-M9XA-4F3C-PQ8D-ZX5N-W2RT`.
2. Dal codice si ricavano la **chiave** AES-256 e il **nome del file** (16 caratteri esadecimali).
3. Il file è `config/cifrati/<nome>.json` sul ramo principale della repository: è pubblico, ma senza codice
   non si legge e dal nome non si capisce di chi è.
4. Dentro c'è un documento JSON cifrato con AES-256-GCM.

Ci sono due tipi di file:

| Tipo | Chi ha il codice | Cosa contiene |
|---|---|---|
| `gestore` | chi gestisce gli appartamenti (l'app bbb) | tutti gli appartamenti, i proprietari con i loro codici, tutte le stime |
| `proprietario` | un proprietario | solo i suoi appartamenti, le sue stime e la tassa di soggiorno da versare |

## 1. Il codice

- È fatto di **15 byte casuali** (120 bit), presi da un generatore crittografico (`crypto.getRandomValues`,
  `SecureRandom`, `os.urandom`).
- Si scrive con l'alfabeto base32 di Crockford, `0123456789ABCDEFGHJKMNPQRSTVWXYZ` (niente I, L, O, U):
  i 120 bit, dal più significativo, diventano 24 caratteri da 5 bit ciascuno.
- Si mostra in 6 gruppi da 4 separati da trattini: `XXXX-XXXX-XXXX-XXXX-XXXX-XXXX`.
- Quando lo si legge da una persona: si tolgono spazi e trattini, si passa in maiuscolo, `O` vale `0`, `I` e `L`
  valgono `1`. Se non restano esattamente 24 caratteri dell'alfabeto, il codice non è valido.

Con 120 bit casuali non serve una derivazione lenta: provare tutti i codici è impossibile anche avendo il file.

## 2. Chiave e nome del file

Con HKDF-SHA256 (RFC 5869), usando come materiale i 15 byte del codice e come sale i byte UTF-8 di
`bbb-cifrato-v1`:

| Cosa | info | Lunghezza |
|---|---|---|
| chiave AES-256 | `chiave` | 32 byte |
| nome del file | `file` | 8 byte, scritti in esadecimale minuscolo (16 caratteri) |

Siccome servono al massimo 32 byte, HKDF si riduce a due HMAC-SHA256, facili da scrivere ovunque:

```
prk    = HMAC-SHA256(chiave = "bbb-cifrato-v1", messaggio = byte del codice)
chiave = HMAC-SHA256(chiave = prk, messaggio = "chiave" || 0x01)            32 byte
nome   = HMAC-SHA256(chiave = prk, messaggio = "file"   || 0x01)[0..8]      in esadecimale
```

## 3. Cifratura

- Algoritmo: AES-256-GCM, etichetta di 128 bit.
- Nonce: 12 byte casuali, **nuovi a ogni salvataggio** (mai riusare lo stesso nonce con la stessa chiave).
- Dati associati (AAD): i byte UTF-8 di `bbb-cifrato-v1:` seguiti dal nome del file, per esempio
  `bbb-cifrato-v1:3e78da9d20d34ee0`. Così un file rinominato o spostato non si apre.
- Testo in chiaro: un documento JSON in UTF-8.
- Il risultato è il testo cifrato con l'etichetta di 16 byte in coda (com'è normale in WebCrypto, Java e Python).

## 4. Il file salvato

```json
{
 "formato": "bbb-cifrato",
 "versione": 1,
 "id": "3e78da9d20d34ee0",
 "nonce": "AAECAwQFBgcICQoL",
 "dati": "gYkEnxnkRCmOF+JuwSXn7Oi+eU8u9EcTc03E44P1xhfEm/CUOLJ0HitGPZzYgvl+..."
}
```

| Campo | Contenuto |
|---|---|
| `formato` | sempre `bbb-cifrato` |
| `versione` | `1`; una versione diversa va rifiutata |
| `id` | nome del file ricavato dal codice; deve coincidere con quello calcolato, altrimenti il codice è di un altro file |
| `nonce` | i 12 byte del nonce in base64 standard (con `=` finali se servono) |
| `dati` | testo cifrato più etichetta, in base64 standard |

Si trova in `config/cifrati/<id>.json` sul ramo principale. Per scaricarlo non serve nessun token:

```
https://raw.githubusercontent.com/<utente>/<repository>/<ramo>/config/cifrati/<id>.json
```

GitHub può tenere questa copia in cache per qualche minuto. Se serve la versione appena salvata si può usare
l'API `GET https://api.github.com/repos/<utente>/<repository>/contents/config/cifrati/<id>.json?ref=<ramo>`
con l'intestazione `Accept: application/vnd.github.raw+json`.

## 5. L'invito

L'app bbb manda al proprietario un testo con dentro un invito che dice dove sta il file e con quale codice aprirlo:

```
bbb1:<utente>/<repository>/<ramo>/<codice>
bbb1:wkisai95/bbb/main/7KQ2-M9XA-4F3C-PQ8D-ZX5N-W2RT
```

L'app dei proprietari può cercare `bbb1:` nel testo incollato, oppure chiedere solo il codice e usare una
repository fissa.

## 6. Come si apre un file, passo per passo

1. Leggi il codice (o l'invito) e trasformalo in 15 byte (sezione 1).
2. Ricava chiave e nome del file (sezione 2).
3. Scarica `config/cifrati/<nome>.json`. Se non c'è, il codice è sbagliato o è stato sostituito.
4. Controlla `formato`, `versione` e che `id` sia uguale al nome calcolato.
5. Decifra `dati` con AES-256-GCM, il `nonce` del file e i dati associati della sezione 3. Se l'etichetta non torna,
   il file è stato alterato o il codice è sbagliato: mostra un errore, non dati parziali.
6. Leggi il JSON e guarda `tipo`.

## 7. Contenuto del file di un proprietario

```json
{
 "tipo": "proprietario",
 "proprietario": "Mario Rossi",
 "versione": 1,
 "salvato": "2026-11-16T17:33:08+01:00",
 "dati_del": "2026-11-16",
 "dal": "2026-11-16",
 "valuta": "EUR",
 "periodi": [30, 60, 90, 365],
 "strategie": [
  {"id": "resa", "nome": "Massima resa"},
  {"id": "occupazione", "nome": "Massima occupazione"},
  {"id": "aggressiva", "nome": "Aggressiva", "sconti": [[14, 10], [7, 20], [3, 30]]}
 ],
 "strategia_gestore": "resa",
 "appartamenti": [
  {
   "id": "a2",
   "nome": "Bolognina due camere",
   "zona": "Bolognina",
   "camere": 2, "bagni": 1, "ospiti": 4, "qualita": "media",
   "portale": "Booking.com",
   "commissione_percentuale": 16.5,
   "iva_sulla_commissione": true,
   "tassa_soggiorno": "proprietario",
   "stime": {
    "resa": {
     "30": {"notti": 30, "notti_vendute": 13.3, "prezzo_medio": 160, "incasso_lordo": 1872, "commissioni": 377,
            "costi": 464, "guadagno": 1031, "tassa_soggiorno_da_versare": 239, "tassa_soggiorno_del_portale": 0},
     "60": {"…": "…"}, "90": {"…": "…"}, "365": {"…": "…"}
    },
    "occupazione": {"…": "…"},
    "aggressiva": {"…": "…"}
   }
  }
 ],
 "totali": {"resa": {"30": {"…": "…"}}, "occupazione": {"…": "…"}, "aggressiva": {"…": "…"}}
}
```

| Campo | Significato |
|---|---|
| `salvato` | quando l'app del gestore ha scritto il file (ISO 8601 con fuso orario) |
| `dati_del` | giorno dei prezzi di mercato su cui si basano le stime |
| `dal` | prima notte delle stime; ogni periodo conta le notti da lì in avanti |
| `periodi` | lunghezze dei periodi in notti; sono le chiavi (come testo) degli oggetti in `stime` e `totali` |
| `strategie` | le strategie di prezzo calcolate; `sconti` sono coppie [giorni prima, sconto %] della strategia aggressiva |
| `strategia_gestore` | la strategia che il gestore stava usando quando ha salvato (un'indicazione, non un impegno) |
| `tassa_soggiorno` | `portale`: la riscuote e la versa il portale (Airbnb); `proprietario`: va riscossa dagli ospiti e versata al Comune |
| `commissione_percentuale`, `iva_sulla_commissione` | commissione del portale e se ci si aggiunge l'IVA |

Ogni stima (per appartamento o nei totali, per strategia e periodo) ha questi numeri, in euro arrotondati
tranne `notti_vendute`:

| Campo | Significato |
|---|---|
| `notti` | notti del periodo; nei totali è la somma delle notti di tutti gli appartamenti |
| `notti_vendute` | notti che ci si aspetta di vendere tra quelle ancora libere oggi (una cifra decimale) |
| `prezzo_medio` | prezzo medio a notte proposto oggi; nei totali è la media pesata sulle notti |
| `incasso_lordo` | quanto pagano gli ospiti per le notti vendute, senza la tassa di soggiorno |
| `commissioni` | commissioni dei portali, IVA compresa quando dovuta |
| `costi` | costi per notte venduta (pulizie e simili) indicati dal gestore |
| `guadagno` | `incasso_lordo − commissioni − costi` |
| `tassa_soggiorno_da_versare` | tassa di soggiorno che il proprietario deve riscuotere e versare |
| `tassa_soggiorno_del_portale` | tassa di soggiorno che riscuote e versa il portale (solo informativa) |

Sono **stime**: dipendono dai prezzi di mercato del giorno e da quante notti si prevede di vendere. Le imposte
sul reddito (per esempio la cedolare secca) non sono calcolate qui: l'app dei proprietari può calcolarle da
`incasso_lordo` con le regole e le aliquote in vigore.

L'app dei proprietari deve ignorare i campi che non conosce: le versioni future potranno aggiungerne.

## 8. Contenuto del file del gestore

Stessi campi comuni (`versione`, `salvato`, `dati_del`, `dal`, `valuta`, `periodi`, `strategie`,
`strategia_gestore`), più:

| Campo | Contenuto |
|---|---|
| `app` | versione dell'app che ha salvato |
| `appartamenti` | gli appartamenti così come li tiene l'app (zona, camere, bagni, ospiti, qualità, prezzo minimo, costo per notte, portale, commissione, IVA, chi paga la tassa, proprietario…) |
| `proprietari` | per ognuno: `nome`, `codice`, `file` (nome del suo file), `appartamenti` (gli id) |
| `impostazioni` | strategia, sconti della strategia aggressiva, vista dei prezzi (lordi o netti) |
| `stime` | le stime di ogni appartamento, per id, come nella sezione 7 |
| `totali` | i totali di tutti gli appartamenti |
| `file` | i nomi di tutti i file scritti con questo salvataggio |

## 9. Vettori di prova

[`tests/vettori_cifratura.json`](../tests/vettori_cifratura.json) contiene esempi fissi: codice, byte del codice,
chiave, nome del file, nonce, testo e file cifrato atteso, più tre file che devono essere rifiutati. Un'altra app
è compatibile se:

- ricava esattamente `chiave_hex` e `id` dal codice, anche scritto come in `codice_scritto_diversamente`;
- cifrando `testo` con `nonce_hex` ottiene esattamente `file.dati`;
- decifrando `file` ottiene esattamente `testo`;
- dà errore su tutti i casi in `da_rifiutare`.

Il primo vettore, per controllare a mano:

| | |
|---|---|
| byte del codice | `000102030405060708090a0b0c0d0e` |
| codice | `000G-40R4-0M30-E209-185G-R38E` |
| chiave | `15f3374c5d7f330caf9d77b2ad725a7df37322e67804a34269aed80a5cbd7a52` |
| nome del file | `3e78da9d20d34ee0` |
| nonce | `000102030405060708090a0b` |
| testo | `{"tipo":"proprietario","proprietario":"Niccolò Bianchi","totale":1234.5}` |

## 10. Implementazioni di riferimento

| Linguaggio | File | Librerie |
|---|---|---|
| JavaScript (browser, WebView, node) | [`app/web/cifrato.js`](../app/web/cifrato.js) | WebCrypto (`crypto.subtle`: HKDF, AES-GCM) |
| Python | [`bnb/cifrato.py`](../bnb/cifrato.py), da riga di comando [`tools/apri_cifrato.py`](../tools/apri_cifrato.py) | `cryptography` |
| Java e Android | [`docs/esempi/BbbCifrato.java`](esempi/BbbCifrato.java) | solo la libreria standard (`Mac` HmacSHA256, `Cipher` AES/GCM/NoPadding), Android 8 o successivo |

Su iOS servono `CryptoKit`: `HKDF<SHA256>.deriveKey` e `AES.GCM.open` con `authenticating:` per i dati associati.
I test in `tests/test_cifrato.py` controllano che le tre implementazioni diano gli stessi byte sui vettori e che i
file scritti da una si aprano con le altre.

Per aprire un file dal computer:

```
pip install cryptography
python tools/apri_cifrato.py "bbb1:wkisai95/bbb/main/XXXX-XXXX-XXXX-XXXX-XXXX-XXXX" --scarica
```

## 11. Cosa protegge e cosa no

- **Protegge** il contenuto: senza il codice giusto non si legge nulla, e un file alterato non si apre.
- **Separa** i proprietari: il codice di uno non apre i file degli altri né quello del gestore.
- **Non nasconde** che i file esistono, quanti sono, quanto sono grandi e quando cambiano: la repository è pubblica.
- **Cronologia:** git tiene le versioni vecchie dei file. Un codice sostituito («Nuovo codice» nell'app) non apre
  più i dati nuovi, ma apre ancora le versioni passate del suo file rimaste nella cronologia.
- **Il codice è il segreto:** chi lo ha vede i dati. Va mandato solo alla persona giusta e, se finisce in mani
  sbagliate, va sostituito. Il codice del gestore apre tutto: va conservato al sicuro.
