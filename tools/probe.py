"""Live probe: checks from a GitHub runner what the data sources actually return.

It writes everything into ./debug_out so the answers can be read back from the
`debug` branch. Each step is isolated: one failing source never hides the others.
"""
from __future__ import annotations

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


def save(name: str, obj, raw: bool = False) -> None:
    path = os.path.join(OUT, name)
    if raw:
        mode = "wb" if isinstance(obj, (bytes, bytearray)) else "w"
        with open(path, mode) as f:
            f.write(obj)
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)


def step(name: str):
    def deco(fn):
        t0 = time.time()
        try:
            res = fn()
            REPORT["steps"][name] = {"ok": True, "secs": round(time.time() - t0, 2), "result": res}
        except Exception as e:  # noqa: BLE001
            REPORT["steps"][name] = {
                "ok": False,
                "secs": round(time.time() - t0, 2),
                "error": repr(e)[:1500],
                "trace": traceback.format_exc()[-2500:],
            }
        print(f"[{name}] {json.dumps(REPORT['steps'][name], ensure_ascii=False)[:600]}", flush=True)
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
CENTER = dict(ne_lat=44.5005, ne_long=11.3500, sw_lat=44.4890, sw_long=11.3360)
CITY = dict(ne_lat=44.556, ne_long=11.433, sw_lat=44.421, sw_long=11.230)


@step("env")
def _env():
    import platform

    import requests

    info = {"python": platform.python_version()}
    try:
        import pyairbnb  # noqa: F401
        from importlib.metadata import version

        info["pyairbnb"] = version("pyairbnb")
        info["curl_cffi"] = version("curl_cffi")
    except Exception as e:  # noqa: BLE001
        info["pyairbnb_err"] = repr(e)
    try:
        r = requests.get("https://ipinfo.io/json", timeout=15)
        j = r.json()
        info["ip_org"] = j.get("org")
        info["ip_country"] = j.get("country")
        info["ip_region"] = j.get("region")
    except Exception as e:  # noqa: BLE001
        info["ip_err"] = repr(e)
    return info


@step("api_key")
def _api_key():
    import pyairbnb

    key = pyairbnb.get_api_key("")
    STATE["api_key"] = key
    return {"len": len(key), "key": key}


@step("search_hash")
def _hash():
    import pyairbnb

    h = pyairbnb.fetch_stays_search_hash()
    STATE["hash"] = h
    return h


def do_search(cursor="", check_in=CHECKIN, check_out=CHECKOUT, box=CENTER, zoom=15, use_hash=True, **flt):
    import pyairbnb.search as s

    args = dict(
        api_key=STATE["api_key"], cursor=cursor, check_in=check_in, check_out=check_out,
        ne_lat=box["ne_lat"], ne_long=box["ne_long"], sw_lat=box["sw_lat"], sw_long=box["sw_long"],
        zoom_value=zoom, currency="EUR", place_type=flt.get("place_type", ""),
        price_min=flt.get("price_min", 0), price_max=flt.get("price_max", 0), amenities=[],
        free_cancellation=False, adults=flt.get("adults", 0), children=0, infants=0,
        min_bedrooms=flt.get("min_bedrooms", 0), min_beds=0, min_bathrooms=flt.get("min_bathrooms", 0),
        language="en", proxy_url="", hash=STATE.get("hash", "") if use_hash else "",
    )
    return s.get(**args)


