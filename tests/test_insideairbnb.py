import gzip
from datetime import date, timedelta

import numpy as np
import pandas as pd

from bnb import insideairbnb as ia


def test_find_snapshot_picks_latest():
    page = ('<a href="https://data.insideairbnb.com/italy/emilia-romagna/bologna/2026-06-20/data/listings.csv.gz">x</a>'
            '<a href="https://data.insideairbnb.com/italy/emilia-romagna/bologna/2026-09-19/data/listings.csv.gz">x</a>'
            '<a href="https://data.insideairbnb.com/italy/toscana/florence/2026-09-25/data/listings.csv.gz">x</a>')
    s = ia.find_snapshot(page)
    assert s["date"] == "2026-09-19" and s["calendar"].endswith("/bologna/2026-09-19/data/calendar.csv.gz")
    assert ia.find_snapshot("<html></html>") is None


def test_parse_listings_and_baths():
    csv = ("id,name,latitude,longitude,room_type,accommodates,bathrooms_text,bedrooms,beds,minimum_nights,"
           "review_scores_rating,number_of_reviews,neighbourhood_cleansed,has_availability,property_type\n"
           '1,"A, b",44.49,11.34,Entire home/apt,4,1.5 baths,2,3,2,4.8,10,Santo Stefano,t,Entire condo\n'
           "2,C,44.50,11.35,Private room,2,1 shared bath,1,1,1,,0,Navile,t,Private room in home\n"
           "3,D,44.51,11.36,Entire home/apt,6,,3,,31,4.5,3,Navile,f,Entire rental unit\n")
    df = ia.parse_listings(gzip.compress(csv.encode()))
    assert list(df["id"]) == [1, 2, 3]
    assert df.loc[0, "baths"] == 1.5 and df.loc[1, "baths"] == 1.0 and np.isnan(df.loc[2, "baths"])
    assert df.loc[0, "guests"] == 4 and df.loc[2, "min_nights"] == 31 and df.loc[0, "district"] == "Santo Stefano"
    assert ia.parse_baths("Shared half-bath") == 0.5 and ia.parse_baths("") is None


