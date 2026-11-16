"""Formato bbb-cifrato v1: implementazione di riferimento in Python.

La specifica completa è in docs/cifratura.md. In breve:

- un *codice* è fatto di 15 byte casuali (120 bit) scritti in base32 di Crockford: 24 caratteri,
  di solito in 6 gruppi da 4 separati da trattini;
- dal codice si ricavano con HKDF-SHA256 (sale "bbb-cifrato-v1") la chiave AES-256 (info "chiave",
  32 byte) e il nome del file (info "file", 8 byte scritti in esadecimale);
- il contenuto (un documento JSON in UTF-8) è cifrato con AES-256-GCM, nonce casuale di 12 byte,
  etichetta di 16 byte in coda al testo cifrato e dati associati "bbb-cifrato-v1:" + nome del file;
- il file salvato è un JSON {"formato", "versione", "id", "nonce", "dati"} in config/cifrati/<id>.json.

Serve il pacchetto `cryptography` (pip install cryptography).
"""
from __future__ import annotations

import base64
import json
import os
import re

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FORMATO = "bbb-cifrato"
VERSIONE = 1
ALFABETO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # base32 di Crockford: niente I, L, O, U
SALE = b"bbb-cifrato-v1"
CARTELLA = "config/cifrati"
BYTE_CODICE = 15


class CodiceNonValido(ValueError):
    """Il testo non è un codice bbb (lunghezza o caratteri sbagliati)."""


class NonSiApre(ValueError):
    """Il codice non apre questo file, oppure il file è stato alterato."""


def scrivi_codice(dati: bytes) -> str:
    """15 byte -> codice di 24 caratteri in gruppi da 4 (es. 7KQ2-M9XA-4F3C-PQ8D-ZX5N-W2RT)."""
    if len(dati) != BYTE_CODICE:
        raise CodiceNonValido("un codice è fatto di 15 byte")
    n = int.from_bytes(dati, "big")
    car = "".join(ALFABETO[(n >> (5 * i)) & 31] for i in range(23, -1, -1))
    return "-".join(car[i:i + 4] for i in range(0, 24, 4))


def leggi_codice(testo: str) -> bytes:
    """Codice scritto da una persona -> 15 byte. Ignora spazi e trattini, maiuscole o minuscole,
    e legge O come 0 e I, L come 1."""
    t = re.sub(r"[\s-]", "", str(testo or "")).upper().replace("O", "0").replace("I", "1").replace("L", "1")
    if len(t) != 24:
        raise CodiceNonValido("il codice deve avere 24 caratteri")
    n = 0
    for c in t:
        v = ALFABETO.find(c)
        if v < 0:
            raise CodiceNonValido(f"carattere non valido nel codice: {c}")
        n = (n << 5) | v
    return n.to_bytes(BYTE_CODICE, "big")


def nuovo_codice() -> str:
    return scrivi_codice(os.urandom(BYTE_CODICE))


def _hkdf(ikm: bytes, info: str, n: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=n, salt=SALE, info=info.encode()).derive(ikm)


def chiavi(codice: str) -> tuple[bytes, str]:
    """Codice -> (chiave AES-256, nome del file in esadecimale)."""
    ikm = leggi_codice(codice)
    return _hkdf(ikm, "chiave", 32), _hkdf(ikm, "file", 8).hex()


def id_file(codice: str) -> str:
    return chiavi(codice)[1]


def percorso(codice_o_id: str) -> str:
    """Percorso del file nella repository, dato il codice o direttamente il nome del file."""
    fid = codice_o_id if re.fullmatch(r"[0-9a-f]{16}", codice_o_id) else id_file(codice_o_id)
    return f"{CARTELLA}/{fid}.json"


def _aad(fid: str) -> bytes:
    return b"bbb-cifrato-v1:" + fid.encode()


def cifra_testo(testo: bytes, codice: str, nonce: bytes | None = None) -> dict:
    """Cifra byte qualsiasi (di solito JSON in UTF-8). Il nonce va lasciato vuoto: si passa solo nei test."""
    chiave, fid = chiavi(codice)
    nonce = nonce if nonce is not None else os.urandom(12)
    if len(nonce) != 12:
        raise ValueError("il nonce è di 12 byte")
    dati = AESGCM(chiave).encrypt(nonce, testo, _aad(fid))
    return {"formato": FORMATO, "versione": VERSIONE, "id": fid,
            "nonce": base64.b64encode(nonce).decode(), "dati": base64.b64encode(dati).decode()}


def decifra_testo(busta: dict, codice: str) -> bytes:
    if not isinstance(busta, dict) or busta.get("formato") != FORMATO or busta.get("versione") != VERSIONE:
        raise NonSiApre("non è un file bbb-cifrato versione 1")
    chiave, fid = chiavi(codice)
    if busta.get("id") != fid:
        raise NonSiApre("questo codice non apre questo file")
    try:
        return AESGCM(chiave).decrypt(base64.b64decode(busta["nonce"]), base64.b64decode(busta["dati"]), _aad(fid))
    except Exception as e:       # etichetta sbagliata: file alterato o codice diverso
        raise NonSiApre("il file è stato alterato o il codice è sbagliato") from e


def cifra(documento, codice: str, nonce: bytes | None = None) -> dict:
    return cifra_testo(json.dumps(documento, ensure_ascii=False, separators=(",", ":")).encode(), codice, nonce)


def decifra(busta: dict, codice: str):
    return json.loads(decifra_testo(busta, codice).decode())


def scrivi_invito(utente: str, repository: str, ramo: str, codice: str) -> str:
    """Invito da mandare al proprietario: dice dove sta il file e con quale codice aprirlo."""
    return f"bbb1:{utente}/{repository}/{ramo}/{codice}"


def leggi_invito(testo: str) -> dict:
    m = re.search(r"bbb1:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/([A-Za-z0-9_./-]+?)/([0-9A-Za-z]{4}(?:-?[0-9A-Za-z]{4}){5})\b", str(testo or ""))
    if not m:
        raise CodiceNonValido("non trovo un invito bbb1 nel testo")
    leggi_codice(m.group(4))
    return {"utente": m.group(1), "repository": m.group(2), "ramo": m.group(3), "codice": m.group(4)}


def indirizzo(utente: str, repository: str, ramo: str, codice: str) -> str:
    """Indirizzo pubblico da cui scaricare il file (non serve nessun token)."""
    return f"https://raw.githubusercontent.com/{utente}/{repository}/{ramo}/{percorso(codice)}"
