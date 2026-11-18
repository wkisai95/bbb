"""Prenotazioni rilevate: chi era libero in una lettura completa e non lo è più in quella dopo.

Per le date entro `track_days` lo scanner legge l'elenco completo degli alloggi liberi, e la stessa finestra
di date torna due giorni dopo. Un alloggio che c'era e non c'è più è stato quasi sempre prenotato (a volte il
proprietario ha chiuso il calendario o alzato il soggiorno minimo: da fuori non si distingue).

Per non scambiare per prenotazione una lettura sfuggita o un annuncio tolto:
  - conta per intero solo se manca anche alla lettura successiva; finché quella non c'è, conta in
    proporzione a quanti, di solito, non ricompaiono;
  - chi ricompare alla lettura successiva non conta ("tornato": era sfuggito, o la prenotazione è saltata);
  - chi sparisce da tutte le date insieme e non si rivede nei giorni seguenti non conta ("ritirato");
  - chi sparisce in blocco da cinque o più finestre nella stessa scansione non conta ("blocco": calendario
    chiuso o soggiorno molto lungo).
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .store import Store

MAX_GAP = 4            # giorni al massimo tra due letture della stessa finestra perché il confronto valga
ALIVE_DAYS = 7         # un annuncio è "vivo" se si rivede, in una data qualsiasi, entro questi giorni
BLOCK_WINDOWS = 5      # finestre sparite insieme oltre le quali è una chiusura, non una prenotazione
BACK_PRIOR = (3.0, 20.0)   # di partenza: 3 "tornati" ogni 20 spariti
EV_COLS = ["scan", "prev", "checkin", "id", "lead", "gap", "pn", "status", "sure", "w"]
PAIR_COLS = ["scan", "prev", "checkin", "lead", "gap", "risk", "booked", "gone", "back", "withdrawn", "blocked", "new", "sure"]


def _empty() -> dict:
    return {"events": pd.DataFrame(columns=EV_COLS), "pairs": pd.DataFrame(columns=PAIR_COLS),
            "samples": [], "back_rate": BACK_PRIOR[0] / BACK_PRIOR[1], "checked": 0}


def detect(store: Store, today: date, lookback: int = 45, nights: int = 2) -> dict:
    """Confronta le letture complete consecutive di ogni finestra.

    Restituisce:
      events   una riga per ogni alloggio sparito, con lo stato e il prezzo a notte a cui era offerto
      pairs    una riga per ogni confronto: quanti erano liberi, quanti prenotati (pesati), quanti tornati…
      samples  per ogni confronto, chi era libero (id, prezzo a notte, prenotato sì/no): serve alla
               sensibilità al prezzo
      back_rate  quota di spariti che ricompare alla lettura successiva
    """
    since = today - timedelta(days=lookback)
    cn = store.load_counts(since=since, nights=nights)
    if cn.empty:
        return _empty()
    cens = cn[(cn["scope"] == "census") & (pd.to_numeric(cn["complete"], errors="coerce") == 1)]
    reads = cens.groupby("checkin")["scan"].apply(lambda s: sorted(set(s))) if len(cens) else pd.Series(dtype=object)
    reads = reads[reads.map(len) >= 2] if len(reads) else reads
    if not len(reads):
        return _empty()
    first = min(days[0] for days in reads)
    pr = store.load_prices(since=date.fromisoformat(first), nights=nights)
    if pr.empty:
        return _empty()
    pr["pn"] = pr["price"] / pr["nights"]
    sn = store.load_seen(since=date.fromisoformat(first))

    wanted = {(s, w) for w, days in reads.items() for s in days}
    avail: dict[tuple, set] = {}
    price: dict[tuple, dict] = {}
    for (s, w), g in pr.groupby(["scan", "checkin"], sort=False):
        if (s, w) in wanted:
            avail[(s, w)] = set(g["id"].tolist())
            price[(s, w)] = dict(zip(g["id"].tolist(), g["pn"].tolist()))
    for (s, w), g in sn.groupby(["scan", "checkin"], sort=False):
        if (s, w) in wanted:
            avail.setdefault((s, w), set()).update(g["id"].tolist())
    seen_day: dict[str, set] = {s: set(g["id"].tolist()) for s, g in pr.groupby("scan", sort=False)}
    for s, g in sn.groupby("scan", sort=False):
        seen_day.setdefault(s, set()).update(g["id"].tolist())
    scan_days = sorted(seen_day)

    def alive(i: int, scan: str) -> bool:
        """Si rivede da qualche parte, in quella scansione o nei giorni dopo?"""
        end = (date.fromisoformat(scan) + timedelta(days=ALIVE_DAYS)).isoformat()
        return any(i in seen_day[s] for s in scan_days if scan <= s <= end)

    def gap_days(a: str, b: str) -> int:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days

    ev, pairs = [], []
    for w, days in reads.items():
        ci = date.fromisoformat(w)
        for k in range(len(days) - 1):
            a, b = days[k], days[k + 1]
            gap = gap_days(a, b)
            A, B = avail.get((a, w)), avail.get((b, w))
            if gap < 1 or gap > MAX_GAP or not A or B is None:
                continue
            nxt = days[k + 2] if k + 2 < len(days) and gap_days(b, days[k + 2]) <= MAX_GAP else None
            C = avail.get((nxt, w)) if nxt else None
            lead = (ci - date.fromisoformat(b)).days
            pa = price.get((a, w), {})
            for i in A - B:
                if C is not None and i in C:
                    status = "tornato"
                elif not alive(i, b):
                    status = "ritirato"
                else:
                    status = "prenotato"
                ev.append({"scan": b, "prev": a, "checkin": w, "id": i, "lead": lead, "gap": gap,
                           "pn": pa.get(i), "status": status, "sure": int(C is not None), "w": 0.0})
            pairs.append({"scan": b, "prev": a, "checkin": w, "lead": lead, "gap": gap, "risk": len(A),
                          "new": len(B - A), "sure": int(C is not None)})
    if not pairs:
        return _empty()
    E = pd.DataFrame(ev, columns=EV_COLS)
    P = pd.DataFrame(pairs)
    if len(E):
        # chiusure del calendario: lo stesso alloggio sparisce da tante finestre nella stessa scansione
        gone_together = E[E["status"] == "prenotato"].groupby(["scan", "id"])["checkin"].transform("size")
        E.loc[gone_together[gone_together >= BLOCK_WINDOWS].index, "status"] = "blocco"
    # quanti spariti ricompaiono: serve a pesare quelli che non hanno ancora una lettura di conferma
    chk = E[(E["sure"] == 1) & E["status"].isin(["prenotato", "tornato"])] if len(E) else E
    back = float((chk["status"] == "tornato").sum()) if len(chk) else 0.0
    back_rate = (back + BACK_PRIOR[0]) / (len(chk) + BACK_PRIOR[1])
    if len(E):
        booked = E["status"] == "prenotato"
        E.loc[booked, "w"] = np.where(E.loc[booked, "sure"] == 1, 1.0, 1.0 - back_rate)

    def per_pair(status: str | None, col: str = "id") -> pd.Series:
        if not len(E):
            return pd.Series(dtype=float)
        sel = E if status is None else E[E["status"] == status]
        g = sel.groupby(["scan", "checkin"])
        return g["w"].sum() if col == "w" else g[col].size()

    key = list(zip(P["scan"], P["checkin"]))
    for name, ser in (("gone", per_pair(None)), ("back", per_pair("tornato")), ("withdrawn", per_pair("ritirato")),
                      ("blocked", per_pair("blocco")), ("booked", per_pair("prenotato", "w"))):
        P[name] = [float(ser.get(k, 0.0)) for k in key]
    # a rischio: chi era libero, tolti quelli di cui non si può dire nulla (i "tornati" restano: non sono stati prenotati)
    P["risk"] = (P["risk"] - P["withdrawn"] - P["blocked"]).clip(lower=1)
    P = P[PAIR_COLS]

    # campioni per la sensibilità al prezzo: chi era libero alla prima lettura, con il suo prezzo
    drop = {}
    if len(E):
        for (s, w), g in E[E["status"].isin(["ritirato", "blocco"])].groupby(["scan", "checkin"]):
            drop[(s, w)] = set(g["id"].tolist())
    took = {}
    if len(E):
        for (s, w), g in E[E["status"] == "prenotato"].groupby(["scan", "checkin"]):
            took[(s, w)] = set(g["id"].tolist())
    samples = []
    for r in P.itertuples():
        pa = price.get((r.prev, r.checkin), {})
        out = drop.get((r.scan, r.checkin), set())
        ids = [i for i in pa if i not in out and pa[i] and pa[i] > 0]
        if len(ids) < 40:
            continue
        yes = took.get((r.scan, r.checkin), set())
        samples.append({"scan": r.scan, "checkin": r.checkin, "lead": r.lead, "gap": r.gap, "id": np.array(ids),
                        "pn": np.array([pa[i] for i in ids], dtype=float),
                        "y": np.array([1.0 if i in yes else 0.0 for i in ids])})
    return {"events": E, "pairs": P, "samples": samples, "back_rate": float(back_rate), "checked": int(len(chk))}


def hazard_by_lead(pairs: pd.DataFrame, horizon: int, smooth: int = 2):
    """Probabilità al giorno che una finestra libera venga prenotata, per giorni mancanti.

    Ogni confronto copre i giorni tra le due letture: le prenotazioni si ripartiscono su quei giorni.
    Restituisce (h, peso) per anticipo 0..horizon-1, dove h[L] è il ritmo tra L+1 e L giorni dalla data
    e peso[L] le prenotazioni su cui poggia.
    """
    num = np.zeros(horizon)
    den = np.zeros(horizon)
    for r in pairs.itertuples():
        for L in range(int(r.lead), int(r.lead) + int(r.gap)):
            if 0 <= L < horizon:
                num[L] += r.booked / r.gap
                den[L] += r.risk
    k = np.ones(2 * smooth + 1)
    num_s, den_s = np.convolve(num, k, mode="same"), np.convolve(den, k, mode="same")
    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(den_s > 0, num_s / np.maximum(den_s, 1e-9), 0.0)
    h = -np.log(1 - np.clip(share, 0.0, 0.5))
    return h, num_s


def summary(trk: dict, lst: pd.DataFrame, attr: pd.Series, today: date, days: int = 14) -> dict:
    """Riepilogo per l'app: quante prenotazioni, a che prezzo rispetto a chi è rimasto libero, per taglia e zona."""
    P, E = trk["pairs"], trk["events"]
    out = {"days": days, "pairs": 0, "risk": 0, "booked": 0.0, "back_rate": round(trk["back_rate"], 3),
           "checked": trk["checked"], "withdrawn": 0, "blocked": 0, "first": None, "last": None,
           "price": {}, "windows": [], "by_lead": []}
    if not len(P):
        return out
    cut = (today - timedelta(days=days)).isoformat()
    recent = P[P["scan"] >= cut]
    out.update({"pairs": int(len(recent)), "risk": int(recent["risk"].sum()), "booked": round(float(recent["booked"].sum()), 1),
                "withdrawn": int(recent["withdrawn"].sum()), "blocked": int(recent["blocked"].sum()),
                "first": str(P["scan"].min()), "last": str(P["scan"].max())})
    h, wgt = hazard_by_lead(recent, 31)
    out["by_lead"] = [[int(L), round(float(h[L]), 4)] for L in range(31) if wgt[L] >= 5]
    last = recent[recent["scan"] == recent["scan"].max()].sort_values("checkin")
    out["windows"] = [{"checkin": r.checkin, "scan": r.scan, "prev": r.prev, "risk": int(r.risk), "booked": round(float(r.booked), 1),
                       "sure": int(r.sure)} for r in last.itertuples()]
    # prezzi: chi è stato prenotato contro chi è rimasto libero, nelle stesse finestre
    rows = []
    for s in trk["samples"]:
        if s["scan"] < cut:
            continue
        a = attr.reindex(s["id"]).to_numpy()
        z = np.log(s["pn"]) - a
        rows.append(pd.DataFrame({"id": s["id"], "pn": s["pn"], "y": s["y"], "z": z - np.nanmedian(z)}))
    if rows:
        S = pd.concat(rows, ignore_index=True).join(lst[["bedrooms_f", "group"]], on="id", how="left")
        S["brc"] = S["bedrooms_f"].fillna(1).clip(upper=3).astype(int).astype(str)

        def stats(g: pd.DataFrame) -> dict | None:
            b, f = g[g["y"] == 1], g[g["y"] == 0]
            if len(b) < 8 or len(f) < 8:
                return None
            rel = float(np.nanmedian(b["z"]) - np.nanmedian(f["z"])) if b["z"].notna().sum() >= 8 else None
            return {"n": int(len(b)), "booked": round(float(b["pn"].median())), "free": round(float(f["pn"].median())),
                    "rel": None if rel is None or rel != rel else round(float(np.exp(rel) - 1), 3)}

        price = {"all": stats(S), "bedrooms": {}, "group": {}}
        for k, g in S.groupby("brc"):
            st = stats(g)
            if st:
                price["bedrooms"][k] = st
        for k, g in S[S["group"].notna() & (S["group"] != "")].groupby("group"):
            st = stats(g)
            if st:
                price["group"][k] = st
        out["price"] = price
    return out


def per_listing(trk: dict, today: date, days: int = 30) -> pd.DataFrame:
    """Per ogni alloggio: quante finestre gli sono state prenotate di recente e a che prezzo mediano a notte."""
    E = trk["events"]
    if not len(E):
        return pd.DataFrame(columns=["n", "pn"])
    cut = (today - timedelta(days=days)).isoformat()
    b = E[(E["status"] == "prenotato") & (E["scan"] >= cut) & E["pn"].notna()]
    if not len(b):
        return pd.DataFrame(columns=["n", "pn"])
    return b.groupby("id").agg(n=("checkin", "nunique"), pn=("pn", "median"))
