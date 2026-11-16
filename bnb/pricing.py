"""Prezzo consigliato per un alloggio e un periodo. L'app fa lo stesso identico calcolo (app/web/pricing.js).

Per ogni notte il modello dà:
  m      prezzo mediano dei comparabili per quella notte (caratteristiche × effetto della data)
  h[L]   probabilità che, a L giorni dalla data, una notte libera venga prenotata quel giorno a prezzo m
  beta   quanto la probabilità scende alzando il prezzo (e sale abbassandolo)

MASSIMA RESA: programmazione dinamica. A ogni giorno che manca si sceglie il prezzo che rende massimo
  [probabilità di vendere oggi × incasso] + [probabilità di non vendere × valore delle occasioni future].
  Lontano dalla data conviene tenere alto (ci sono ancora molte occasioni); avvicinandosi, se la notte è
  ancora libera, il prezzo scende. Il prezzo mostrato è quello da impostare oggi.

MASSIMA OCCUPAZIONE: il prezzo più alto che, tenuto fermo fino alla data, lascia almeno l'obiettivo
  (90%) di probabilità di vendere la notte; se non basta nemmeno il minimo, il minimo.

In entrambi i casi il prezzo resta in una fascia attorno al mercato (percentili dei comparabili,
e comunque non oltre -25% / +40%).
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np

GRID = 25
H_MIN = 0.0005     # anche nei giorni più fiacchi resta una piccola probabilità di prenotazione


def seg_class(bedrooms: int) -> str:
    return "c01" if bedrooms <= 1 else "c2" if bedrooms == 2 else "c3"


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _expit(x):
    return 1 / (1 + np.exp(-x))


def attr_level(model: dict, zone: str, bedrooms: int, baths: float, guests: int, quality: str = "media") -> float:
    c = model["coef"]
    return (c["intercept"] + c["zone"].get(zone, 0.0) + c["bedrooms"][str(min(max(int(bedrooms), 0), 4))]
            + c["bath"] * (min(max(float(baths), 0.5), 4.0) - 1.0)
            + c["guests"] * math.log(min(max(float(guests), 1.0), 12.0) / 4.0)
            + c["quality"].get(quality, c["quality"]["media"]))


def price_band(model: dict) -> tuple[float, float]:
    """Estremi (in log) del prezzo relativo ai comparabili."""
    q = dict(zip(model["q_levels"], model["q_values"]))
    lo = max(q[model["band"][0]], math.log(1 - model.get("max_discount", 0.25)))
    hi = min(q[model["band"][1]], math.log(1 + model.get("max_premium", 0.40)))
    return lo, max(hi, lo + 0.05)


def night_hazards(model: dict, theta: float, lead0: int) -> np.ndarray:
    """h[j] = probabilità di prenotazione nel giorno in cui mancano lead0-j giorni (a prezzo di mercato).

    Ritmo tipico di quel giorno (differenza della curva `pace`) × velocità della notte (theta),
    riportato dalla finestra di più notti alla singola notte (nu).
    """
    if lead0 <= 0:
        return np.zeros(0)
    C = np.asarray(model["pace"], dtype=float)
    L = np.arange(lead0, 0, -1)
    dC = np.maximum(C[L - 1] - C[L], 0.0)
    return np.clip(1 - np.exp(-(theta / model["nu"]) * dC), H_MIN, 0.95)


def default_cost(model: dict, attr: float) -> float:
    """Costo variabile di partenza per notte prenotata: una quota del prezzo tipico dei comparabili."""
    base = sorted(model["base"][:45])
    return round(model.get("cost_share", 0.30) * math.exp(attr + base[len(base) // 2]))


DEFAULT_FEES = {
    "vat": 0.22,
    "channels": [
        {"id": "airbnb", "name": "Airbnb", "fee": 15.5, "vat": True, "tax": "portal"},
        {"id": "booking", "name": "Booking.com", "fee": 16.5, "vat": True, "tax": "host"},
        {"id": "vrbo", "name": "Vrbo", "fee": 8.0, "vat": True, "tax": "host"},
        {"id": "direct", "name": "Prenotazione diretta", "fee": 0.0, "vat": False, "tax": "host"},
    ],
    "tourist_tax": {"year": 2026, "portal_rate": 0.105, "cap": 7.0,
                    "bands": [[31, 4.0], [71, 5.8], [121, 6.5], [None, 7.0]], "max_nights": 5},
}


def fees_of(model: dict) -> dict:
    return model.get("fees") or DEFAULT_FEES


def channel(model: dict, channel_id: str | None) -> dict:
    chans = fees_of(model)["channels"]
    return next((c for c in chans if c["id"] == channel_id), chans[0])


def fee_share(model: dict, channel_id: str | None = None, fee: float | None = None, vat: bool | None = None) -> float:
    """Quota del prezzo che il portale trattiene: la commissione (in %), con l'IVA sopra se dovuta."""
    ch = channel(model, channel_id)
    pct = ch["fee"] if fee is None else float(fee)
    with_vat = ch.get("vat", False) if vat is None else bool(vat)
    return min(0.9, max(0.0, pct / 100.0 * (1 + (fees_of(model).get("vat", 0.22) if with_vat else 0.0))))


