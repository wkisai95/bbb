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
    pace = stays["pace"]
    assert pace[0][0] == 0 and pace[0][1] > 0.3                                   # vicino alla data molto è già prenotato
    assert all(a[1] >= b[1] for a, b in zip(pace, pace[1:]))                      # e sempre meno andando lontano
