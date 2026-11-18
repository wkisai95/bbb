"""Stime: prezzo dei comparabili, effetto delle date e degli eventi, domanda, sensibilità al prezzo.

In breve:
  1. prezzo/notte = caratteristiche dell'alloggio (zona, camere, bagni, ospiti, recensioni) × effetto della data;
  2. effetto della data: misurato sulle finestre lette; dove manca, livello normale × effetto tipico dell'evento;
  3. domanda: quota di alloggi non più disponibili per ogni data, confrontata con l'andamento tipico
     per anticipo ("curva di prenotazione") -> probabilità che una notte libera oggi venga prenotata;
  4. sensibilità al prezzo: dai censimenti ripetuti, chi sparisce (prenotato) rispetto al proprio prezzo relativo.
Ogni stima parte da un valore prudente e si sposta verso i dati man mano che si accumulano.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy import sparse

from . import events as ev
from . import track
from .geo import Zones
from .store import Store

Q_LEVELS = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95]
SEG_CLASSES = ["c01", "c2", "c3"]            # 0-1 camere · 2 camere · 3 o più
GROUPS = ["centro", "cintura", "periferia", "fuori"]
LEAD_EDGES = [0, 2, 4, 6, 9, 12, 16, 22, 31, 46, 61, 91, 121, 181, 271, 400]


def logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def expit(x):
    return 1 / (1 + np.exp(-np.asarray(x, dtype=float)))


def seg_class(bedrooms) -> str:
    b = 1 if bedrooms is None or bedrooms != bedrooms else int(bedrooms)
    return "c01" if b <= 1 else "c2" if b == 2 else "c3"


# ------------------------------------------------------------------ catalogo

def prepare_listings(lst: pd.DataFrame) -> pd.DataFrame:
    """Riempie i vuoti: camere, bagni e soprattutto ospiti (stimati da camere e letti quando mancano)."""
    df = lst.copy()
    df["bedrooms_f"] = df["bedrooms"].fillna(1).clip(0, 8)
    df["baths_f"] = df["baths"].fillna(1).clip(0.5, 6)
    df["beds_f"] = df["beds"].fillna(df["bedrooms_f"].clip(lower=1)).clip(1, 12)
    known = df[df["guests"].notna() & ~df["guests_src"].isin(["est", ""])]
    key = lambda d: (d["bedrooms_f"].clip(upper=4).astype(int), d["beds_f"].clip(upper=6).astype(int))  # noqa: E731
    est = pd.Series(np.nan, index=df.index)
    if len(known) >= 30:
        kb, kd = key(known)
        table = known.groupby([kb, kd])["guests"].median()
        by_br = known.groupby(kb)["guests"].median()
        b, d = key(df)
        est = pd.Series([table.get((bi, di), np.nan) for bi, di in zip(b, d)], index=df.index)
        est = est.fillna(b.map(by_br))
    fallback = (2 + 2 * df["bedrooms_f"]).clip(2, 12).where(df["bedrooms_f"] > 0, 2)
    fallback = np.maximum(fallback, np.minimum(df["beds_f"] * 1.5, 12)).round()
    est = est.fillna(fallback)
    missing = df["guests"].isna() | df["guests_src"].isin(["est", ""])
    df["guests_f"] = df["guests"].where(~missing, est).clip(1, 16)
    df["guests_est"] = missing
    df["rating_f"] = df["rating"].clip(4.0, 5.0)
    df["is_new"] = df["rating"].isna().astype(float)
    return df


# ------------------------------------------------------------------ 1. prezzi dei comparabili

def _features(df: pd.DataFrame, zones: Zones, windows: list[str]):
    """Matrice sparsa delle caratteristiche. Restituisce (X, nomi colonne, penalità)."""
    n = len(df)
    names, pens, cols, rows, vals = ["intercept"], [0.0], [], [], []
    rows.append(np.arange(n)); cols.append(np.zeros(n, dtype=int)); vals.append(np.ones(n))  # noqa: E702

    def add_dummy(name, mask, pen):
        idx = np.flatnonzero(np.asarray(mask))
        names.append(name); pens.append(pen)  # noqa: E702
        rows.append(idx); cols.append(np.full(len(idx), len(names) - 1)); vals.append(np.ones(len(idx)))  # noqa: E702

    def add_num(name, x, pen):
        names.append(name); pens.append(pen)  # noqa: E702
        rows.append(np.arange(n)); cols.append(np.full(n, len(names) - 1)); vals.append(np.asarray(x, dtype=float))  # noqa: E702

    zone = df["zone"].to_numpy()
    grp = df["group"].to_numpy()
    for g in GROUPS:
        add_dummy(f"grp:{g}", grp == g, 0.5)
    for z in zones.zones:
        add_dummy(f"zone:{z['id']}", zone == z["id"], None)   # penalità impostata dal chiamante
    br = df["bedrooms_f"].clip(upper=4).astype(int).to_numpy()
    for b in (0, 2, 3, 4):
        add_dummy(f"br:{b}", br == b, 0.5)
    add_num("bath", df["baths_f"].clip(0.5, 4).to_numpy() - 1.0, 0.2)
    add_num("guests", np.log(df["guests_f"].clip(1, 12).to_numpy() / 4.0), 0.2)
    add_num("rating", df["rating_f"].fillna(4.8).to_numpy() - 4.8, 0.5)
    add_dummy("new", df["is_new"].to_numpy() > 0, 1.0)
    add_dummy("fav1", df["fav"].to_numpy() == 1, 1.0)
    add_dummy("fav2", df["fav"].to_numpy() == 2, 1.0)
    add_dummy("superhost", df["superhost"].to_numpy() == 1, 1.0)
    wk = df["checkin"].to_numpy()
    for w in windows:
        add_dummy(f"win:{w}", wk == w, -1.0)                  # -1 = penalità finestra, impostata dal chiamante
    X = sparse.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, len(names)))
    return X, names, pens


def fit_prices(obs: pd.DataFrame, zones: Zones, mcfg: dict) -> dict:
    """Regressione ridge su log(prezzo/notte). obs: una riga per annuncio e finestra, con le caratteristiche."""
    wcount = obs["checkin"].value_counts()
    windows = sorted(wcount[wcount >= 8].index.tolist())
    y = np.log(obs["pn"].to_numpy())
    X, names, pens = _features(obs, zones, windows)
    pen = np.array([mcfg["ridge_zone"] if p is None else mcfg["ridge_window"] if p == -1.0 else p for p in pens])
    keep = np.ones(len(y), dtype=bool)
    coef = None
    for _ in range(3):
        Xk, yk = X[keep], y[keep]
        A = (Xk.T @ Xk).toarray() + np.diag(pen)
        coef = np.linalg.solve(A, Xk.T @ yk)
        resid = y - X @ coef
        s = 1.4826 * np.median(np.abs(resid[keep] - np.median(resid[keep]))) or 0.3
        new_keep = np.abs(resid) < 3.5 * s
        if new_keep.sum() == keep.sum():
            break
        keep = new_keep
    resid = y - X @ coef
    c = dict(zip(names, coef))
    win = {w: c[f"win:{w}"] for w in windows}
    attr = (X @ coef) - np.array([win.get(w, 0.0) for w in obs["checkin"].to_numpy()])
    return {"coef": c, "windows": win, "win_n": {w: int(wcount[w]) for w in windows}, "resid": resid, "keep": keep,
            "attr": attr, "sigma": float(np.std(resid[keep])), "n": int(keep.sum()), "names": names}


def night_effects(win: dict, win_n: dict, nights_per: int, bump: dict | None = None, lam: float = 0.08):
    """Dalle finestre (somma di più notti) all'effetto di ogni singola notte.

    Minimi quadrati con richiamo verso il livello atteso della notte (tipo di giorno + effetto tipico
    dell'evento, `bump`): quando due finestre sfalsate coprono la stessa notte si riesce a separare,
    ad esempio, il sabato dalla domenica; quando ce n'è una sola, il rincaro va alla notte con l'evento.
    """
    if not win:
        return {}, {}
    cover: dict[int, list] = {}
    items = []
    for w, eff in win.items():
        c = date.fromisoformat(w).toordinal()
        ns = [c + j for j in range(nights_per)]
        wt = win_n[w] / (win_n[w] + 5.0)
        items.append((ns, eff, wt, win_n[w]))
        for d in ns:
            cover.setdefault(d, []).append((eff, wt, win_n[w]))
    days = sorted(cover)
    pos = {d: i for i, d in enumerate(days)}
    n0 = np.array([sum(e * w for e, w, _ in cover[d]) / sum(w for _, w, _ in cover[d]) for d in days])
    nobs = {d: sum(c for _, _, c in cover[d]) / nights_per for d in days}
    is_we = np.array([date.fromordinal(d).weekday() in (4, 5) for d in days])
    prior = np.zeros(len(days))
    bumps = np.array([(bump or {}).get(d, 0.0) for d in days])
    for flag in (True, False):
        m = is_we == flag
        if m.any():
            calm = m & (bumps < 0.15)
            prior[m] = np.median(n0[calm]) if calm.sum() >= 3 else np.median(n0[m])
    prior = prior + bumps
    A = np.eye(len(days)) * lam
    b = lam * prior
    for ns, eff, wt, _ in items:
        idx = [pos[d] for d in ns]
        k = len(idx)
        for i in idx:
            b[i] += wt * eff / k
            for j in idx:
                A[i, j] += wt / (k * k)
    sol = np.linalg.solve(A, b)
    return {d: float(sol[pos[d]]) for d in days}, nobs


def baseline(night: dict, weights: dict, target_days: list[int]) -> dict:
    """Livello 'normale' per ogni notte: mediana delle notti vicine dello stesso tipo, senza grandi eventi."""
    days = np.array(sorted(night))
    if len(days) == 0:
        return {d: 0.0 for d in target_days}
    vals = np.array([night[d] for d in days])
    we = np.array([date.fromordinal(int(d)).weekday() in (4, 5) for d in days])
    calm = np.array([weights.get(int(d), 0) < 3 for d in days])
    out = {}
    for d in target_days:
        flag = date.fromordinal(d).weekday() in (4, 5)
        val = None
        for span in (21, 45, 90, 200, 400):
            m = (we == flag) & calm & (np.abs(days - d) <= span)
            if m.sum() >= 3:
                val = float(np.median(vals[m]))
                break
        if val is None:
            m = (we == flag) & calm
            val = float(np.median(vals[m])) if m.any() else float(np.median(vals))
        out[d] = val
    return out


# ------------------------------------------------------------------ 3. domanda

def _pava_decreasing(y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Regressione isotonica (non crescente)."""
    y, w = list(map(float, y)), list(map(float, w))
    blocks = [[y[i], w[i], 1] for i in range(len(y))]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] < blocks[i + 1][0] - 1e-12:      # viola "non crescente"
            a, b = blocks[i], blocks[i + 1]
            tot = a[1] + b[1]
            blocks[i] = [(a[0] * a[1] + b[0] * b[1]) / tot, tot, a[2] + b[2]]
            del blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    out = []
    for v, _, n in blocks:
        out += [v] * n
    return np.array(out)


