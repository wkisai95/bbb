"""File per l'app (out/public), storico della qualità del modello, archivio di backup."""
from __future__ import annotations

import os
import zipfile
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import __version__
from .config import CONFIG_DIR
from .store import Store

LISTING_FIELDS = ["id", "zone", "bedrooms", "baths", "guests", "guests_est", "price", "rating", "reviews", "fav", "lat", "lon", "name",
                  "booked", "booked_price"]     # finestre prenotate negli ultimi 30 giorni e prezzo mediano a notte

DATA_README = """# Dati raccolti (ramo `data`)

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
"""


def _upsert(old: pd.DataFrame, new: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if old is None or old.empty:
        return new
    out = pd.concat([old, new], ignore_index=True)
    return out.drop_duplicates(keys, keep="last").sort_values(keys).reset_index(drop=True)


def update_history(store: Store, model: dict, today: date, run: dict | None) -> dict:
    """Tiene traccia di cosa era stato previsto e di come è andata, per misurare l'errore nel tempo."""
    t0 = date.fromisoformat(model["start"])
    # livello "finale" delle notti che stanno per passare
    rows = []
    for k in (0, 1):
        if k < len(model["idx"]):
            rows.append({"date": (t0 + timedelta(days=k)).isoformat(), "idx": model["idx"][k], "base": model["base"][k],
                         "cov": model["cov"][k], "u": model["occ_now"][k]})
    nights = _upsert(store.load_history("nights"), pd.DataFrame(rows), ["date"])
    store.save_history("nights", nights)
    # previsioni a 7, 14 e 28 giorni
    rows = []
    for lead in (7, 14, 28):
        if lead < len(model["idx"]):
            rows.append({"date": (t0 + timedelta(days=lead)).isoformat(), "lead": lead, "made": today.isoformat(),
                         "idx": model["idx"][lead], "u_fin": model["occ_fin"][lead], "cov": model["cov"][lead]})
    fc = _upsert(store.load_history("forecast"), pd.DataFrame(rows), ["date", "lead"])
    store.save_history("forecast", fc)
    # confronto previsioni / realtà
    forecast = {}
    if not fc.empty and not nights.empty:
        j = fc.merge(nights[nights["cov"] >= 6], on="date", suffixes=("_f", "_r"))
        for lead, g in j.groupby("lead"):
            g = g[g["cov_f"] >= 6]
            if len(g) >= 3:
                forecast[str(int(lead))] = {
                    "n": int(len(g)),
                    "price_err": round(float(np.median(np.abs(np.exp(g["idx_f"] - g["idx_r"]) - 1))), 3),
                    "occ_err": round(float(np.median(np.abs(g["u_fin"] - g["u"]))), 3),
                }
    rel = model["reliability"]
    m = {"date": today.isoformat(), "scan_days": rel["scan_days"], "n_obs": rel["n_obs"], "n_listings": rel["n_listings"],
         "fit_mdape": rel["fit_mdape"], "seg_mdape": rel["seg_mdape"], "beta": model["beta"],
         "beta_weight": rel["beta"]["weight"], "beta_pairs": rel["beta"]["pairs"], "curve_n": rel["curve_n"],
         "fc7_price": (forecast.get("7") or {}).get("price_err"), "fc14_price": (forecast.get("14") or {}).get("price_err"),
         "fc7_occ": (forecast.get("7") or {}).get("occ_err"),
         "requests": (run or {}).get("requests"), "errors": (run or {}).get("errors"), "prices": (run or {}).get("prices")}
    metrics = _upsert(store.load_history("metrics"), pd.DataFrame([m]), ["date"])
    store.save_history("metrics", metrics)
    tail = metrics.tail(90)
    return {"forecast": forecast,
            "history": [{"date": r["date"], "seg_mdape": None if pd.isna(r["seg_mdape"]) else round(float(r["seg_mdape"]), 4),
                         "fit_mdape": None if pd.isna(r["fit_mdape"]) else round(float(r["fit_mdape"]), 4),
                         "n_obs": int(r["n_obs"]), "beta": round(float(r["beta"]), 3)} for r in tail.to_dict("records")]}


def publish(store: Store, model: dict | None, today: date, run: dict | None, cfg: dict, log=print) -> dict:
    state = store.load_state()
    warnings = []
    if run and run.get("stopped"):
        warnings.append(f"Ultima scansione interrotta: {run['stopped']}")
    status = {
        "app_data_version": 1, "program": __version__, "generated": today.isoformat(), "city": cfg["city"],
        "last_scan": state.get("last_scan"), "run": state.get("last_run"), "inside_airbnb": state.get("inside_airbnb"),
        "has_model": bool(model), "warnings": warnings,
    }
    if model:
        listing_rows = model.pop("_listings", [])
        extra = update_history(store, model, today, run)
        model["reliability"]["forecast"] = extra["forecast"]
        model["reliability"]["history"] = extra["history"]
        med = (model["market"].get("all") or {}).get("med")
        if med and not (40 <= med <= 400):
            warnings.append(f"Prezzo mediano insolito ({med} €/notte): controllare la lettura dei prezzi")
        store.write_json("public/model.json", model, compact=True)
        store.write_json("public/listings.json", {"generated": today.isoformat(), "fields": LISTING_FIELDS, "rows": listing_rows}, compact=True)
        status.update(scan_days=model["reliability"]["scan_days"], listings=len(listing_rows),
                      n_obs=model["reliability"]["n_obs"], median_price=med)
    else:
        warnings.append("Dati ancora insufficienti per stimare i prezzi")
    store.write_json("public/status.json", status)
    with open(os.path.join(store.root, "README.md"), "w", encoding="utf-8") as f:
        f.write(DATA_README)
    return status


def make_backup(store: Store, dest: str, log=print) -> str:
    """Un solo file ZIP con tutti i dati raccolti, il modello e la configurazione."""
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    n = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for base in ("data", "public"):
            root = os.path.join(store.root, base)
            for dirpath, _dirs, files in os.walk(root):
                for fn in sorted(files):
                    p = os.path.join(dirpath, fn)
                    z.write(p, os.path.relpath(p, store.root))
                    n += 1
        for fn in ("events.yaml", "events_custom.json", "zones.json", "settings.yaml", "quartieri.geojson"):
            p = os.path.join(CONFIG_DIR, fn)
            if os.path.exists(p):
                z.write(p, os.path.join("config", fn))
                n += 1
        z.writestr("LEGGIMI.txt", DATA_README + "\nPer ripristinare: copiare `data/` e `public/` nel ramo `data` del repository.\n")
    log(f"backup: {n} file, {os.path.getsize(dest) / 1e6:.1f} MB -> {dest}")
    return dest