def _calendar(n, days, independent, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        booked = np.zeros(days, bool)
        if independent:
            booked = rng.random(days) < (0.15 + 0.6 * np.exp(-np.arange(days) / 40))
        else:
            d = 0
            while d < days:
                if rng.random() < 0.05 + 0.5 * np.exp(-d / 40):
                    ln = 1 + rng.geometric(1 / 1.6)
                    booked[d:d + ln] = True
                    d += ln
                else:
                    d += 1
        for d in range(days):
            rows.append((i, (date(2026, 9, 19) + timedelta(days=d)).isoformat(), "f" if booked[d] else "t"))
    return gzip.compress(pd.DataFrame(rows, columns=["listing_id", "date", "available"]).to_csv(index=False).encode())


def test_window_factor():
    indep = ia.window_factor(_calendar(250, 190, True), range(250), 2)
    stays = ia.window_factor(_calendar(250, 190, False), range(250), 2)
    assert 1.75 < indep["nu"] <= 2.0        # notti indipendenti: la finestra pesa quasi due notti
    assert 1.2 < stays["nu"] < 1.65         # soggiorni di più notti: molto meno
    assert ia.window_factor(_calendar(20, 190, True), range(20), 2) is None      # troppo pochi annunci
    prof = stays["profile"]
    assert prof[0][0] == 0 and prof[0][1] < -0.3                                  # vicino alla data molto è già prenotato
    assert max(v for _, v in prof) == 0.0                                         # riferita al suo massimo


def test_profile_sees_calendars_not_yet_open():
    """Lontano nel tempo la disponibilità cala perché i calendari sono chiusi: non sono prenotazioni."""
    from bnb import model

    L = np.arange(364)
    booked = 0.10 + 0.45 * np.exp(-L / 15.0)                       # prenotazioni: quasi tutte nell'ultimo mese
    opened = np.clip(1.0 - 0.0035 * np.maximum(L - 70, 0), 0.2, 1.0)   # calendari aperti: sempre meno andando lontano
    prof = ia.availability_profile((1 - booked) * opened)
    xs, ys = [p[0] for p in prof], [p[1] for p in prof]
    assert xs[-1] == 360 and ys[xs.index(300)] < -0.5 and ys[0] < -0.5      # scende da entrambe le parti
    P = model.prior_profile({"profile": prof}, 365)
    C, peak = model.pace_from_profile(P)
    assert 45 <= peak <= 90
    assert all(a >= b - 1e-12 for a, b in zip(C, C[1:]))            # il ritmo cumulato cresce solo avvicinandosi alla data
    assert C[300] < 0.05 and C[peak] < 0.12                         # i calendari chiusi non contano come prenotazioni
    true_near = -np.log((1 - booked[0]) / (1 - booked[60]))
    assert abs((C[0] - C[60]) - true_near) < 0.12                   # il calo dell'ultimo tratto invece sì
    # senza taratura c'è la forma generica, con le stesse proprietà
    G, gpeak = model.pace_from_profile(model.prior_profile({}, 365))
    assert 60 <= gpeak <= 100 and G[0] > 0.8 and G[300] < 0.02


def test_start_curve_adapts_to_a_last_minute_market():
    """La forma di partenza prende livello, ampiezza e durata da ciò che si vede, senza farsi trascinare dai pieni."""
    from bnb import model

    P = model.prior_profile({}, 365)
    truth = model.squeeze(P, 80, 0.35)
    truth[:81] *= 0.45                                              # si prenota poco e tutto sotto data
    lead = np.array(list(range(1, 62, 2)) + [72, 75, 86, 100, 120, 150, 200, 300])
    rng = np.random.default_rng(0)
    ln_obs = -0.5 + truth[lead] + rng.normal(0, 0.03, len(lead))
    ln_obs[[2, 5, 6, 9, 22]] -= 0.6                                 # fine settimana ed eventi: molto meno disponibile
    ln_typ, info = model.fit_prior(P, ln_obs, lead)
    assert info["tau"] < 1.0 and abs(info["level"] + 0.5) < 0.06 and 0.3 <= info["scale"] <= 0.6
    assert np.abs(ln_typ[:81] - (-0.5 + truth[:81])).max() < 0.1    # la curva tipica ricalca quella vera
    C, _ = model.pace_from_profile(ln_typ)
    assert abs((C[1] - C[40]) - (truth[40] - truth[1])) < 0.08      # e il calo delle ultime settimane pure
    # un mercato che segue la forma di partenza la lascia com'è
    same = -0.4 + P[lead] + rng.normal(0, 0.03, len(lead))
    info = model.fit_prior(P, same, lead)[1]
    assert info["tau"] == 1.0 and abs(info["scale"] - 1.0) < 0.15 and abs(info["level"] + 0.4) < 0.03


def test_calibration_is_fetched_whenever_it_is_missing(tmp_path, monkeypatch):
    """La taratura dai calendari non deve aspettare lo scarico settimanale dell'elenco."""
    from bnb import __main__ as cli
    from bnb.config import load_settings
    from bnb.store import Store

    cfg, store = load_settings(), Store(str(tmp_path))
    df = pd.DataFrame({"id": [1, 2], "room_type": ["Entire home/apt"] * 2, "min_nights": [1, 2]})
    calls = {"elenco": 0, "calendari": 0}
    result = {"nu": 1.3, "listings": 2, "profile": [[0, -0.5], [3, -0.4], [7, -0.3], [30, 0.0], [120, -0.1], [300, -0.6]]}

    def fake_download(log):
        calls["elenco"] += 1
        return df, "2026-09-19"

    def fake_calendar(snapshot, ids, nights, log):
        calls["calendari"] += 1
        return result

    monkeypatch.setattr(cli.insideairbnb, "download", fake_download)
    monkeypatch.setattr(cli.insideairbnb, "download_calendar_factor", fake_calendar)
    monkeypatch.setattr(cli, "log", lambda *a: None)
    day = date(2026, 11, 16)
    # stato lasciato da una versione precedente: elenco appena scaricato, taratura senza la forma per anticipo
    st = store.load_state()
    st["inside_airbnb"] = {"merged": "2026-11-15"}
    st["calibration"] = {"nu": 1.106, "snapshot": "2026-09-19"}
    store.save_state(st)
    assert cli.inside_airbnb(cfg, store, day, with_listings=True) == (None, "")
    assert calls == {"elenco": 1, "calendari": 1} and store.load_state()["calibration"]["nu"] == 1.3
    cli.inside_airbnb(cfg, store, day + timedelta(days=1), with_listings=True)
    assert calls == {"elenco": 1, "calendari": 1}                    # tutto a posto: non scarica altro
    got = cli.inside_airbnb(cfg, store, day + timedelta(days=7), with_listings=True)
    assert got[1] == "2026-09-19" and calls == {"elenco": 2, "calendari": 1}     # elenco settimanale, stessa istantanea
    # se i calendari non si leggono si riprova, ma non a ogni esecuzione
    st = store.load_state()
    del st["calibration"]["profile"]
    st.pop("calibration_tried", None)
    store.save_state(st)
    result = None
    cli.inside_airbnb(cfg, store, day + timedelta(days=8), with_listings=False)
    cli.inside_airbnb(cfg, store, day + timedelta(days=9), with_listings=False)
    assert calls["calendari"] == 2
    cli.inside_airbnb(cfg, store, day + timedelta(days=10), with_listings=False)
    assert calls["calendari"] == 3