def booking_curve(u: pd.DataFrame) -> dict:
    """Quota non disponibile tipica per anticipo. u: colonne lead, U."""
    centers, med, cnt = [], [], []
    for a, b in zip(LEAD_EDGES[:-1], LEAD_EDGES[1:]):
        m = (u["lead"] >= a) & (u["lead"] < b)
        if m.sum() >= 2:
            centers.append((a + b - 1) / 2)
            med.append(float(np.median(logit(u.loc[m, "U"].to_numpy()))))
            cnt.append(int(m.sum()))
    if not centers:
        return {"lead": [0, 365], "logit": [logit(0.65), logit(0.35)], "n": 0}
    centers, med, cnt = np.array(centers), np.array(med), np.array(cnt, dtype=float)
    near = centers <= 150
    if near.sum() >= 2:
        med[near] = _pava_decreasing(med[near], cnt[near])
    lead, lg = [float(c) for c in centers], [float(v) for v in med]
    if lead[0] > 0:                                  # il giorno stesso non viene mai letto: lo estrapolo
        slope = (lg[0] - lg[1]) / (lead[1] - lead[0]) if len(lead) > 1 else 0.0
        lead.insert(0, 0.0)
        lg.insert(0, lg[0] + float(np.clip(slope * lead[1], 0.0, 0.3)))
    return {"lead": lead, "logit": [round(v, 4) for v in lg], "n": int(cnt.sum())}


def curve_at(curve: dict, lead) -> np.ndarray:
    return np.interp(np.asarray(lead, dtype=float), curve["lead"], curve["logit"])


TAIL_FILL, TAIL_DAYS = 0.1625, 100.0     # 65% di riempimento tipico × un quarto delle prenotazioni fatte con largo anticipo


def tail_pace(L, nu: float = 1.0) -> np.ndarray:
    """Prenotazioni arrivate prima che la disponibilità tocchi il suo massimo.

    Dal confronto tra date non si vedono, perché a quell'anticipo si confondono con i calendari non ancora
    aperti: si ipotizza una coda lenta, che pesa poco e viene poi corretta dalla velocità di ogni notte.
    È espressa per notte; una finestra di più notti la consuma nu volte più in fretta.
    """
    return -nu * np.log(1 - TAIL_FILL * np.exp(-np.asarray(L, dtype=float) / TAIL_DAYS))