def tourist_tax(model: dict, price: float, payers: float, mode: str) -> float:
    """Imposta di soggiorno per una notte, per tutti gli ospiti che la pagano.

    mode "portal": la riscuote il portale che incassa l'affitto (Airbnb) come percentuale del prezzo, con un
    tetto a persona. mode "host": la riscuote chi ospita, a scaglioni sul prezzo a persona a notte.
    """
    tt = fees_of(model).get("tourist_tax")
    if not tt or payers <= 0 or price <= 0:
        return 0.0
    if mode == "portal":
        return float(min(tt["portal_rate"] * price, tt["cap"] * payers))
    each = price / payers
    for limit, amount in tt["bands"]:
        if limit is None or each < limit:
            return float(amount * payers)
    return float(tt["bands"][-1][1] * payers)


def solve_night(h: np.ndarray, beta: float, m: float, lo: float, hi: float, strategy: str, target: float,
                cost: float = 0.0, floor_price: float = 0.0, keep: float = 1.0) -> dict:
    """Prezzo di oggi per una notte, con probabilità di venderla e incasso atteso.

    keep = quota del prezzo che resta dopo la commissione del portale: la massima resa guarda a quella.
    """
    lo_eff = max(lo, math.log(max(floor_price, 1e-9) / m))     # mai sotto il prezzo minimo dell'utente
    hi_eff = max(hi, lo_eff)
    lnr = np.linspace(lo_eff, hi_eff, GRID)
    r = np.exp(lnr)
    if len(h) == 0:
        return {"rel": float(r[0]), "sell": 0.0, "gross": 0.0, "net": 0.0, "sell_market": 0.0, "plan": []}
    lg = _logit(h)
    sell_market = float(1 - np.prod(1 - h))
    if strategy == "occupazione":
        hr = _expit(lg[:, None] + beta * lnr[None, :])          # giorni × prezzi
        sell = 1 - np.prod(1 - hr, axis=0)
        ok = np.flatnonzero(sell >= target)
        j = int(ok[-1]) if len(ok) else 0
        return {"rel": float(r[j]), "sell": float(sell[j]), "gross": float(r[j] * m * sell[j]),
                "net": float((r[j] * m * keep - cost) * sell[j]), "sell_market": sell_market, "plan": []}
    V = G = Q = 0.0
    policy = []
    for k in range(len(h) - 1, -1, -1):                          # dall'ultimo giorno utile a oggi
        hr = _expit(lg[k] + beta * lnr)
        val = hr * (r * m * keep - cost) + (1 - hr) * V
        j = int(np.argmax(val))
        G = hr[j] * r[j] * m + (1 - hr[j]) * G
        Q = hr[j] + (1 - hr[j]) * Q
        V = float(val[j])
        policy.append(float(r[j]))
    policy.reverse()                                             # policy[i]: prezzo quando mancano len(h)-i giorni
    lead0 = len(h)
    plan = [{"lead": L, "rel": policy[lead0 - L]} for L in (30, 14, 7, 3, 1) if L < lead0]
    return {"rel": policy[0], "sell": float(Q), "gross": float(G), "net": float(V), "sell_market": sell_market, "plan": plan}


