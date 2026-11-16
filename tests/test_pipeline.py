"""Prova d'insieme su un mercato simulato di cui si conosce la verità: scanner -> modello -> prezzi."""
import math
from datetime import date, timedelta

import numpy as np
import pytest

from bnb import pricing, publish, scan
from bnb.config import load_settings
from bnb.sim import TRUE, SimClient, SimMarket
from bnb.store import Store

from conftest import DAYS, START


def test_scan_volume_is_bounded(world):
    run = world["run"]
    assert 500 < run["requests"] < 1400
    assert run["census"] == 15 and run["windows"] >= 45       # le 15 finestre entro 30 giorni lette per intero
    assert run["track"]["found"] == run["track"]["expected"] > 3000
    assert run["prices"] > 5000


def test_catalog(world):
    lst = world["store"].load_listings()
    assert len(lst) > 1100
    assert (lst["zone"] != "").mean() > 0.99
    assert lst["guests"].notna().mean() > 0.6         # capienze lette dalla pagina dell'annuncio, 60 al giorno
    assert set(lst["guests_src"].unique()) <= {"pdp", "ia", "est", ""}


def test_price_coefficients_recovered(world):
    c = world["model"]["coef"]
    for b in (0, 2, 3, 4):
        assert abs(c["bedrooms"][str(b)] - TRUE["br"][b]) < 0.07, b
    assert abs(c["bath"] - TRUE["bath"]) < 0.05
    assert abs(c["guests"] - TRUE["guests"]) < 0.07
    by_group = {}
    for z in world["model"]["zones"]:
        by_group.setdefault(z["group"], []).append(c["zone"][z["id"]])
    centro, cintura, periferia = (np.mean(by_group[g]) for g in ("centro", "cintura", "periferia"))
    assert 0.17 < centro - cintura < 0.34 and -0.32 < periferia - cintura < -0.10
    assert world["model"]["reliability"]["seg_mdape"] < 0.2


def test_event_effects_and_unknown_peak(world):
    m = world["model"]
    eff = {int(k): v for k, v in m["event_effect"].items()}
    assert eff[5] > eff[4] > eff[3] > eff[2] > 0
    assert abs(eff[5] - 0.9) < 0.25 and abs(eff[4] - 0.5) < 0.15 and abs(eff[3] - 0.25) < 0.1
    peaks = [e for e in m["events"] if e["source"] == "mercato"]
    assert any(abs((date.fromisoformat(p["start"]) - world["secret"]).days) <= 1 and p["uplift"] > 0.25 for p in peaks)
    assert len(peaks) <= 3                           # pochi falsi allarmi
    cosmo = next(e for e in m["events"] if e["name"].startswith("Cosmoprof"))
    assert cosmo["measured"] and cosmo["uplift"] > 0.5


def test_demand_matches_true_pickup(world):
    m, mk = world["model"], world["mk"]
    t0 = date.fromisoformat(m["start"])
    k0 = (t0 - mk.origin).days
    errs = []
    for k in range(3, 50, 2):
        if mk.weight[k0 + k] >= 2:
            continue
        F = mk.final_occ[k0 + k]
        g = F * mk._share(np.array([k]))[0]
        true = (F - g) / (1 - g)
        h = pricing.night_hazards(m, m["theta"]["c01"]["centro"][k], k)
        errs.append(abs((1 - np.prod(1 - h)) - true))
    assert np.mean(errs) < 0.08
    # le notti con un grande evento si riempiono più in fretta delle altre
    hot = [m["theta_city"][k] for k in range(5, 200) if mk.weight[k0 + k] in (4, 5)]
    calm = [m["theta_city"][k] for k in range(5, 200) if mk.weight[k0 + k] == 0]
    assert np.mean(hot) > np.mean(calm) + 0.2
    assert abs(m["occ_fin"][20] - mk.final_occ[k0 + 20]) < 0.15


def test_price_sensitivity_learned(world):
    b = world["model"]["reliability"]["beta"]
    assert b["pairs"] >= 100 and b["n"] > 50000       # ogni finestra seguita dà un confronto ogni due giorni
    assert -4.0 < world["model"]["beta"] < -1.8      # verità: -3, attenuata dalla qualità non osservata


def rec(world, strategy, **kw):
    t = world["today"]
    return pricing.recommend(world["model"], "c_maggiore", 2, 1, 4, t + timedelta(days=1), t + timedelta(days=75), strategy, **kw)


def test_strategies(world):
    resa, occ = rec(world, "resa"), rec(world, "occupazione")
    assert resa["avg_price"] > occ["avg_price"]
    assert occ["occupancy"] >= resa["occupancy"]
    assert resa["net"] >= occ["net"]                       # la resa massimizza il margine atteso
    assert resa["net"] >= rec(world, "resa")["market_revenue"] - resa["cost"] * 75 * resa["market_occupancy"] - 1
    for x in resa["nights"] + occ["nights"]:
        assert x["floor"] - 1 <= x["price"] <= x["cap"] + 1
        assert 0 <= x["sell"] <= 1
    # più costi variabili -> conviene tenere prezzi più alti; prezzo minimo rispettato
    assert rec(world, "resa", cost=80)["avg_price"] > rec(world, "resa", cost=0)["avg_price"]
    floor = rec(world, "occupazione", min_price=200)
    assert min(x["price"] for x in floor["nights"]) >= 200
    # avvicinandosi alla data, se la notte è ancora libera il prezzo consigliato scende
    far = next(x for x in resa["nights"] if x["lead"] == 60)
    plan = [p["price"] for p in far["plan"]]
    assert plan == sorted(plan, reverse=True) and far["price"] >= plan[0]