def prior_profile(calib: dict, horizon: int) -> np.ndarray:
    """Disponibilità tipica di partenza per anticipo, in log e riferita al suo massimo (0).

    Vicino alla data cala perché gli alloggi vengono prenotati: si parte da una forma tipica (tre quarti
    delle prenotazioni nell'ultimo mese), che fit_prior adatta poi ai dati del giorno. Quella dei calendari
    di Inside Airbnb qui non si usa: fotografa una sola settimana, e se era una settimana di fiera inganna.
    Molto lontano cala di nuovo perché tanti calendari non sono ancora aperti: questo dipende dalle abitudini
    di chi affitta e cambia poco, quindi vale la forma misurata sui calendari di Inside Airbnb; se manca,
    lo 0,4% di alloggi in meno per ogni giorno oltre gli 80.
    """
    L = np.arange(horizon + 1, dtype=float)
    share = 0.75 * np.exp(-L / 21.0) + 0.25 * np.exp(-L / 100.0)
    booked = np.log(1 - 0.65 * share)
    pts = calib.get("profile")
    if pts and len(pts) >= 6:
        measured = np.interp(L, [float(p[0]) for p in pts], [float(p[1]) for p in pts])
        peak = int(np.clip(np.argmax(measured[: min(len(measured), 241)]), 45, 150))
        closed = np.where(L > peak, np.minimum(measured - measured[min(peak, horizon)], 0.0), 0.0)
    else:
        closed = -0.004 * np.maximum(L - 80.0, 0.0)
    P = booked + closed
    return P - float(P[: min(len(P), 241)].max())


def pace_from_profile(ln_typ: np.ndarray, nu: float = 1.0):
    """Ritmo cumulato di prenotazione C[L] (0 = ancora tutto libero) dalla disponibilità tipica per anticipo.

    Conta come prenotazione solo il calo tra l'anticipo in cui la disponibilità è massima e la data.
    Più lontano la disponibilità scende per i calendari chiusi, non per le prenotazioni: lì resta soltanto
    la coda ipotizzata. Restituisce anche l'anticipo del massimo.
    """
    ln_typ = np.asarray(ln_typ, dtype=float)
    n = len(ln_typ)
    peak = int(np.argmax(ln_typ[: min(n, 241)]))
    tail = tail_pace(np.arange(n), nu)
    C = tail.copy()
    near = np.maximum(ln_typ[peak] - ln_typ[: peak + 1], 0.0)
    C[: peak + 1] = tail[peak] + np.maximum.accumulate(near[::-1])[::-1]
    return C, peak


def squeeze(P: np.ndarray, peak: int, tau: float) -> np.ndarray:
    """Stessa forma di partenza, ma con il calo finale concentrato negli ultimi tau·peak giorni."""
    out = P.copy()
    L = np.arange(peak + 1, dtype=float)
    out[: peak + 1] = np.interp(np.minimum(L / tau, float(peak)), L, P[: peak + 1])
    return out


def fit_prior(P: np.ndarray, ln_obs: np.ndarray, lead: np.ndarray):
    """Adatta la forma di partenza a ciò che si vede: livello, ampiezza e durata del calo finale.

    Tre numeri soltanto. Il livello è la mediana sulle date in cui la forma conta poco; ampiezza e durata
    si scelgono tra poche alternative, tenendo quelle che lasciano gli scarti più piccoli (in bassa
    stagione si prenota meno e più sotto data che in alta). Gli scarti grandi contano fino a un tetto,
    così un fine settimana pieno non trascina la stima.
    """
    peak = int(np.argmax(P[: min(len(P), 241)]))
    zone = lead <= peak
    best = None
    for tau in (1.0, 0.7, 0.5, 0.35):
        Pt = squeeze(P, peak, tau)
        flat = (Pt[lead] > -0.12) & (lead <= peak + 20)      # attorno al massimo: né prenotazioni né calendari chiusi
        use = flat if flat.sum() >= 8 else np.ones(len(lead), dtype=bool)
        level = float(np.median(ln_obs[use] - Pt[lead[use]]))
        if zone.sum() < 12:                                   # troppo poche letture per scegliere: forma così com'è
            best = (0.0, tau, 1.0, level, Pt)
            break
        y, x = ln_obs[zone] - level, Pt[lead[zone]]
        err, scale = min((float(np.minimum(np.abs(y - sc * x), 0.3).mean()), float(sc)) for sc in np.arange(0.2, 2.01, 0.1))
        if best is None or err < 0.95 * best[0]:              # si cambia durata solo se gli scarti calano davvero
            best = (err, tau, scale, level, Pt)
    _, tau, scale, level, Pt = best
    Pt = Pt.copy()
    Pt[: peak + 1] *= scale
    return level + Pt, {"level": round(level, 4), "tau": tau, "scale": round(scale, 2)}


def reference_curve(city: pd.DataFrame, curve: dict, calib: dict, horizon: int, days_n: int, nu: float = 1.0):
    """Disponibilità tipica (in log) per anticipo e ritmo cumulato C.

    Il primo giorno una sola lettura non basta a distinguere l'effetto dell'anticipo da quello delle
    singole date: si parte quindi dalla forma di riferimento adattata a ciò che si vede oggi e ci si
    sposta verso la curva osservata man mano che i giorni di scansione si accumulano (pieno peso a 14 giorni).
    """
    L = np.arange(horizon + 1, dtype=float)
    ln_obs = np.log(np.clip(1 - city["U"].to_numpy(dtype=float), 1e-4, 1.0))
    lead = np.clip(city["lead"].to_numpy(), 0, horizon).astype(int)
    ln_prior, _ = fit_prior(prior_profile(calib, horizon), ln_obs, lead)
    ln_live = np.log(np.clip(1 - expit(curve_at(curve, L)), 1e-4, 1.0))
    w = min(1.0, days_n / 14.0) * min(1.0, curve.get("n", 0) / 400.0)
    ln_ref = (1 - w) * ln_prior + w * ln_live
    C, _ = pace_from_profile(ln_ref, nu)
    return ln_ref, C, w


THETA_PRIOR = {0: 1.0, 1: 1.0, 2: 1.1, 3: 1.3, 4: 1.6, 5: 2.0}
BUSY_WEIGHT = 4   # da questo peso in su (grandi fiere) una data non entra nella stima del "tipico"
C0 = 0.25      # quanta "evidenza" serve prima di credere al ritmo di una singola data


# ------------------------------------------------------------------ 4. sensibilità al prezzo

def fit_beta(pairs: list, prior: float, prior_sd: float) -> dict:
    """Logit condizionato: P(sparito tra due censimenti) = σ(a_coppia + β·ln prezzo relativo).

    pairs = [(z, y)] con z = log del prezzo relativo (centrato nella coppia) e y = 1 se l'annuncio non c'è più.
    """
    pairs = [(np.asarray(z, float), np.asarray(y, float)) for z, y in pairs
             if len(z) >= 40 and 0.03 < np.mean(y) < 0.97]
    n = int(sum(len(z) for z, _ in pairs))
    if not pairs:
        return {"beta": prior, "pairs": 0, "n": 0, "weight": 0.0}
    beta = prior
    a = [float(logit(np.mean(y))) for _, y in pairs]
    info = 0.0
    for _ in range(40):
        g = -(beta - prior) / prior_sd ** 2
        h = -1.0 / prior_sd ** 2
        info = 0.0
        for j, (z, y) in enumerate(pairs):
            for _ in range(8):                      # intercetta della coppia, a β fissato
                p = expit(a[j] + beta * z)
                a[j] += (y.sum() - p.sum()) / max(1e-6, (p * (1 - p)).sum())
            p = expit(a[j] + beta * z)
            g += ((y - p) * z).sum()
            hj = (p * (1 - p) * z * z).sum()
            h -= hj
            info += hj
        step = -g / h
        beta += float(np.clip(step, -0.5, 0.5))
        if abs(step) < 1e-4:
            break
    beta = float(np.clip(beta, -8.0, -1.3))
    return {"beta": beta, "pairs": len(pairs), "n": n, "weight": float(info / (info + 1.0 / prior_sd ** 2))}


