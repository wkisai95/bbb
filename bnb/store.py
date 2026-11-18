"""Archivio dei dati raccolti (cartella `out/`, pubblicata sul ramo `data`).

out/data/listings.csv              catalogo degli annunci
out/data/prices/AAAA-MM-GG.csv.gz  prezzi letti quel giorno (uno per annuncio e finestra di date)
out/data/counts/AAAA-MM-GG.csv     alloggi disponibili per finestra, riquadro e taglia
out/data/seen/AAAA-MM-GG.csv.gz    alloggi visti liberi senza prezzo nelle letture complete (gli altri sono tra i prezzi)
out/data/runs/AAAA-MM-GG.json      diario dell'esecuzione
out/data/history/*.csv             storico: indice dei prezzi per notte, qualità del modello
out/data/state.json                stato tra un'esecuzione e l'altra
out/public/*.json                  file letti dall'app
"""
from __future__ import annotations

import glob
import json
import os
from datetime import date

import pandas as pd

LISTING_COLS = ["id", "name", "kind", "lat", "lon", "zone", "district", "bedrooms", "beds", "baths", "guests",
                "guests_src", "rating", "reviews", "superhost", "fav", "min_nights", "first_seen", "last_seen", "n_seen"]
PRICE_COLS = ["id", "checkin", "nights", "price", "orig", "src"]
COUNT_COLS = ["checkin", "nights", "scope", "count", "pages", "complete"]
SEEN_COLS = ["checkin", "id"]