def test_event_nights_cost_more(world):
    m, t = world["model"], world["today"]
    r = pricing.recommend(m, "fiera", 1, 1, 2, date(2027, 3, 10), date(2027, 3, 21), "resa")
    cosmo = [x for x in r["nights"] if "Cosmoprof Worldwide Bologna" in x["events"]]
    normal = [x for x in r["nights"] if x["weight"] == 0]
    assert len(cosmo) == 4 and normal
    assert np.mean([x["price"] for x in cosmo]) > 1.5 * np.mean([x["price"] for x in normal])
    bigger = pricing.recommend(m, "fiera", 3, 2, 6, date(2027, 3, 10), date(2027, 3, 12), "resa")
    assert bigger["market_avg"] > 1.4 * pricing.recommend(m, "fiera", 1, 1, 2, date(2027, 3, 10), date(2027, 3, 12), "resa")["market_avg"]


def test_reliability_grows_with_history(world):
    r = rec(world, "resa")["reliability"]
    assert r["label"] in ("media", "alta") and 0.4 < r["score"] <= 1
    assert r["parts"]["storico"] == pytest.approx(DAYS / 45, abs=0.01)
    assert r["comparables"] >= 30


def test_published_files(world):
    st = world["store"]
    status = st.read_json("public/status.json")
    assert status["has_model"] and status["scan_days"] == DAYS and not status["warnings"]
    mj = st.read_json("public/model.json")
    assert "_listings" not in mj and len(mj["idx"]) == 366 and len(mj["pace"]) == 366
    lj = st.read_json("public/listings.json")
    assert len(lj["rows"]) > 1000 and len(lj["rows"][0]) == len(lj["fields"])
    assert all(isinstance(r[0], str) for r in lj["rows"][:5])         # id come testo: in JavaScript i numeri grandi si rovinano
    hist = st.load_history("metrics")
    assert len(hist) == 1 and math.isfinite(float(hist["beta"].iloc[0]))


def test_backup_contains_everything(world, tmp_path):
    import zipfile

    dest = publish.make_backup(world["store"], str(tmp_path / "b.zip"), log=lambda *a: None)
    names = zipfile.ZipFile(dest).namelist()
    assert "data/listings.csv" in names and "public/model.json" in names and "config/events.yaml" in names
    assert sum(n.startswith("data/prices/") for n in names) == DAYS
    assert sum(n.startswith("data/counts/") for n in names) == DAYS


def test_budget_stop_keeps_data(tmp_path):
    cfg = load_settings()
    mk = SimMarket(START, n=400, seed=5, days=3)
    store = Store(str(tmp_path))
    run = scan.run_scan(cfg, store, SimClient(mk, START, max_requests=200), START, log=lambda *a: None)
    assert "tetto" in run["stopped"]
    assert run["prices"] > 500 and len(store.load_listings()) > 100      # salvato comunque ciò che era stato letto


def test_first_day_is_already_usable(tmp_path):
    """Con una sola scansione le probabilità di prenotazione non devono essere degeneri."""
    from bnb import model

    cfg = load_settings()
    cfg["model"]["nu"] = 2.0
    mk = SimMarket(START, n=900, seed=11, days=3)
    store = Store(str(tmp_path))
    run = scan.run_scan(cfg, store, SimClient(mk, START), START, log=lambda *a: None)
    m = model.build(store, cfg, START, log=lambda *a: None)
    assert m is not None and m["reliability"]["scan_days"] == 1
    assert m["reliability"]["curve_live_weight"] < 0.1          # il primo giorno comanda la curva di partenza
    assert all(a >= b for a, b in zip(m["pace"], m["pace"][1:]))  # il ritmo cumulato cresce avvicinandosi alla data
    sells, typical = [], []
    for k in (5, 10, 20, 40):
        sells.append(1 - np.prod(1 - pricing.night_hazards(m, m["theta"]["c01"]["centro"][k], k)))
        typical.append(1 - np.prod(1 - pricing.night_hazards(m, 1.0, k)))     # stessa notte "tipica", anticipi diversi
    assert all(0.05 < s < 0.9 for s in sells), sells
    assert typical == sorted(typical)                             # più tempo davanti, più probabilità di vendere
    r = pricing.recommend(m, "c_maggiore", 1, 1, 2, START + timedelta(days=1), START + timedelta(days=45), "resa")
    o = pricing.recommend(m, "c_maggiore", 1, 1, 2, START + timedelta(days=1), START + timedelta(days=45), "occupazione")
    assert r["avg_price"] > o["avg_price"] and 0.1 < r["occupancy"] < 0.95
    assert r["reliability"]["label"] in ("bassa", "media")
    publish.publish(store, m, START, run, cfg)
