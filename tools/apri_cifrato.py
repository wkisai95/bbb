"""Apre un file bbb-cifrato con il codice del gestore o di un proprietario e stampa il contenuto in chiaro.

uso:
  python tools/apri_cifrato.py CODICE                     cerca il file in config/cifrati di questa cartella
  python tools/apri_cifrato.py "bbb1:utente/repo/main/CODICE" --scarica   lo scarica da GitHub
  python tools/apri_cifrato.py CODICE --file percorso.json [--esci risultato.json]
  python tools/apri_cifrato.py CODICE --id                 stampa solo il nome del file

Serve il pacchetto cryptography (pip install cryptography).
"""
import argparse, json, os, sys, urllib.request

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)
from bnb import cifrato as C  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Apre un file bbb-cifrato")
    ap.add_argument("codice", help="codice di 24 caratteri o invito bbb1:...")
    ap.add_argument("--file", help="file da aprire (se manca: config/cifrati/<id>.json)")
    ap.add_argument("--scarica", action="store_true", help="scarica il file da GitHub (serve un invito o --repo)")
    ap.add_argument("--repo", help="utente/repository/ramo, se non usi un invito", default="")
    ap.add_argument("--id", action="store_true", help="stampa solo il nome del file")
    ap.add_argument("--esci", help="salva il contenuto in chiaro in questo file invece di stamparlo")
    a = ap.parse_args()

    dove = None
    codice = a.codice
    if a.codice.strip().startswith("bbb1:"):
        inv = C.leggi_invito(a.codice)
        codice, dove = inv["codice"], (inv["utente"], inv["repository"], inv["ramo"])
    elif a.repo:
        utente, repo, ramo = (a.repo.split("/", 2) + ["main"])[:3]
        dove = (utente, repo, ramo)
    try:
        fid = C.id_file(codice)
    except C.CodiceNonValido as e:
        sys.exit(f"Codice non valido: {e}")
    if a.id:
        print(fid)
        return
    if a.scarica:
        if not dove:
            sys.exit("Per scaricare serve un invito bbb1:... oppure --repo utente/repository/ramo")
        url = C.indirizzo(*dove, codice)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                busta = json.load(r)
        except Exception as e:
            sys.exit(f"Non riesco a scaricare {url}: {e}")
    else:
        path = a.file or os.path.join(RADICE, C.percorso(fid))
        try:
            with open(path, encoding="utf-8") as f:
                busta = json.load(f)
        except FileNotFoundError:
            sys.exit(f"Non trovo {path}: controlla il codice o usa --scarica")
    try:
        doc = C.decifra(busta, codice)
    except C.NonSiApre as e:
        sys.exit(f"Il file non si apre: {e}")
    testo = json.dumps(doc, ensure_ascii=False, indent=1)
    if a.esci:
        with open(a.esci, "w", encoding="utf-8") as f:
            f.write(testo + "\n")
        print("salvato", a.esci)
    else:
        print(testo)


if __name__ == "__main__":
    main()