# ------------------------------------------------------------------ modello completo

def _single_nights(store: Store, today: date, nights_per: int, level: dict | None = None, days: int = 28) -> dict | None:
    """Soggiorni di una notte (lette a parte, vedi scan.singles): quanto chiedono gli stessi alloggi per una
    notte sola rispetto al prezzo che quella notte ha dentro il soggiorno campione con lo stesso arrivo, e quanti
    alloggi liberi si trovano per una notte sola ogni 100 che si trovano per i soggiorni campione.

    level: livello del prezzo di ogni notte (giorno ordinale -> fattore): un sabato dentro "sabato e domenica"
    vale più della metà del soggiorno, e senza questa correzione sembrerebbe un rincaro della notte singola."""
    since = today - timedelta(days=days)
    p1 = store.load_prices(since=since, nights=1)
    if p1.empty:
        return None
    p2 = store.load_prices(since=since, nights=nights_per)
    j = p1.merge(p2, on=["scan", "id", "checkin"], suffixes=("_1", "_2"))
    out = {"since": since.isoformat(), "prices": int(len(p1)), "pairs": int(len(j)), "premium": None,
           "premium_weekend": None, "premium_weekday": None, "share_higher": None, "supply_ratio": None, "dates": 0}
    if len(j):
        def share(ci: str) -> float:
            d0 = date.fromisoformat(ci).toordinal()
            lv = [level.get(d0 + k) for k in range(nights_per)] if level else []
            return lv[0] / sum(lv) if lv and all(v for v in lv) else 1.0 / nights_per
        j = j.assign(r=j["price_1"] / (j["price_2"] * j["checkin"].map(share)))
        j = j[(j["r"] > 0.4) & (j["r"] < 2.5)]
        weekend = pd.to_datetime(j["checkin"]).dt.weekday.isin([4, 5]).to_numpy()      # notti di venerdì e sabato

        def premium(mask) -> float | None:
            x = j["r"].to_numpy()[mask]
            return round(float(np.exp(np.median(np.log(x)))), 3) if len(x) >= 30 else None
        out["premium"] = premium(np.ones(len(j), bool))
        out["premium_weekend"] = premium(weekend)
        out["premium_weekday"] = premium(~weekend)
        out["share_higher"] = round(float((j["r"] > 1.02).mean()), 3) if len(j) else None
    c1 = store.load_counts(since=since, nights=1)
    c2 = store.load_counts(since=since, nights=nights_per)
    c1 = c1[(c1["checkin"] != "") & (c1["scope"] == "city")]
    c2 = c2[(c2["checkin"] != "") & (c2["scope"] == "city")]
    cc = c1.merge(c2, on=["scan", "checkin"], suffixes=("_1", "_2"))
    if len(cc):
        ratio = (pd.to_numeric(cc["count_1"], errors="coerce") / pd.to_numeric(cc["count_2"], errors="coerce").replace(0, np.nan)).dropna()
        out["supply_ratio"] = round(float(ratio.median()), 3) if len(ratio) else None
        out["dates"] = int(cc["checkin"].nunique())
    return out