def summarize(raw):
    res = dig(raw, "data.presentation.staysSearch.results", {}) or {}
    mp = dig(raw, "data.presentation.staysSearch.mapResults", {}) or {}
    sr = res.get("searchResults") or []
    msr = mp.get("mapSearchResults") or []
    siv = mp.get("staysInViewport") or []
    pag = res.get("paginationInfo") or {}
    types = {}
    for x in sr:
        types[x.get("__typename", "?")] = types.get(x.get("__typename", "?"), 0) + 1
    mtypes = {}
    for x in msr:
        mtypes[x.get("__typename", "?")] = mtypes.get(x.get("__typename", "?"), 0) + 1
    return {
        "searchResults": len(sr), "types": types,
        "mapSearchResults": len(msr), "mapTypes": mtypes,
        "staysInViewport": len(siv),
        "siv_sample": siv[:2],
        "pageCursors": len(pag.get("pageCursors") or []),
        "hasNext": bool(pag.get("nextPageCursor")),
        "top_keys": sorted((dig(raw, "data.presentation.staysSearch", {}) or {}).keys()),
        "results_keys": sorted(res.keys()),
        "map_keys": sorted(mp.keys()),
        "errors": raw.get("errors"),
        "bytes": len(json.dumps(raw)),
    }


def ids_of(raw):
    import pyairbnb.standardize as st

    out = []
    for x in dig(raw, "data.presentation.staysSearch.results.searchResults", []) or []:
        i = st.decode_listing_id(dig(x, "demandStayListing.id", "") or "")
        if i:
            out.append(i)
    return out


@step("search_center_p1")
def _s1():
    raw = do_search()
    STATE["s1"] = raw
    save("search_center_p1.json", raw)
    STATE["ids"] = ids_of(raw)
    return summarize(raw)


@step("search_center_std")
def _std():
    import pyairbnb.standardize as st

    std = st.from_search(STATE["s1"])
    for x in std:
        x["images"] = x.get("images", [])[:1]
    save("search_center_p1_std.json", std)
    mp = []
    try:
        mp = st.from_map_search(STATE["s1"])
        for x in mp:
            x["images"] = x.get("images", [])[:1]
        save("search_center_p1_mapstd.json", mp)
    except Exception as e:  # noqa: BLE001
        return {"std": len(std), "map_err": repr(e)[:300]}
    return {"std": len(std), "map": len(mp)}


@step("search_center_pages")
def _pages():
    raw = STATE["s1"]
    seen = set(STATE["ids"])
    out = []
    cursors = dig(raw, "data.presentation.staysSearch.results.paginationInfo.pageCursors", []) or []
    cur = dig(raw, "data.presentation.staysSearch.results.paginationInfo.nextPageCursor")
    n = 1
    while cur and n < 6:
        time.sleep(1.0)
        r = do_search(cursor=cur)
        ids = ids_of(r)
        new = [i for i in ids if i not in seen]
        seen.update(ids)
        n += 1
        out.append({"page": n, "n": len(ids), "new": len(new), "sum": {k: v for k, v in summarize(r).items() if k in ("mapSearchResults", "staysInViewport", "hasNext")}})
        if n == 2:
            save("search_center_p2.json", r)
        cur = dig(r, "data.presentation.staysSearch.results.paginationInfo.nextPageCursor")
    STATE["ids_all"] = sorted(seen)
    return {"pageCursors": len(cursors), "pages": out, "distinct_ids": len(seen)}


@step("search_city_wide")
def _city():
    raw = do_search(box=CITY, zoom=12)
    save("search_city_p1.json", raw)
    return summarize(raw)


@step("search_no_dates")
def _nodates():
    raw = do_search(check_in="", check_out="")
    save("search_center_nodates.json", raw)
    return summarize(raw)


@step("search_no_hash")
def _nohash():
    raw = do_search(use_hash=False)
    return summarize(raw)


@step("search_filters")
def _filters():
    out = {}
    for name, flt in [("min_bedrooms=2", dict(min_bedrooms=2)), ("adults=6", dict(adults=6)),
                      ("min_bathrooms=2", dict(min_bathrooms=2)), ("entire", dict(place_type="Entire home/apt"))]:
        time.sleep(1.0)
        r = do_search(**flt)
        out[name] = {"n": len(ids_of(r)), "sum": {k: v for k, v in summarize(r).items() if k in ("searchResults", "mapSearchResults", "staysInViewport", "hasNext", "pageCursors")}}
        if name == "adults=6":
            save("search_center_adults6.json", r)
    return out


