"""Scrive tests/vettori_cifratura.json: esempi fissi del formato bbb-cifrato v1 con cui un'altra app può
controllare la propria implementazione (stesso codice e stesso nonce devono dare esattamente gli stessi byte).

uso: python tools/vettori_cifratura.py
"""
import base64, hashlib, json, os, sys

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)
from bnb import cifrato as C  # noqa: E402


def vettore(nome, byte_codice, nonce, testo):
    codice = C.scrivi_codice(byte_codice)
    chiave, fid = C.chiavi(codice)
    scritto = [codice.lower().replace("-", " ").replace("0", "o").replace("1", "l"), codice.replace("-", "")]
    busta = C.cifra_testo(testo.encode(), codice, nonce)
    return {"nome": nome, "codice": codice, "codice_scritto_diversamente": scritto,
            "codice_hex": byte_codice.hex(), "chiave_hex": chiave.hex(), "id": fid,
            "aad": "bbb-cifrato-v1:" + fid, "nonce_hex": nonce.hex(), "testo": testo, "file": busta}


def main():
    v = [
        vettore("byte da 00 a 0e", bytes(range(15)), bytes(range(12)),
                '{"tipo":"proprietario","proprietario":"Niccolò Bianchi","totale":1234.5}'),
        vettore("codice casuale", hashlib.sha256(b"bbb-vettore-2").digest()[:15], hashlib.sha256(b"nonce-2").digest()[:12],
                '{"tipo":"gestore","versione":1,"appartamenti":[{"id":"a1","name":"Via dell\'Indipendenza","owner":"Zoë"}]}'),
    ]
    altro = C.scrivi_codice(hashlib.sha256(b"bbb-altro").digest()[:15])
    alterato = dict(v[0]["file"])
    dati = bytearray(base64.b64decode(alterato["dati"]))
    dati[0] ^= 1
    alterato["dati"] = base64.b64encode(bytes(dati)).decode()
    out = {
        "descrizione": "Vettori di prova del formato bbb-cifrato v1 (docs/cifratura.md). Ogni implementazione deve: "
                       "ricavare chiave_hex e id dal codice (anche scritto diversamente), cifrare testo con nonce_hex "
                       "ottenendo esattamente file.dati, e decifrare file riottenendo testo. I casi in da_rifiutare "
                       "devono dare errore.",
        "vettori": v,
        "da_rifiutare": [
            {"motivo": "codice di un altro file", "codice": altro, "file": v[0]["file"]},
            {"motivo": "un bit cambiato nei dati", "codice": v[0]["codice"], "file": alterato},
            {"motivo": "codice di 23 caratteri", "codice": v[0]["codice"][:-1], "file": v[0]["file"]},
        ],
    }
    path = os.path.join(RADICE, "tests", "vettori_cifratura.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("scritto", path)
    for x in v:
        print(x["codice"], x["id"])


if __name__ == "__main__":
    main()