def build(store: Store, cfg: dict, today: date, log=print) -> dict | None:
    """Stima tutto dai dati raccolti e restituisce il modello (dizionario pronto per l'app)."""
    mcfg = cfg["model"]
    zones = Zones()
    nights_per = int(cfg["scan"]["nights"])
    horizon = int(cfg["scan"]["far_days"])
    t_ord = today.toordinal()

    lst = store.load_listings()
    if lst.empty:
        log("modello: catalogo vuoto, niente da stimare")
        return None
    lst = prepare_listings(lst)
    lst["group"] = lst["zone"].map(lambda z: zones.group_of(z) if z in zones.by_id else "")

    since = today - timedelta(days=int(mcfg["fit_days"]))
    pr = store.load_prices(since=since, nights=nights_per)
    if pr.empty:
        log("modello: nessun prezzo raccolto")
        return None
    pr["pn"] = pr["price"] / pr["nights"]
    pr = pr[(pr["pn"] >= mcfg["min_price"]) & (pr["pn"] <= mcfg["max_price"])]
    pr = pr.sort_values("scan").drop_duplicates(["id", "checkin", "nights"], keep="last")
    cols = ["zone", "group", "bedrooms_f", "baths_f", "guests_f", "rating_f", "is_new", "fav", "superhost"]
    obs = pr.join(lst[cols], on="id", how="inner")
    obs = obs[obs["zone"].isin(zones.by_id)]
    if len(obs) < 200:
        log(f"modello: solo {len(obs)} prezzi utilizzabili, troppo pochi")
        return None

    # ---- 1. prezzi
    fit = fit_prices(obs, zones, mcfg)
    c = fit["coef"]
    resid = fit["resid"][fit["keep"]]
    q_values = [float(np.percentile(resid, q)) for q in Q_LEVELS]

    # controllo fuori campione: un quinto degli annunci tenuto da parte
    ids = obs["id"].to_numpy()
    hold = (ids % 5) == (t_ord % 5)
    quality = {"fit_mdape": None, "seg_mdape": None}
    if hold.sum() >= 100 and (~hold).sum() >= 300:
        f2 = fit_prices(obs[~hold], zones, mcfg)
        X_h, names_h, _ = _features(obs[hold], zones, sorted(f2["windows"]))
        pred = X_h @ np.array([f2["coef"][n] for n in names_h])
        act = np.log(obs.loc[hold, "pn"].to_numpy())
        quality["fit_mdape"] = float(np.median(np.abs(np.exp(act - pred) - 1)))
        h = obs[hold].assign(pred=pred, act=act)
        h["seg"] = h["zone"] + "|" + h["bedrooms_f"].clip(upper=3).astype(int).astype(str)
        g = h.groupby("seg").agg(p=("pred", "median"), a=("act", "median"), n=("act", "size"))
        g = g[g["n"] >= 8]
        if len(g):
            quality["seg_mdape"] = float(np.median(np.abs(np.exp(g["a"] - g["p"]) - 1)))

    # ---- 2. effetto delle date
    auto = [e for e in (ev._from_dict(x, "auto") for x in (store.read_json("data/events_auto.json", {}) or {}).get("events", [])) if e]  # noqa: SLF001
    events = ev.all_events(today, horizon + 5, auto)
    nw = ev.night_weights(events)
    weights = {d.toordinal(): w for d, (w, _) in nw.items()}
    prior = {int(k): math.log(v) for k, v in mcfg["event_prior"].items()}
    bump = {d: prior.get(w, 0.0) for d, w in weights.items()}
    night, nobs = night_effects(fit["windows"], fit["win_n"], nights_per, bump)
    days = list(range(t_ord, t_ord + horizon + 1))
    base = baseline(night, weights, days)
    base_past = baseline(night, weights, sorted(night))
    # effetto tipico per peso dell'evento: parte dal valore di partenza e si sposta verso il misurato
    learned, learned_n = {}, {}
    for w in range(1, 6):
        d_w = [night[d] - base_past[d] for d in night if weights.get(d, 0) == w and nobs.get(d, 0) >= 8]
        learned_n[w] = len(d_w)
        learned[w] = (len(d_w) * float(np.median(d_w)) + 3 * prior[w]) / (len(d_w) + 3) if d_w else prior[w]
    idx, cov = [], []
    for d in days:
        n_d = nobs.get(d, 0.0)
        guess = base[d] + learned.get(weights.get(d, 0), 0.0)
        if d in night and n_d >= 6:
            k = n_d / (n_d + 6.0)
            idx.append(k * night[d] + (1 - k) * guess)
        else:
            idx.append(guess)
        cov.append(int(round(n_d)))
    base_arr = [base[d] for d in days]

    # ---- 3. domanda
    cn = store.load_counts(since=today - timedelta(days=75), nights=nights_per)
    calib = dict((store.load_state().get("calibration") or {}))
    if mcfg.get("nu") is not None:
        calib["nu"] = float(mcfg["nu"])
    # chi era libero a una lettura completa e non lo è più a quella dopo: prenotazioni rilevate
    trk = track.detect(store, today, nights=nights_per)
    realized = track.realized(trk, today)
    attr = _attr(lst, c, zones)
    demand = _demand(cn, days, today, nights_per, weights, zones, cfg, calib, log, trk["pairs"])

    # ---- 4. sensibilità al prezzo
    beta = _beta_from_tracking(trk, attr, mcfg)
    tracking = track.summary(trk, lst, attr, today)

    # ---- eventi misurati e picchi senza nome
    ev_out = []
    for e in events:
        ns = [n.toordinal() for n in e.nights()]
        if ns[-1] < t_ord or ns[0] > t_ord + horizon:
            continue
        j = e.to_json()
        meas = [idx[d - t_ord] - base[d] for d in ns if t_ord <= d <= t_ord + horizon and cov[d - t_ord] >= 6]
        j["uplift"] = round(float(np.exp(np.mean(meas)) - 1), 3) if meas else None
        j["measured"] = bool(meas)
        ev_out.append(j)
    ev_out += _detect_peaks(days, idx, base, cov, weights, today)

    # ---- mercato e catalogo
    market, listing_rows = _market(obs, fit, lst, zones, base_arr, today, track.per_listing(trk, today))

    def r3(xs):
        return [round(float(x), 3) for x in xs]

    zone_coef = {}
    for z in zones.zones:
        zone_coef[z["id"]] = round(float(c.get(f"grp:{z['group']}", 0.0) + c.get(f"zone:{z['id']}", 0.0)), 4)
    active = lst[lst["last_seen"] >= (today - timedelta(days=45)).isoformat()]
    means = {"rating": float(active["rating_f"].mean()) if active["rating_f"].notna().any() else 4.8,
             "new": float(active["is_new"].mean()) if len(active) else 0.1,
             "fav1": float((active["fav"] == 1).mean()) if len(active) else 0.2,
             "fav2": float((active["fav"] == 2).mean()) if len(active) else 0.2,
             "superhost": float((active["superhost"] == 1).mean()) if len(active) else 0.3}
    q_media = (c["rating"] * (means["rating"] - 4.8) + c["new"] * means["new"] + c["fav1"] * means["fav1"]
               + c["fav2"] * means["fav2"] + c["superhost"] * means["superhost"])
    quality_adj = {"base": round(float(c["rating"] * (4.6 - 4.8)), 4),
                   "media": round(float(q_media), 4),
                   "alta": round(float(c["rating"] * (4.95 - 4.8) + c["fav2"] + c["superhost"]), 4)}

    scan_days = store.scan_days("prices")
    model = {
        "version": 1,
        "generated": today.isoformat(),
        "city": cfg["city"],
        "currency": cfg["currency"],
        "nights_per_sample": nights_per,
        "start": today.isoformat(),
        "coef": {
            "intercept": round(float(c["intercept"]), 4),
            "zone": zone_coef,
            "bedrooms": {"0": round(float(c["br:0"]), 4), "1": 0.0, "2": round(float(c["br:2"]), 4),
                         "3": round(float(c["br:3"]), 4), "4": round(float(c["br:4"]), 4)},
            "bath": round(float(c["bath"]), 4),
            "guests": round(float(c["guests"]), 4),
            "quality": quality_adj,
        },
        "q_levels": Q_LEVELS,
        "q_values": [round(v, 4) for v in q_values],
        "sigma": round(fit["sigma"], 4),
        "idx": r3(idx),
        "base": r3(base_arr),
        "cov": cov,
        "pace": demand["C"],
        "theta": demand["theta"],
        "theta_city": demand["theta_city"],
        "nu": round(demand["nu"], 3),
        "occ_now": demand["u_now"],
        "occ_fin": demand["u_fin"],
        "demand_measured": [int(x) for x in demand["measured"]],
        "calibration": {"nu": round(demand["nu"], 3), "source": calib.get("source", "predefinito"),
                        "typical_theta": demand["typical_theta"]},
        "beta": round(beta["beta"], 3),
        "target_occupancy": mcfg["target_occupancy"],
        "band": mcfg["band"],
        "max_discount": mcfg.get("max_discount", 0.25),
        "max_premium": mcfg.get("max_premium", 0.40),
        "cost_share": mcfg.get("cost_share", 0.30),
        "fees": cfg.get("fees"),
        "aggressive": {"steps": mcfg.get("aggressive_steps", [[14, 10], [7, 20], [3, 30]])},
        "events": ev_out,
        "event_effect": {str(w): round(float(math.exp(learned[w]) - 1), 3) for w in learned},
        "zones": zones.public(),
        "groups": zones.groups,
        "market": market,
        "tracking": tracking,
        "single_night": _single_nights(store, today, nights_per, {d: math.exp(v) for d, v in zip(days, idx)}),
        "reliability": {
            "scan_days": len(scan_days),
            "first_scan": scan_days[0].isoformat() if scan_days else None,
            "last_scan": scan_days[-1].isoformat() if scan_days else None,
            "n_obs": int(fit["n"]),
            "n_listings": int(obs["id"].nunique()),
            "fit_mdape": quality["fit_mdape"],
            "seg_mdape": quality["seg_mdape"],
            "beta": beta,
            "curve_n": demand["curve"]["n"],
            "demand_days": demand["days"],
            "curve_live_weight": demand.get("live_weight", 0.0),
            "track_weight": demand.get("track_weight", 0.0),
            "track_pairs": int(len(trk["pairs"])),
            "track_booked": round(float(trk["pairs"]["booked"].sum()), 1) if len(trk["pairs"]) else 0.0,
            "event_n": {str(k): v for k, v in learned_n.items()},
            "nights_covered_60": int(sum(1 for x in cov[:61] if x >= 6)),
        },
        "curve": demand["curve"],
    }
    model["_listings"] = listing_rows
    model["_realized"] = realized
    return model


