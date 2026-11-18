"""Prenotazioni rilevate dal confronto tra letture complete: contro la verità del mercato simulato."""
import tempfile
from datetime import date, timedelta

import numpy as np
import pytest

from bnb import model, scan, track
from bnb.config import load_settings
from bnb.sim import SimClient, SimMarket
from bnb.store import Store

START, DAYS = date(2026, 11, 16), 9


@pytest.fixture(scope="module")
def tracked():
    cfg = load_settings()
    cfg["model"]["nu"] = 2.0
    mk = SimMarket(START, n=1500, seed=5, days=DAYS + 5)
    store = Store(tempfile.mkdtemp())
    runs = []
    for i in range(DAYS):
        today = START + timedelta(days=i)
        client = SimClient(mk, today, jitter=0.02)       # l'ordine dei risultati cambia tra una pagina e l'altra
        if i == 4:
            # due annunci quasi sempre liberi nelle prossime settimane: uno viene tolto dal sito, l'altro chiude il calendario
            t = (today - mk.origin).days
            free = (~mk.booked[:, t:t + 26] & ~mk.blocked[:, t:t + 26]).sum(axis=1)
            pick = np.flatnonzero((free >= 22) & (mk.min_n <= 2) & (mk.open_days > 400))[:2]
            mk.blocked[pick[0], t:] = True
            mk.blocked[pick[1], t + 2:t + 24] = True
            special = (int(mk.ids[pick[0]]), int(mk.ids[pick[1]]))
        runs.append(scan.run_scan(cfg, store, client, today, log=lambda *a: None))
    return {"cfg": cfg, "mk": mk, "store": store, "runs": runs, "today": today, "trk": track.detect(store, today),
            "gone": special[0], "blocked": special[1]}


def _truth(mk, a: str, b: str, w: str):
    """Chi era davvero libero per la finestra w il giorno a, e chi di loro è stato prenotato entro il giorno b."""
    da, db, ci = date.fromisoformat(a), date.fromisoformat(b), date.fromisoformat(w)
    k = (ci - mk.origin).days
    free_a = (~mk.history[da][:, k:k + 2].any(axis=1) & ~mk.blocked[:, k:k + 2].any(axis=1) & (mk.min_n <= 2)
              & (mk.open_days >= (ci - da).days))
    taken = free_a & mk.history[db][:, k:k + 2].any(axis=1)
    return set(mk.ids[free_a].tolist()), set(mk.ids[taken].tolist())


def test_complete_reads_survive_unstable_ordering(tracked):
    for run in tracked["runs"]:
        t = run["track"]
        assert t["windows"] == 15 and t["complete"] >= 13
        assert t["found"] >= 0.97 * t["expected"]
        assert run["requests"] < 1300 and not run["stopped"]


def test_bookings_match_the_truth(tracked):
    mk, trk = tracked["mk"], tracked["trk"]
    E, P = trk["events"], trk["pairs"]
    assert len(P) >= 60 and len(E) > 500
    hit = miss = false = 0
    for r in P[P["sure"] == 1].itertuples():
        _, taken = _truth(mk, r.prev, r.scan, r.checkin)
        found = set(E[(E["scan"] == r.scan) & (E["checkin"] == r.checkin) & (E["status"] == "prenotato")]["id"])
        hit += len(found & taken)
        miss += len(taken - found)
        false += len(found - taken)
    assert hit > 400
    assert hit / (hit + false) > 0.93            # quasi tutti quelli contati sono prenotazioni vere
    assert hit / (hit + miss) > 0.90             # e quasi tutte le prenotazioni vere vengono viste


def test_unconfirmed_bookings_are_weighted(tracked):
    """L'ultima lettura non ha ancora conferma: chi è sparito conta per la quota che di solito non ricompare."""
    mk, trk = tracked["mk"], tracked["trk"]
    assert 0.02 < trk["back_rate"] < 0.5 and trk["checked"] > 300
    last = trk["pairs"][trk["pairs"]["sure"] == 0]
    assert len(last) >= 10
    true_n = sum(len(_truth(mk, r.prev, r.scan, r.checkin)[1]) for r in last.itertuples())
    assert abs(last["booked"].sum() - true_n) < 0.2 * true_n


