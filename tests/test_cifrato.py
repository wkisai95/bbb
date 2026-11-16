"""Formato bbb-cifrato v1: le tre implementazioni (Python, JavaScript dell'app, Java di esempio) danno gli stessi
byte sui vettori di prova e si aprono i file a vicenda."""
import json, os, shutil, subprocess, tempfile

import pytest

pytest.importorskip("cryptography")
from bnb import cifrato as C  # noqa: E402

QUI = os.path.dirname(os.path.abspath(__file__))
RADICE = os.path.dirname(QUI)
VETTORI = os.path.join(QUI, "vettori_cifratura.json")
JS = os.path.join(QUI, "js_cifrato.js")
JAVA = os.path.join(RADICE, "docs", "esempi", "BbbCifrato.java")


def _vettori():
    with open(VETTORI, encoding="utf-8") as f:
        return json.load(f)


def test_vettori_python():
    v = _vettori()
    for x in v["vettori"]:
        chiave, fid = C.chiavi(x["codice"])
        assert chiave.hex() == x["chiave_hex"] and fid == x["id"]
        assert all(C.id_file(c) == x["id"] for c in x["codice_scritto_diversamente"])
        assert C.scrivi_codice(bytes.fromhex(x["codice_hex"])) == x["codice"]
        assert C.cifra_testo(x["testo"].encode(), x["codice"], bytes.fromhex(x["nonce_hex"])) == x["file"]
        assert C.decifra_testo(x["file"], x["codice"]).decode() == x["testo"]
    for y in v["da_rifiutare"]:
        with pytest.raises((C.NonSiApre, C.CodiceNonValido)):
            C.decifra_testo(y["file"], y["codice"])


def test_vettori_javascript():
    r = json.loads(subprocess.run(["node", JS, "vettori", VETTORI], capture_output=True, text=True, check=True).stdout)
    assert all(all(v for k, v in x.items() if k != "nome") for x in r["vettori"]), r
    assert all(x["rifiutato"] for x in r["rifiutati"]), r


def test_vettori_java():
    if not shutil.which("javac"):
        pytest.skip("javac non disponibile")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["javac", "-d", d, JAVA], capture_output=True, check=True)
        run = lambda *a: subprocess.run(["java", "-cp", d, "BbbCifrato", *a], capture_output=True, text=True, encoding="utf-8")
        for x in _vettori()["vettori"]:
            f = x["file"]
            assert run("id", x["codice"]).stdout.strip() == x["id"]
            assert run("apri", x["codice"], f["id"], f["nonce"], f["dati"]).stdout.rstrip("\n") == x["testo"]
            assert json.loads(run("chiudi", x["codice"], x["nonce_hex"], x["testo"]).stdout) == f
        y = _vettori()["da_rifiutare"][1]
        assert run("apri", y["codice"], y["file"]["id"], y["file"]["nonce"], y["file"]["dati"]).returncode != 0


def test_python_e_javascript_si_aprono_i_file(tmp_path):
    doc = {"tipo": "proprietario", "versione": 1, "proprietario": "Zoë D'Àngelo",
           "appartamenti": [{"nome": "Via Saragozza", "stime": {"resa": {"30": {"guadagno": 961, "notti_vendute": 12.6}}}}]}
    codice = C.nuovo_codice()
    # Python cifra, JavaScript apre
    p = tmp_path / "da_python.json"
    p.write_text(json.dumps(C.cifra(doc, codice)), encoding="utf-8")
    out = subprocess.run(["node", JS, "apri", codice, str(p)], capture_output=True, check=True).stdout.decode()
    assert json.loads(out) == doc
    # JavaScript cifra, Python apre
    t = tmp_path / "testo.json"
    t.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    busta = json.loads(subprocess.run(["node", JS, "cifra", codice, str(t)], capture_output=True, check=True).stdout)
    assert busta["id"] == C.id_file(codice) and C.decifra(busta, codice) == doc
    # due cifrature dello stesso testo non si somigliano (nonce nuovo ogni volta)
    assert C.cifra(doc, codice)["dati"] != C.cifra(doc, codice)["dati"]


def test_codici_e_inviti():
    r = json.loads(subprocess.run(["node", JS, "varie"], capture_output=True, text=True, check=True).stdout)
    assert r["formato"] and r["diversi"] and r["andata_ritorno"] and not any(r["sbagliati"])
    assert r["invito"] == {"utente": "wkisai95", "repository": "bbb", "ramo": "main", "codice": r["codice0"]}
    c = C.nuovo_codice()
    assert len(c) == 29 and C.leggi_codice(c.lower().replace("-", " ")) == C.leggi_codice(c)
    inv = C.leggi_invito("Il tuo codice: " + C.scrivi_invito("wkisai95", "bbb", "main", c) + ".")
    assert inv == {"utente": "wkisai95", "repository": "bbb", "ramo": "main", "codice": c}
    assert C.percorso(c) == f"config/cifrati/{C.id_file(c)}.json"
    for sbagliato in ("", "ABCD", c + "Z", c[:-1] + "U"):
        with pytest.raises(C.CodiceNonValido):
            C.leggi_codice(sbagliato)