def _plain_windows(ci, weights: dict, nights_per: int) -> np.ndarray:
    """Finestre di sole notti feriali (né venerdì né sabato) e senza eventi: la notte "tipica"."""
    out = []
    for c in ci:
        c = int(c)
        out.append(all(weights.get(c + j, 0) < 2 and date.fromordinal(c + j).weekday() not in (4, 5) for j in range(nights_per)))
    return np.array(out, dtype=bool)


def _demand(cn: pd.DataFrame, days: list[int], today: date, nights_per: int, weights: dict, zones: Zones,
            cfg: dict, calib: dict, log, trk_pairs: pd.DataFrame | None = None) -> dict:
    """Domanda: ritmo tipico di prenotazione per anticipo e, per ogni notte, quanto corre rispetto al tipico.

    La ricerca dice, per ogni finestra di date, quanti alloggi sono ancora disponibili. Da qui:
      C[L]     ritmo tipico cumulato (vedi pace_from_profile);
      theta    velocità della singola notte: 1 = come il tipico, 2 = si riempie al doppio della velocità;
      nu       quante "notti indipendenti" pesa una finestra di più notti (1 = le notti si prenotano
               sempre insieme, 2 = del tutto separate); stimato dai calendari aperti di Inside Airbnb.
    """
    t_ord = days[0]
    n = len(days)
    lead = np.arange(n)
    nu = float(np.clip(calib.get("nu", 1.4), 1.0, float(nights_per)))
    empty_curve = {"lead": [0.0, 30.0, 120.0, 365.0], "logit": [float(logit(0.72)), float(logit(0.55)), float(logit(0.35)), float(logit(0.3))], "n": 0}
    w_arr = np.array([min(5, weights.get(d, 0)) for d in days])
    weekend = np.array([date.fromordinal(int(d)).weekday() in (4, 5) for d in days])

    def finish(curve, C, extra, measured, theta_adj, days_n, weight_live=0.0, weight_track=0.0):
        typ = dict(THETA_PRIOR)
        base_theta = np.array([typ[int(w)] for w in w_arr])
        raw = np.clip((C + np.nan_to_num(extra) + C0 * base_theta) / (C + C0), 0.3, 3.5)
        # effetto tipico per peso dell'evento, imparato dalle notti misurate
        for w in range(1, 6):
            m = measured & (w_arr == w) & (C > 0.05)
            if m.sum() >= 3:
                typ[w] = float((m.sum() * np.median(raw[m]) + 3 * THETA_PRIOR[w]) / (m.sum() + 3))
        # venerdì e sabato senza eventi: la curva tipica è quella feriale, il fine settimana corre di più
        m = measured & weekend & (w_arr < 2)
        typ_we = float((m.sum() * np.median(raw[m]) + 3.0) / (m.sum() + 3)) if m.sum() >= 3 else 1.0
        base_theta = np.array([typ_we if (we and w < 2) else typ[int(w)] for w, we in zip(w_arr, weekend)])
        typ["weekend"] = typ_we
        city = np.where(measured, np.clip((C + np.nan_to_num(extra) + C0 * base_theta) / (C + C0), 0.3, 3.5), base_theta)
        U_d = 1 - np.exp(-(C + np.nan_to_num(extra)))            # quota già "consumata" per quella notte
        theta = {}
        for s in SEG_CLASSES:
            theta[s] = {}
            for g in GROUPS:
                adj = U_d * (theta_adj["seg"][s] + theta_adj["grp"][g])
                th = np.clip((C + np.nan_to_num(extra) + adj + C0 * base_theta) / (C + C0), 0.3, 3.5)
                theta[s][g] = [round(float(x), 3) for x in np.where(measured, th, base_theta)]
        done = C + np.where(measured, np.nan_to_num(extra), (base_theta - 1) * C)
        occ_now = 1 - np.exp(-np.maximum(done, 0) / nu)
        occ_fin = 1 - np.exp(-np.maximum(done + city * (C[0] - C), 0) / nu)
        return {"curve": curve, "days": days_n, "nu": nu, "live_weight": round(float(weight_live), 3),
                "track_weight": round(float(weight_track), 3),
                "C": [round(float(x), 4) for x in C], "theta": theta,
                "theta_city": [round(float(x), 3) for x in city], "typical_theta": {str(k): round(v, 3) for k, v in typ.items()},
                "u_now": [round(float(x), 3) for x in occ_now], "u_fin": [round(float(x), 3) for x in np.maximum(occ_fin, occ_now)],
                "unavailable": [round(float(x), 3) for x in expit(curve_at(curve, lead.astype(float)) )],
                "measured": [bool(x) for x in measured]}

    zero_adj = {"seg": {s: np.zeros(n) for s in SEG_CLASSES}, "grp": {g: np.zeros(n) for g in GROUPS}}
    C_prior, _ = pace_from_profile(prior_profile(calib, n - 1), nu)
    if cn.empty:
        return finish(empty_curve, C_prior, np.full(n, np.nan), np.zeros(n, dtype=bool), zero_adj, 0)

    cn = cn.copy()
    cn["count"] = pd.to_numeric(cn["count"], errors="coerce")
    sup = cn[cn["checkin"] == ""]
    dated = cn[cn["checkin"] != ""].copy()
    dated["ci"] = dated["checkin"].map(lambda s: date.fromisoformat(s).toordinal())
    dated["sc"] = dated["scan"].map(lambda s: date.fromisoformat(s).toordinal())
    dated["lead"] = dated["ci"] - dated["sc"]

    def supply_of(scope: str) -> pd.Series:
        s = sup[sup["scope"] == scope].set_index("scan")["count"]
        return s[s > 0]

    def unavailable(scope: str, minus: str | None = None) -> pd.DataFrame:
        """U = 1 − disponibili/offerta, per finestra e giorno di scansione."""
        a = dated[dated["scope"] == scope][["scan", "checkin", "ci", "sc", "lead", "count"]].copy()
        s = supply_of(scope)
        if a.empty or s.empty:
            return pd.DataFrame(columns=["scan", "checkin", "ci", "sc", "lead", "U"])
        if minus:
            b = dated[dated["scope"] == minus].set_index(["scan", "checkin"])["count"]
            a["count"] = a["count"] - a.set_index(["scan", "checkin"]).index.map(b).to_numpy()
            s = (s - supply_of(minus).reindex(s.index)).dropna()
            if s.empty:
                return pd.DataFrame(columns=["scan", "checkin", "ci", "sc", "lead", "U"])
        s_all = s.sort_index()
        a["S"] = a["scan"].map(s_all)
        a["S"] = a["S"].fillna(float(s_all.iloc[-1]))
        a = a[(a["S"] > 0) & a["count"].notna()]
        a["U"] = (1 - a["count"] / a["S"]).clip(0.01, 0.99)
        return a

    city = unavailable("city")
    city = city[city["lead"] >= 0]
    if city.empty:
        return finish(empty_curve, C_prior, np.full(n, np.nan), np.zeros(n, dtype=bool), zero_adj, 0)
    # Il "tipico" è la notte feriale senza eventi. Le finestre con un venerdì o un sabato e quelle con eventi
    # si riempiono per conto loro, e la loro velocità la misura theta: mischiarle nella curva tipica confonde
    # l'effetto dell'anticipo con quello delle singole date (tra novembre e gennaio, per esempio, le date
    # lontane sono le feste, molto più piene). Se le feriali sono troppo poche: tutte tranne le grandi fiere.
    plain_mask = _plain_windows(city["ci"], weights, nights_per)
    plain = city[plain_mask]
    calm = city[np.array([max(weights.get(int(ci) + j, 0) for j in range(nights_per)) < BUSY_WEIGHT for ci in city["ci"]], dtype=bool)]
    if len(plain) >= 12 and int((plain["lead"] <= 45).sum()) >= 5:
        typical = plain
    elif len(calm) >= max(20, 0.5 * len(city)):
        typical = calm
    else:
        typical = city
    curve = booking_curve(typical)
    days_n = int(city["scan"].nunique())
    ln_ref, C, w_live = reference_curve(typical, curve, calib, n - 1, days_n, nu)

    # Dove gli alloggi vengono seguiti uno per uno (date entro track_days) il ritmo si misura direttamente:
    # quanti dei liberi vengono prenotati ogni giorno, a ogni anticipo. È la stessa data vista più volte,
    # quindi non risente delle differenze tra una data e l'altra; prende peso con i confronti raccolti.
    w_track = 0.0
    if trk_pairs is not None and len(trk_pairs):
        # tutte le date tranne le grandi fiere: seguendo gli alloggi la stessa data è vista più volte, quindi
        # qui non c'è da separare l'anticipo dalle date, e più confronti danno una misura più stabile
        tp = trk_pairs.copy()
        ords = [date.fromisoformat(x).toordinal() for x in tp["checkin"]]
        tp = tp[[max(weights.get(o + j, 0) for j in range(nights_per)) < BUSY_WEIGHT for o in ords]]
        if len(tp) >= 6:
            H = min(int(cfg["scan"].get("track_days", 30)) + 1, n - 1)
            h_trk, wgt = track.hazard_by_lead(tp, H)
            w = np.minimum(1.0, wgt / 60.0) * min(1.0, tp["scan"].nunique() / 4.0)
            dC = C[:-1] - C[1:]
            dC[:H] = (1 - w) * dC[:H] + w * h_trk
            C = np.concatenate([np.cumsum(dC[::-1])[::-1] + C[-1], [C[-1]]])
            w_track = float(np.mean(w[: min(H, 21)]))

    def nightly(u: pd.DataFrame, value, max_age: int = 4) -> np.ndarray:
        """Media per notte di un valore calcolato su ogni finestra letta di recente; NaN dove non c'è lettura."""
        out = np.full(n, np.nan)
        if u.empty:
            return out
        recent = u[u["sc"] >= t_ord - max_age].sort_values("sc").drop_duplicates("checkin", keep="last")
        acc: dict[int, list] = {}
        for ci, sc, U in zip(recent["ci"], recent["sc"], recent["U"]):
            v = value(float(U), int(np.clip(ci - sc, 0, n - 1)))
            for j in range(nights_per):
                k = ci + j - t_ord
                if 0 <= k < n:
                    acc.setdefault(k, []).append(v)
        for k, v in acc.items():
            out[k] = float(np.mean(v))
        return out

    # quanto la notte è più "consumata" del tipico a pari anticipo (0 = come il tipico)
    extra = nightly(city, lambda U, L: float(ln_ref[L] - math.log(max(1 - U, 1e-4))))
    measured = ~np.isnan(extra)
    lg_city = nightly(city, lambda U, L: float(logit(U)))

    adj = {"seg": {s: np.zeros(n) for s in SEG_CLASSES}, "grp": {g: np.zeros(n) for g in GROUPS}}

    def relative(u: pd.DataFrame) -> np.ndarray:
        """Quanto una taglia o una fascia è più tirata della città per quella notte, tolta la sua differenza abituale."""
        d = nightly(u, lambda U, L: float(logit(U))) - lg_city
        ok = ~np.isnan(d)
        if ok.sum() < 5:
            return np.zeros(n)
        d = d - np.median(d[ok])
        return np.clip(np.nan_to_num(d, nan=0.0) * 0.7, -1.0, 1.0)

    seg_ids = [s["id"] for s in cfg.get("segments", [])]
    if "br2" in seg_ids:
        adj["seg"]["c01"] = relative(unavailable("city", "seg:br2"))
        adj["seg"]["c2"] = relative(unavailable("seg:br2", "seg:br3") if "br3" in seg_ids else unavailable("seg:br2"))
        if "br3" in seg_ids:
            adj["seg"]["c3"] = relative(unavailable("seg:br3"))

    grp_of_tile = {}
    for tl in cfg["tiles"]:
        z = zones.zone_of((tl["sw"][0] + tl["ne"][0]) / 2, (tl["sw"][1] + tl["ne"][1]) / 2)
        grp_of_tile[tl["id"]] = zones.group_of(z) if z in zones.by_id else "periferia"
    tile_rows = dated[dated["scope"].str.startswith("tile:")].copy()
    tile_sup = sup[sup["scope"].str.startswith("tile:")]
    if not tile_rows.empty and not tile_sup.empty:
        tile_rows["tile"] = tile_rows["scope"].str[5:]
        tile_rows["grp"] = tile_rows["tile"].map(grp_of_tile)
        last_sup = tile_sup.sort_values("scan").drop_duplicates("scope", keep="last")
        s_map = {sc[5:]: float(v) for sc, v in zip(last_sup["scope"], last_sup["count"])}
        tile_rows["S"] = tile_rows["tile"].map(s_map)
        tile_rows = tile_rows[tile_rows["S"] > 0]
        for g in GROUPS:
            tg = tile_rows[tile_rows["grp"] == g]
            if tg.empty:
                continue
            agg = tg.groupby(["scan", "checkin", "ci", "sc", "lead"], as_index=False).agg(count=("count", "sum"), S=("S", "sum"))
            agg = agg[agg["S"] >= 25]
            if agg.empty:
                continue
            agg["U"] = (1 - agg["count"] / agg["S"]).clip(0.01, 0.99)
            adj["grp"][g] = relative(agg)
        if not np.any(adj["grp"]["fuori"]):
            adj["grp"]["fuori"] = adj["grp"]["periferia"]

    return finish(curve, C, extra, measured, adj, days_n, w_live, w_track)


