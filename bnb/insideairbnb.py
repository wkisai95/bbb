"""Inside Airbnb: dati aperti trimestrali (CC BY 4.0), https://insideairbnb.com/get-the-data/

Servono a partire con un catalogo già completo: capienza (ospiti), camere, bagni, soggiorno
minimo e quartiere di ogni annuncio, senza doverli leggere uno per uno da Airbnb.
"""
from __future__ import annotations

import gzip
import io
import re

import numpy as np
import pandas as pd

INDEX_URL = "https://insideairbnb.com/get-the-data/"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
PATH = "italy/emilia-romagna/bologna"


def find_snapshot(index_html: str, path: str = PATH) -> dict | None:
    """{'date': '2026-09-19', 'listings': url, 'calendar': url} dell'ultima istantanea."""
    urls = set(re.findall(rf"https://data\.insideairbnb\.com/{re.escape(path)}/(\d{{4}}-\d{{2}}-\d{{2}})/data/listings\.csv\.gz", index_html))
    if not urls:
        return None
    d = max(urls)
    base = f"https://data.insideairbnb.com/{path}/{d}"
    return {"date": d, "listings": f"{base}/data/listings.csv.gz", "calendar": f"{base}/data/calendar.csv.gz"}


def parse_baths(text) -> float | None:
    """'1.5 baths' -> 1.5 · 'Shared half-bath' -> 0.5 · '1 private bath' -> 1.0"""
    if not isinstance(text, str) or not text.strip():
        return None
    m = re.search(r"\d+(?:\.\d+)?", text)
    if m:
        return float(m.group())
    return 0.5 if "half" in text.lower() else None


def parse_listings(csv_bytes: bytes) -> pd.DataFrame:
    raw = gzip.decompress(csv_bytes) if csv_bytes[:2] == b"\x1f\x8b" else csv_bytes
    df = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
    out = pd.DataFrame({"id": pd.to_numeric(df["id"], errors="coerce")})
    out["name"] = df.get("name", "")
    out["lat"] = pd.to_numeric(df.get("latitude"), errors="coerce")
    out["lon"] = pd.to_numeric(df.get("longitude"), errors="coerce")
    out["room_type"] = df.get("room_type", "")
    out["guests"] = pd.to_numeric(df.get("accommodates"), errors="coerce")
    out["bedrooms"] = pd.to_numeric(df.get("bedrooms"), errors="coerce")
    out["beds"] = pd.to_numeric(df.get("beds"), errors="coerce")
    out["baths"] = df.get("bathrooms_text", pd.Series([""] * len(df))).map(parse_baths)
    out["min_nights"] = pd.to_numeric(df.get("minimum_nights"), errors="coerce")
    out["rating"] = pd.to_numeric(df.get("review_scores_rating"), errors="coerce")
    out["reviews"] = pd.to_numeric(df.get("number_of_reviews"), errors="coerce").fillna(0)
    out["district"] = df.get("neighbourhood_cleansed", "")
    out["has_availability"] = df.get("has_availability", "") == "t"
    out["kind"] = df.get("property_type", "")
    out = out.dropna(subset=["id"])
    out["id"] = out["id"].astype("int64")
    return out.drop_duplicates("id").reset_index(drop=True)


def download(log=print) -> tuple[pd.DataFrame, str] | None:
    """Scarica l'ultima istantanea di Bologna. None se non raggiungibile."""
    import requests

    try:
        idx = requests.get(INDEX_URL, headers=UA, timeout=60)
        snap = find_snapshot(idx.text)
        if not snap:
            log("Inside Airbnb: nessuna istantanea di Bologna trovata nella pagina dei dati")
            return None
        r = requests.get(snap["listings"], headers=UA, timeout=180)
        r.raise_for_status()
        df = parse_listings(r.content)
        log(f"Inside Airbnb: istantanea del {snap['date']}, {len(df)} annunci")
        return df, snap["date"]
    except Exception as e:  # noqa: BLE001
        log(f"Inside Airbnb non raggiungibile: {e!r}"[:300])
        return None


def window_factor(calendar_bytes: bytes, listing_ids, nights: int = 2) -> dict | None:
    """Quante "notti indipendenti" pesa una finestra di più notti (nu), dai calendari di Inside Airbnb.

    La ricerca di Airbnb dice se un alloggio è libero per l'intera finestra (es. 2 notti); a chi affitta
    interessa la singola notte. Se le prenotazioni coprissero sempre entrambe le notti, finestra e notte
    si riempirebbero allo stesso ritmo (nu = 1); se le notti fossero indipendenti, la finestra si
    riempirebbe al doppio (nu = 2). Qui lo si misura sui calendari reali, annuncio per annuncio.
    """
    raw = gzip.decompress(calendar_bytes) if calendar_bytes[:2] == b"\x1f\x8b" else calendar_bytes
    df = pd.read_csv(io.BytesIO(raw), usecols=["listing_id", "date", "available"],
                     dtype={"listing_id": "int64", "date": "string", "available": "string"})
    df = df[df["listing_id"].isin(set(int(x) for x in listing_ids))]
    if df.empty:
        return None
    df["a"] = (df["available"] == "t").astype("int8")
    M = df.pivot_table(index="listing_id", columns="date", values="a", aggfunc="max").sort_index(axis=1)
    M = M.fillna(0).to_numpy(dtype=bool)
    M = M[M[:, :180].any(axis=1)]                      # via gli annunci mai disponibili (chiusi)
    if M.shape[0] < 100 or M.shape[1] < 150:
        return None
    night = M[:, :-nights + 1 if nights > 1 else None].mean(axis=0)
    win = np.ones_like(M[:, : M.shape[1] - nights + 1])
    for j in range(nights):
        win = win & M[:, j: M.shape[1] - nights + 1 + j]
    win = win.mean(axis=0)
    upto = min(240, len(night))
    xn, xw = night[:upto] / night[:upto].max(), win[:upto] / win[:upto].max()
    ok = (xn < 0.92) & (xn > 0.05) & (xw > 0.02)
    ok[150:] = False
    if ok.sum() < 10:
        return None
    nu = float(np.median(np.log(xw[ok]) / np.log(xn[ok])))
    return {"nu": round(float(np.clip(nu, 1.0, float(nights))), 3), "raw": round(nu, 3), "listings": int(M.shape[0]),
            "leads": int(ok.sum())}


def download_calendar_factor(snapshot_date: str, listing_ids, nights: int, log=print, path: str = PATH) -> dict | None:
    import requests

    url = f"https://data.insideairbnb.com/{path}/{snapshot_date}/data/calendar.csv.gz"
    try:
        r = requests.get(url, headers=UA, timeout=240)
        r.raise_for_status()
        res = window_factor(r.content, listing_ids, nights)
        log(f"Inside Airbnb, calendari: {res}")
        return res
    except Exception as e:  # noqa: BLE001
        log(f"Inside Airbnb, calendari non utilizzabili: {e!r}"[:300])
        return None