def test_withdrawn_and_blocked_are_not_bookings(tracked):
    E = tracked["trk"]["events"]
    gone = E[E["id"] == tracked["gone"]]
    assert len(gone) >= 5 and set(gone["status"]) == {"ritirato"}            # sparito da tutte le date: tolto, non prenotato
    blk = E[E["id"] == tracked["blocked"]]
    assert len(blk) >= 5 and (blk["status"] == "blocco").mean() > 0.7        # sparito in blocco: calendario chiuso


def test_pace_follows_the_listings(tracked):
    """Il ritmo misurato seguendo gli alloggi è vicino a quello vero del mercato simulato."""
    mk, P = tracked["mk"], tracked["trk"]["pairs"]
    h, wgt = track.hazard_by_lead(P, 31)
    for L in (3, 7, 12, 20):
        true = []
        for r in P.itertuples():
            if r.lead <= L < r.lead + r.gap:
                free, taken = _truth(mk, r.prev, r.scan, r.checkin)
                true.append(-np.log(1 - len(taken) / len(free)) / r.gap)
        assert wgt[L] > 20 and abs(h[L] - np.mean(true)) < 0.35 * np.mean(true) + 0.004


def test_model_uses_the_bookings(tracked):
    m = model.build(tracked["store"], tracked["cfg"], tracked["today"], log=lambda *a: None)
    t = m["tracking"]
    assert t["pairs"] >= 60 and t["booked"] > 500 and t["risk"] > 10000
    assert t["price"]["all"]["n"] > 300 and t["price"]["all"]["rel"] < -0.03       # si prenota prima chi costa meno
    assert set(t["price"]["bedrooms"]) >= {"1", "2"} and len(t["by_lead"]) >= 15
    assert t["windows"] and {"checkin", "risk", "booked"} <= set(t["windows"][0])
    rel = m["reliability"]
    assert rel["track_pairs"] >= 60 and 0.3 < rel["track_weight"] <= 1.0
    assert rel["beta"]["pairs"] >= 50 and -5.0 < m["beta"] < -1.6                  # vero: -3
    rows = m["_listings"]
    assert len(rows[0]) == 15 and sum(1 for r in rows if r[13] > 0) > 200


def test_realized_for_the_app(tmp_path):
    """Riscontro: per ogni annuncio le finestre prenotate (date come giorni dopo since) e le letture in cui era libero."""
    from datetime import date as _date, timedelta as _td

    from bnb import model, publish, scan, track
    from bnb.config import load_settings
    from bnb.sim import SimClient, SimMarket
    from bnb.store import Store

    cfg = load_settings()
    start = _date(2026, 3, 2)
    mk = SimMarket(start, n=500, seed=3, days=8)
    store = Store(str(tmp_path))
    for k in range(0, 5, 2):                     # tre scansioni a due giorni di distanza: due confronti per finestra
        day = start + _td(days=k)
        scan.run_scan(cfg, store, SimClient(mk, day), day, log=lambda *a: None)
    today = start + _td(days=4)
    trk = track.detect(store, today)
    r = track.realized(trk, today)
    assert r["since"] == (today - _td(days=45)).isoformat()
    assert r["booked"] and r["observed"]
    for lid, ev in r["booked"].items():
        assert r["observed"][lid][1] == len(ev) <= r["observed"][lid][0]
        for arr, seen, pn, sure in ev:
            assert 0 < seen <= arr and (pn is None or pn > 0) and sure in (0, 1)
    m = model.build(store, cfg, today, log=lambda *a: None)
    publish.publish(store, m, today, None, cfg, log=lambda *a: None)
    pub = store.read_json("public/realizzo.json", {})
    assert pub["fields"] == ["arrivo", "rilevata", "prezzo_notte", "confermata"] and pub["booked"] == r["booked"]
    assert "_realized" not in store.read_json("public/model.json", {})