def _attr(lst: pd.DataFrame, c: dict, zones: Zones) -> pd.Series:
    """Parte del prezzo (in log) spiegata dalle caratteristiche di ogni alloggio."""
    L = lst[lst["zone"].isin(zones.by_id)]
    return (L["zone"].map(lambda z: c.get(f"zone:{z}", 0.0) + c.get(f"grp:{zones.group_of(z)}", 0.0))
            + L["bedrooms_f"].clip(upper=4).astype(int).map(lambda b: c.get(f"br:{b}", 0.0))
            + c["bath"] * (L["baths_f"].clip(0.5, 4) - 1) + c["guests"] * np.log(L["guests_f"].clip(1, 12) / 4)
            + c["rating"] * (L["rating_f"].fillna(4.8) - 4.8) + c["new"] * L["is_new"]
            + c["fav1"] * (L["fav"] == 1) + c["fav2"] * (L["fav"] == 2) + c["superhost"] * (L["superhost"] == 1))


def _beta_from_tracking(trk: dict, attr: pd.Series, mcfg: dict) -> dict:
    """Sensibilità al prezzo: tra gli alloggi liberi a una lettura, chi è stato prenotato prima della successiva.

    A parità di date, si guarda quanto il prezzo (rispetto a quello atteso per le caratteristiche) sposta la
    probabilità di essere prenotati.
    """
    pairs = []
    for sm in trk["samples"]:
        a = attr.reindex(sm["id"]).to_numpy()
        ok = np.isfinite(a)
        if ok.sum() < 40:
            continue
        z = np.log(sm["pn"][ok]) - a[ok]
        z = z - np.median(z)
        keep = np.abs(z) < 1.2
        pairs.append((z[keep], sm["y"][ok][keep]))
    return fit_beta(pairs, float(mcfg["beta_prior"]), float(mcfg["beta_prior_sd"]))