@step("search_one_night_and_week")
def _nights():
    out = {}
    for name, (a, b) in {"1n": (20, 21), "7n": (20, 27), "far_300d": (300, 302)}.items():
        time.sleep(1.0)
        r = do_search(check_in=(TODAY + timedelta(days=a)).isoformat(), check_out=(TODAY + timedelta(days=b)).isoformat())
        prices = []
        for x in dig(r, "data.presentation.staysSearch.results.searchResults", []) or []:
            pl = dig(x, "structuredDisplayPrice.primaryLine", {}) or {}
            prices.append({k: pl.get(k) for k in ("__typename", "price", "originalPrice", "discountedPrice", "qualifier", "accessibilityLabel")})
        out[name] = {"n": len(prices), "sample": prices[:6]}
    return out


@step("calendar")
def _cal():
    import pyairbnb.calendarinfo as c

    ids = STATE.get("ids") or []
    out = []
    for k, rid in enumerate(ids[:3]):
        time.sleep(0.8)
        months = c.get(STATE["api_key"], str(rid), TODAY.month, TODAY.year, "")
        days = [d for m in months for d in (m.get("days") or [])]
        if k == 0:
            save("calendar_sample.json", months)
        out.append({
            "room": rid, "months": len(months), "days": len(days),
            "available": sum(1 for d in days if d.get("available")),
            "day_keys": sorted(days[0].keys()) if days else [],
            "first": days[0] if days else None,
            "price_nonnull": sum(1 for d in days if dig(d, "price.localPriceFormatted")),
            "minNights": sorted({d.get("minNights") for d in days if d.get("minNights") is not None})[:8],
        })
    return out


@step("details")
def _details():
    import pyairbnb.details as d

    ids = STATE.get("ids") or []
    out = []
    for k, rid in enumerate(ids[:2]):
        time.sleep(1.0)
        t0 = time.time()
        data, price_input, _cookies = d.get(f"https://www.airbnb.com/rooms/{rid}", "en", "")
        data["images"] = data.get("images", [])[:1]
        data["amenities"] = data.get("amenities", [])[:1]
        if k == 0:
            save("details_sample.json", data)
        out.append({
            "room": rid, "secs": round(time.time() - t0, 2),
            "room_type": data.get("room_type"), "person_capacity": data.get("person_capacity"),
            "sub_description": data.get("sub_description"), "coordinates": data.get("coordinates"),
            "title": data.get("title"), "rating": data.get("rating"),
        })
    return out