class Store:
    def __init__(self, root: str):
        self.root = root
        self.data = os.path.join(root, "data")
        self.public = os.path.join(root, "public")
        for d in ("prices", "counts", "seen", "runs", "history"):
            os.makedirs(os.path.join(self.data, d), exist_ok=True)
        os.makedirs(self.public, exist_ok=True)

    # ---- catalogo
    def load_listings(self) -> pd.DataFrame:
        p = os.path.join(self.data, "listings.csv")
        if not os.path.exists(p):
            df = pd.DataFrame(columns=LISTING_COLS)
        else:
            df = pd.read_csv(p, dtype={"id": "int64", "name": "string", "kind": "string", "zone": "string",
                                       "district": "string", "guests_src": "string", "first_seen": "string",
                                       "last_seen": "string"}, keep_default_na=True)
        for c in LISTING_COLS:
            if c not in df.columns:
                df[c] = pd.NA
        df = df[LISTING_COLS]
        for c in ("name", "kind", "zone", "district", "guests_src", "first_seen", "last_seen"):
            df[c] = df[c].astype("string").fillna("")
        for c in ("lat", "lon", "bedrooms", "beds", "baths", "guests", "rating", "min_nights"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        for c in ("reviews", "superhost", "fav", "n_seen"):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
        df["id"] = df["id"].astype("int64")
        return df.drop_duplicates("id").set_index("id", drop=False)

    def save_listings(self, df: pd.DataFrame) -> None:
        out = df[LISTING_COLS].sort_values("id")
        out.to_csv(os.path.join(self.data, "listings.csv"), index=False, float_format="%.5f")

    # ---- prezzi
    def _day_path(self, kind: str, day: date) -> str:
        ext = "csv.gz" if kind in ("prices", "seen") else "csv"
        return os.path.join(self.data, kind, f"{day.isoformat()}.{ext}")

    def add_prices(self, day: date, rows: list[dict]) -> int:
        """Aggiunge i prezzi del giorno; se la stessa coppia annuncio/finestra c'è già, vale l'ultima lettura."""
        p = self._day_path("prices", day)
        new = pd.DataFrame(rows, columns=PRICE_COLS)
        if os.path.exists(p):
            old = pd.read_csv(p, dtype={"id": "int64", "checkin": "string", "src": "string"})
            new = pd.concat([old, new], ignore_index=True)
        new = new.drop_duplicates(["id", "checkin", "nights"], keep="last").sort_values(["checkin", "id"])
        new.to_csv(p, index=False, compression="gzip", float_format="%.2f")
        return len(new)

    def add_counts(self, day: date, rows: list[dict]) -> int:
        p = self._day_path("counts", day)
        new = pd.DataFrame(rows, columns=COUNT_COLS)
        if os.path.exists(p):
            old = pd.read_csv(p, dtype={"checkin": "string", "scope": "string"}, keep_default_na=False)
            new = pd.concat([old, new], ignore_index=True)
        new["checkin"] = new["checkin"].fillna("").astype(str)
        new = new.drop_duplicates(["checkin", "nights", "scope"], keep="last").sort_values(["checkin", "scope"])
        new.to_csv(p, index=False)
        return len(new)

    def add_seen(self, day: date, rows: list[dict]) -> int:
        """Alloggi visti liberi ma senza prezzo in una lettura completa: servono a non scambiarli per prenotati."""
        p = self._day_path("seen", day)
        new = pd.DataFrame(rows, columns=SEEN_COLS)
        if os.path.exists(p):
            old = pd.read_csv(p, dtype={"id": "int64", "checkin": "string"})
            new = pd.concat([old, new], ignore_index=True)
        new = new.drop_duplicates(["checkin", "id"]).sort_values(["checkin", "id"])
        new.to_csv(p, index=False, compression="gzip")
        return len(new)

    def load_seen(self, since: date | None = None, until: date | None = None) -> pd.DataFrame:
        frames = []
        for d in self.scan_days("seen"):
            if (since and d < since) or (until and d > until):
                continue
            df = pd.read_csv(self._day_path("seen", d), dtype={"id": "int64", "checkin": "string"})
            df["scan"] = d.isoformat()
            frames.append(df)
        if not frames:
            return pd.DataFrame(columns=SEEN_COLS + ["scan"])
        return pd.concat(frames, ignore_index=True)

    def scan_days(self, kind: str = "prices") -> list[date]:
        ext = "csv.gz" if kind in ("prices", "seen") else "csv"
        out = []
        for p in glob.glob(os.path.join(self.data, kind, f"*.{ext}")):
            try:
                out.append(date.fromisoformat(os.path.basename(p)[:10]))
            except ValueError:
                pass
        return sorted(out)

    def load_prices(self, since: date | None = None, until: date | None = None, nights: int | None = None) -> pd.DataFrame:
        """Prezzi letti; con nights solo quelli dei soggiorni di quella durata (2 = campione, 1 = notti singole)."""
        frames = []
        for d in self.scan_days("prices"):
            if (since and d < since) or (until and d > until):
                continue
            df = pd.read_csv(self._day_path("prices", d), dtype={"id": "int64", "checkin": "string", "src": "string"})
            if nights is not None:
                df = df[df["nights"] == nights]
            df["scan"] = d.isoformat()
            frames.append(df)
        if not frames:
            return pd.DataFrame(columns=PRICE_COLS + ["scan"])
        return pd.concat(frames, ignore_index=True)

    def load_counts(self, since: date | None = None, until: date | None = None, nights: int | None = None) -> pd.DataFrame:
        """Conteggi; con nights solo quelli delle finestre di quella durata, più quelli senza date (offerta)."""
        frames = []
        for d in self.scan_days("counts"):
            if (since and d < since) or (until and d > until):
                continue
            df = pd.read_csv(self._day_path("counts", d), dtype={"checkin": "string", "scope": "string"}, keep_default_na=False)
            if nights is not None:
                df = df[(df["checkin"] == "") | (pd.to_numeric(df["nights"], errors="coerce") == nights)]
            df["scan"] = d.isoformat()
            frames.append(df)
        if not frames:
            return pd.DataFrame(columns=COUNT_COLS + ["scan"])
        out = pd.concat(frames, ignore_index=True)
        out["count"] = pd.to_numeric(out["count"], errors="coerce")
        return out

    # ---- json
    def read_json(self, rel: str, default=None):
        p = os.path.join(self.root, rel)
        if not os.path.exists(p):
            return default
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return default

    def write_json(self, rel: str, obj, compact: bool = False) -> None:
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            if compact:
                json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
            else:
                json.dump(obj, f, ensure_ascii=False, indent=1)

    def load_state(self) -> dict:
        return self.read_json("data/state.json", {}) or {}

    def save_state(self, state: dict) -> None:
        self.write_json("data/state.json", state)

    # ---- storico
    def load_history(self, name: str) -> pd.DataFrame:
        p = os.path.join(self.data, "history", f"{name}.csv")
        if not os.path.exists(p):
            return pd.DataFrame()
        return pd.read_csv(p, dtype={"date": "string"})

    def save_history(self, name: str, df: pd.DataFrame) -> None:
        df.to_csv(os.path.join(self.data, "history", f"{name}.csv"), index=False, float_format="%.5f")