def _detect_peaks(days, idx, base, cov, weights, today: date) -> list:
    """Notti molto più care del normale senza un evento noto: probabilmente un evento che non conosciamo."""
    out, run = [], []

    def flush():
        if not run:
            return
        up = float(np.exp(np.mean([u for _, u in run])) - 1)
        a, b = date.fromordinal(run[0][0]), date.fromordinal(run[-1][0])
        w = 4 if up > 0.6 else 3 if up > 0.35 else 2
        out.append({"id": f"picco-{a.isoformat()}", "name": "Picco di domanda rilevato dai prezzi", "start": a.isoformat(),
                    "end": b.isoformat(), "venue": "", "category": "mercato", "weight": w, "source": "mercato", "note": "",
                    "nights_from": a.isoformat(), "nights_to": b.isoformat(), "night_first": a.isoformat(),
                    "night_last": b.isoformat(), "uplift": round(up, 3), "measured": True})
        run.clear()

    for k, d in enumerate(days):
        up = idx[k] - base[d]
        near_big = any(weights.get(d + j, 0) >= 3 for j in (-1, 1))   # coda di un grande evento, non un picco nuovo
        if cov[k] >= 15 and up > math.log(1.22) and weights.get(d, 0) <= 1 and not near_big:
            if run and d - run[-1][0] > 1:
                flush()
            run.append((d, up))
        else:
            flush()
    flush()
    return out


def _market(obs: pd.DataFrame, fit: dict, lst: pd.DataFrame, zones: Zones, base_arr: list, today: date,
            booked: pd.DataFrame | None = None):
    """Statistiche per zona/camere/bagni/ospiti e catalogo compatto, a prezzi di una notte normale."""
    normal = float(np.median(base_arr[:45])) if base_arr else 0.0
    o = obs.copy()
    o["lp"] = np.log(o["pn"]) - np.array([fit["windows"].get(w, 0.0) for w in o["checkin"]]) + normal
    typ = o.groupby("id")["lp"].median()
    L = lst[lst.index.isin(typ.index)].copy()
    L["typ"] = np.exp(typ.reindex(L.index))
    L["brc"] = L["bedrooms_f"].clip(upper=4).astype(int)
    L["bac"] = L["baths_f"].map(lambda b: "1" if b < 1.5 else "1.5" if b < 2 else "2" if b < 2.5 else "3+")
    L["guc"] = L["guests_f"].map(lambda g: "1-2" if g <= 2 else "3-4" if g <= 4 else "5-6" if g <= 6 else "7+")

    def stats(g: pd.DataFrame) -> dict:
        v = g["typ"].to_numpy()
        return {"n": int(len(v)), "p25": round(float(np.percentile(v, 25))), "med": round(float(np.median(v))),
                "p75": round(float(np.percentile(v, 75)))}

    def by(col: str, order=None) -> list:
        rows = [{"key": str(k), **stats(g)} for k, g in L.groupby(col) if len(g) >= 3]
        if order:
            rows.sort(key=lambda r: order.index(r["key"]) if r["key"] in order else 99)
        return rows

    zone_order = [z["id"] for z in zones.zones]
    cross = {}
    for (z, b), g in L.groupby(["zone", "brc"]):
        cross.setdefault(z, {})[str(int(b))] = {"n": int(len(g)), "med": round(float(g["typ"].median()))}
    market = {
        "normal_night": "prezzi riportati a una notte normale dei prossimi 45 giorni, tutto incluso per l'ospite",
        "all": stats(L) if len(L) else {"n": 0},
        "zone": by("zone", zone_order),
        "group": by("group", GROUPS),
        "district": by("district"),
        "bedrooms": by("brc", ["0", "1", "2", "3", "4"]),
        "baths": by("bac", ["1", "1.5", "2", "3+"]),
        "guests": by("guc", ["1-2", "3-4", "5-6", "7+"]),
        "zone_bedrooms": cross,
    }
    cut = (today - timedelta(days=45)).isoformat()
    rows = []
    zidx = {z: i for i, z in enumerate(zone_order)}
    bk = booked if booked is not None else pd.DataFrame(columns=["n", "pn"])
    bn, bp = bk["n"].to_dict(), bk["pn"].to_dict()
    for r in L[L["last_seen"] >= cut].sort_values("typ").to_dict("records"):
        i = int(r["id"])
        rows.append([str(i), zidx.get(r["zone"], -1), int(r["brc"]), float(r["baths_f"]),
                     int(round(r["guests_f"])), int(bool(r["guests_est"])), int(round(r["typ"])),
                     None if r["rating"] != r["rating"] else round(float(r["rating"]), 2), int(r["reviews"] or 0),
                     int(r["fav"] or 0), round(float(r["lat"]), 4), round(float(r["lon"]), 4),
                     (r["name"] or "")[:60], int(bn.get(i, 0)), int(round(bp[i])) if i in bp else None])
    return market, rows