DEFAULT_STEPS = [[14, 10.0], [7, 20.0], [3, 30.0]]     # [giorni prima della notte, sconto % sul prezzo di mercato]


def clean_steps(steps) -> list:
    """Gradini validi, dal più lontano al più vicino alla data."""
    out = []
    for x in steps or []:
        try:
            days, pct = int(x[0]), float(x[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= days <= 120 and 0 <= pct <= 80:
            out.append([days, pct])
    return sorted(out, key=lambda x: -x[0])


def ladder_rel(lead: int, steps: list) -> float:
    """Prezzo rispetto al mercato a `lead` giorni dalla notte: vale lo sconto più forte tra i gradini raggiunti."""
    d = 0.0
    for days, pct in steps:
        if lead <= days:
            d = max(d, pct)
    return max(0.05, 1.0 - d / 100.0)


def solve_aggressive(h: np.ndarray, beta: float, m: float, steps: list, cost: float = 0.0,
                     floor_price: float = 0.0, keep: float = 1.0) -> dict:
    """Strategia aggressiva: prezzo di mercato, poi sconti a gradini se la notte è ancora libera.

    È una regola fissa, non un'ottimizzazione: qui se ne calcola l'esito atteso (probabilità di vendere,
    incasso, guadagno) con lo stesso modello della domanda delle altre strategie.
    """
    k = len(h)
    floor_rel = max(floor_price, 0.0) / m
    rel_at = lambda L: max(ladder_rel(L, steps), floor_rel)          # mai sotto il prezzo minimo dell'utente
    today = rel_at(k)
    plan, last = [], today
    for days, _ in steps:
        if days < k and abs(rel_at(days) - last) > 1e-12:
            last = rel_at(days)
            plan.append({"lead": int(days), "rel": float(last)})
    if k == 0:
        return {"rel": float(today), "sell": 0.0, "gross": 0.0, "net": 0.0, "sell_market": 0.0, "plan": plan}
    rel = np.array([rel_at(L) for L in range(k, 0, -1)])              # h[i] è il giorno in cui mancano k-i giorni
    hr = _expit(_logit(h) + beta * np.log(rel))
    surv, gross, net, sold = 1.0, 0.0, 0.0, 0.0
    for i in range(k):
        gross += surv * hr[i] * rel[i] * m
        net += surv * hr[i] * (rel[i] * m * keep - cost)
        sold += surv * hr[i]
        surv *= 1 - hr[i]
    return {"rel": float(today), "sell": float(sold), "gross": float(gross), "net": float(net),
            "sell_market": float(1 - np.prod(1 - h)), "plan": plan}


def recommend(model: dict, zone: str, bedrooms: int, baths: float, guests: int, start: date, end: date,
              strategy: str = "resa", quality: str = "media", min_price: float = 0.0, cost: float | None = None,
              fee: float = 0.0, payers: float = 2.0, tax_mode: str = "portal", steps: list | None = None) -> dict:
    """Notti da `start` a `end` comprese. strategy: resa | occupazione | aggressiva.

    cost      costo variabile per notte prenotata (None = valore di partenza)
    fee       quota del prezzo trattenuta dal portale (vedi fee_share); 0 = nessuna commissione
    payers    ospiti che pagano l'imposta di soggiorno; tax_mode dice chi la riscuote (vedi tourist_tax)
    steps     gradini della strategia aggressiva, [[giorni, sconto %], ...] (None = quelli del modello)
    """
    keep = 1.0 - fee
    steps = clean_steps(steps if steps is not None else (model.get("aggressive") or {}).get("steps") or DEFAULT_STEPS)
    t0 = date.fromisoformat(model["start"])
    lo, hi = price_band(model)
    q = dict(zip(model["q_levels"], model["q_values"]))
    beta = float(model["beta"])
    target = float(model["target_occupancy"])
    attr = attr_level(model, zone, bedrooms, baths, guests, quality)
    grp = next((z["group"] for z in model["zones"] if z["id"] == zone), "cintura")
    thetas = model["theta"][seg_class(int(bedrooms))][grp]
    if cost is None:
        cost = default_cost(model, attr)
    ev_by_night: dict[str, list] = {}
    for e in model["events"]:
        a, b = date.fromisoformat(e["night_first"]), date.fromisoformat(e["night_last"])
        for i in range((b - a).days + 1):
            ev_by_night.setdefault((a + timedelta(days=i)).isoformat(), []).append(e)
    nights = []
    d = start
    while d <= end:
        k = (d - t0).days
        if 0 <= k < len(model["idx"]):
            m = math.exp(attr + model["idx"][k])
            hz = night_hazards(model, thetas[k], k)
            if strategy == "aggressiva":
                s = solve_aggressive(hz, beta, m, steps, cost, min_price, keep)
            else:
                s = solve_night(hz, beta, m, lo, hi, strategy, target, cost, min_price, keep)
            price = max(1, round(s["rel"] * m))
            tax = tourist_tax(model, price, payers, tax_mode)
            evs = sorted(ev_by_night.get(d.isoformat(), []), key=lambda e: -e["weight"])
            nights.append({
                "date": d.isoformat(), "lead": k, "price": int(price), "sell": round(s["sell"], 3),
                "gross": round(s["gross"], 1), "net": round(s["net"], 1),
                "fee": round(price * fee, 2), "payout": round(price * keep, 2), "tax": round(tax, 2),
                "market": round(m), "sell_market": round(s["sell_market"], 3),
                "low": round(m * math.exp(q[25])), "high": round(m * math.exp(q[75])),
                "floor": round(m * math.exp(lo)), "cap": round(m * math.exp(hi)),
                "uplift": round(math.exp(model["idx"][k] - model["base"][k]) - 1, 3),
                "occ_now": model["occ_now"][k], "occ_fin": model["occ_fin"][k],
                "measured": model["cov"][k] >= 6, "events": [e["name"] for e in evs],
                "weight": evs[0]["weight"] if evs else 0,
                "plan": [{"lead": p["lead"], "price": int(round(p["rel"] * m))} for p in s["plan"]],
            })
        d += timedelta(days=1)
    n = len(nights)
    return {
        "strategy": strategy, "cost": cost, "nights": nights, "fee": fee, "tax_mode": tax_mode, "payers": payers, "steps": steps,
        "fees": round(sum(x["gross"] for x in nights) * fee),
        "tax": round(sum(x["tax"] * x["sell"] for x in nights)),
        "avg_price": round(sum(x["price"] for x in nights) / n) if n else 0,
        "occupancy": round(sum(x["sell"] for x in nights) / n, 3) if n else 0.0,
        "revenue": round(sum(x["gross"] for x in nights)),
        "net": round(sum(x["net"] for x in nights)),
        "market_avg": round(sum(x["market"] for x in nights) / n) if n else 0,
        "market_occupancy": round(sum(x["sell_market"] for x in nights) / n, 3) if n else 0.0,
        "market_revenue": round(sum(x["market"] * x["sell_market"] for x in nights)),
        "reliability": reliability(model, zone, int(bedrooms), nights),
    }


def reliability(model: dict, zone: str, bedrooms: int, nights: list) -> dict:
    """Quanto fidarsi: storico raccolto, numero di comparabili, notti misurate davvero, domanda, sensibilità."""
    rel = model["reliability"]
    comp = ((model["market"].get("zone_bedrooms") or {}).get(zone) or {}).get(str(min(bedrooms, 4)), {}).get("n", 0)
    parts = {
        "storico": min(1.0, rel["scan_days"] / 45),
        "comparabili": min(1.0, comp / 30),
        "date": (sum(1 for x in nights if x["measured"]) / len(nights)) if nights else 0.0,
        "domanda": min(1.0, (rel.get("demand_days") or 0) / 21),
        "sensibilita": float((rel.get("beta") or {}).get("weight", 0.0)),
    }
    score = (0.25 * parts["storico"] + 0.25 * parts["comparabili"] + 0.25 * parts["date"]
             + 0.15 * parts["domanda"] + 0.10 * parts["sensibilita"])
    label = "alta" if score >= 0.7 else "media" if score >= 0.4 else "bassa"
    return {"score": round(score, 3), "label": label, "parts": {k: round(v, 3) for k, v in parts.items()}, "comparables": int(comp)}
