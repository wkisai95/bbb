"""Diagnostica: quanti alloggi interi risultano disponibili in ogni riquadro della città.

Serve solo a disegnare la griglia di scansione. Usa la ricerca pubblica di Airbnb
tramite la libreria pyairbnb, una richiesta alla volta e con una pausa tra una e l'altra.
I risultati finiscono in ./debug_out (ramo `debug`).
"""
from __future__ import annotations

import collections
import csv
import gzip
import io
import json
import os
import re
import sys
import time
import traceback
from datetime import date, timedelta

OUT = "debug_out"
os.makedirs(OUT, exist_ok=True)
REPORT: dict = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "steps": {}}
PAUSE = 1.3


def save(name: str, obj) -> None:
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def step(name: str):
    def deco(fn):
        t0 = time.time()
        try:
            res = fn()
            REPORT["steps"][name] = {"ok": True, "secs": round(time.time() - t0, 2), "result": res}
        except Exception as e:  # noqa: BLE001
            REPORT["steps"][name] = {"ok": False, "secs": round(time.time() - t0, 2), "error": repr(e)[:800],
                                     "trace": traceback.format_exc()[-1500:]}
        print(f"[{name}] {json.dumps(REPORT['steps'][name], ensure_ascii=False)[:500]}", flush=True)
        save("report.json", REPORT)
        return fn

    return deco


def dig(d, path, default=None):
    cur = d
    for k in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return default
        if cur is None:
            return default
    return cur


STATE: dict = {}
TODAY = date.today()
CHECKIN = (TODAY + timedelta(days=20)).isoformat()
CHECKOUT = (TODAY + timedelta(days=22)).isoformat()
CITY = dict(ne_lat=44.556, ne_long=11.433, sw_lat=44.421, sw_long=11.230)


@step("setup")
def _setup():
    import pyairbnb

    STATE["api_key"] = pyairbnb.get_api_key("")
    STATE["hash"] = pyairbnb.fetch_stays_search_hash()
    return "ok"


def search(box, check_in=CHECKIN, check_out=CHECKOUT, **flt):
    import pyairbnb.search as s

    time.sleep(PAUSE)
    return s.get(
        api_key=STATE["api_key"], cursor="", check_in=check_in, check_out=check_out,
        ne_lat=box["ne_lat"], ne_long=box["ne_long"], sw_lat=box["sw_lat"], sw_long=box["sw_long"],
        zoom_value=14, currency="EUR", place_type="Entire home/apt", price_min=0, price_max=0, amenities=[],
        free_cancellation=False, adults=flt.get("adults", 0), children=0, infants=0,
        min_bedrooms=flt.get("min_bedrooms", 0), min_beds=0, min_bathrooms=flt.get("min_bathrooms", 0),
        language="en", proxy_url="", hash=STATE["hash"],
    )


def count_of(raw):
    res = dig(raw, "data.presentation.staysSearch.results", {}) or {}
    return {
        "count": dig(res, "filters.filterPanel.resultCount"),
        "n": len(res.get("searchResults") or []),
        "pages": len(dig(res, "paginationInfo.pageCursors", []) or []),
    }


@step("grid_counts")
def _grid():
    rows = []
    nlat, nlon = 6, 6
    dlat = (CITY["ne_lat"] - CITY["sw_lat"]) / nlat
    dlon = (CITY["ne_long"] - CITY["sw_long"]) / nlon
    for i in range(nlat):
        for j in range(nlon):
            box = dict(sw_lat=round(CITY["sw_lat"] + i * dlat, 5), sw_long=round(CITY["sw_long"] + j * dlon, 5),
                       ne_lat=round(CITY["sw_lat"] + (i + 1) * dlat, 5), ne_long=round(CITY["sw_long"] + (j + 1) * dlon, 5))
            rec = {"i": i, "j": j, "box": box}
            for tag, (ci, co) in {"dated": (CHECKIN, CHECKOUT), "undated": ("", "")}.items():
                try:
                    rec[tag] = count_of(search(box, ci, co))
                except Exception as e:  # noqa: BLE001
                    rec[tag] = {"err": repr(e)[:160]}
            rows.append(rec)
    return rows


@step("city_counts_by_filter")
def _cityf():
    out = {}
    for name, kw in {
        "entire_dated": {}, "entire_undated": dict(check_in="", check_out=""),
        "br2": dict(min_bedrooms=2), "br3": dict(min_bedrooms=3),
        "adults4": dict(adults=4), "adults6": dict(adults=6), "bath2": dict(min_bathrooms=2),
    }.items():
        out[name] = count_of(search(CITY, **kw))
    return out


@step("inside_airbnb")
def _inside():
    import requests

    ua = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
    page = requests.get("https://insideairbnb.com/get-the-data/", headers=ua, timeout=60)
    urls = sorted(set(re.findall(r"https://data\.insideairbnb\.com/italy/emilia-romagna/bologna/[0-9-]+/[a-z]+/[A-Za-z_.]+", page.text)))
    lst = next((u for u in urls if u.endswith("data/listings.csv.gz")), None)
    r = requests.get(lst, headers=ua, timeout=120)
    rows = list(csv.DictReader(io.StringIO(gzip.decompress(r.content).decode("utf-8", "replace"))))
    keep = ["id", "room_type", "accommodates", "bathrooms_text", "bedrooms", "beds", "price", "price_quote_checkin_date",
            "price_quote_checkout_date", "price_quote_total_price", "price_quote_price_per_night",
            "minimum_nights", "has_availability", "availability_30", "availability_365", "estimated_occupancy_l365d"]
    return {
        "rows": len(rows),
        "room_type": collections.Counter(x.get("room_type") for x in rows).most_common(),
        "last_scraped": collections.Counter(x.get("last_scraped") for x in rows).most_common(4),
        "bedrooms": collections.Counter(x.get("bedrooms") for x in rows).most_common(10),
        "accommodates": collections.Counter(x.get("accommodates") for x in rows).most_common(14),
        "bathrooms_text": collections.Counter(x.get("bathrooms_text") for x in rows).most_common(14),
        "quote_nonempty": sum(1 for x in rows if (x.get("price_quote_price_per_night") or "").strip()),
        "sample": [{k: (x.get(k) or "")[:80] for k in keep} for x in rows[100:106]],
    }


REPORT["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
save("report.json", REPORT)
print("done", flush=True)
sys.exit(0)