@step("rate_search")
def _rate_search():
    """40 search pages in a row: do we get throttled from a runner?"""
    ok, fail, times = 0, [], []
    for i in range(40):
        dlat = 0.004 * (i % 5)
        dlon = 0.006 * (i // 5)
        box = dict(ne_lat=44.5005 + dlat, ne_long=11.3200 + dlon + 0.012, sw_lat=44.4890 + dlat, sw_long=11.3200 + dlon)
        a = 10 + (i % 20) * 3
        t0 = time.time()
        try:
            r = do_search(box=box, check_in=(TODAY + timedelta(days=a)).isoformat(), check_out=(TODAY + timedelta(days=a + 2)).isoformat())
            n = len(ids_of(r))
            ok += 1
            times.append((round(time.time() - t0, 2), n))
        except Exception as e:  # noqa: BLE001
            fail.append({"i": i, "err": repr(e)[:400]})
            if len(fail) >= 3:
                break
        time.sleep(0.6)
    return {"ok": ok, "fail": fail, "timings": times}


@step("rate_calendar")
def _rate_cal():
    import pyairbnb.calendarinfo as c

    ids = (STATE.get("ids_all") or STATE.get("ids") or [])[:30]
    ok, fail, secs = 0, [], []
    for rid in ids:
        t0 = time.time()
        try:
            months = c.get(STATE["api_key"], str(rid), TODAY.month, TODAY.year, "")
            if months:
                ok += 1
            secs.append(round(time.time() - t0, 2))
        except Exception as e:  # noqa: BLE001
            fail.append({"room": rid, "err": repr(e)[:300]})
            if len(fail) >= 3:
                break
        time.sleep(0.4)
    return {"n": len(ids), "ok": ok, "fail": fail, "secs": secs}


@step("inside_airbnb")
def _inside():
    import csv

    import requests

    ua = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
    page = requests.get("https://insideairbnb.com/get-the-data/", headers=ua, timeout=60)
    urls = sorted(set(re.findall(r"https://data\.insideairbnb\.com/italy/emilia-romagna/bologna/[0-9-]+/[a-z]+/[A-Za-z_.]+", page.text)))
    out = {"page_status": page.status_code, "page_len": len(page.text), "urls": urls}
    lst = next((u for u in urls if u.endswith("data/listings.csv.gz")), None)
    if lst:
        r = requests.get(lst, headers=ua, timeout=120)
        out["listings_status"] = r.status_code
        out["listings_bytes"] = len(r.content)
        rows = list(csv.DictReader(io.StringIO(gzip.decompress(r.content).decode("utf-8", "replace"))))
        out["listings_rows"] = len(rows)
        out["columns"] = list(rows[0].keys()) if rows else []
        keep = ["id", "name", "neighbourhood_cleansed", "latitude", "longitude", "property_type", "room_type", "accommodates",
                "bathrooms", "bathrooms_text", "bedrooms", "beds", "price", "minimum_nights", "availability_30", "availability_365",
                "number_of_reviews", "review_scores_rating", "last_scraped", "has_availability", "estimated_occupancy_l365d"]
        out["sample"] = [{k: row.get(k) for k in keep} for row in rows[:4]]
        out["price_nonempty"] = sum(1 for row in rows if (row.get("price") or "").strip())
    geo = next((u for u in urls if u.endswith("neighbourhoods.geojson")), None)
    if geo:
        r = requests.get(geo, headers=ua, timeout=60)
        out["geo_status"] = r.status_code
        if r.status_code == 200:
            save("neighbourhoods.geojson", r.content, raw=True)
            g = r.json()
            out["geo_features"] = [f.get("properties") for f in g.get("features", [])]
    cal = next((u for u in urls if u.endswith("data/calendar.csv.gz")), None)
    if cal:
        r = requests.get(cal, headers=ua, timeout=180)
        out["calendar_status"] = r.status_code
        out["calendar_bytes"] = len(r.content)
        if r.status_code == 200:
            txt = gzip.decompress(r.content).decode("utf-8", "replace")
            lines = txt.split("\n")
            out["calendar_rows"] = len(lines)
            out["calendar_head"] = lines[:4]
    return out


@step("event_pages")
def _events():
    import requests

    ua = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
          "Accept-Language": "it-IT,it;q=0.9"}
    out = {}
    for name, url in {
        "bolognafiere": "https://bolognafiere.it/calendario/",
        "bolognawelcome_concerti": "https://www.bolognawelcome.com/it/blog/concerti-da-vedere-a-bologna",
        "bolognawelcome_fiere": "https://www.bolognawelcome.com/it/eventi/fiere",
        "unipolarena": "https://www.unipolarena.it/eventi/",
    }.items():
        try:
            r = requests.get(url, headers=ua, timeout=60)
            out[name] = {"status": r.status_code, "len": len(r.text), "ld_json": len(re.findall(r"application/ld\+json", r.text))}
            save(f"page_{name}.html", r.text[:900_000], raw=True)
        except Exception as e:  # noqa: BLE001
            out[name] = {"error": repr(e)[:300]}
    return out


REPORT["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
save("report.json", REPORT)
print("done", flush=True)
sys.exit(0)
