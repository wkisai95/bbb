from datetime import date, timedelta

from bnb import events as ev
from bnb.config import load_settings
from bnb.geo import Zones
from bnb.scan import build_windows
from bnb.store import Store


def test_zones():
    z = Zones()
    assert z.zone_of(44.4938, 11.3426) == "c_maggiore"          # Piazza Maggiore
    assert z.zone_of(44.4963, 11.3506) == "c_universita"        # Piazza Verdi
    assert z.zone_of(44.5108, 11.3622) == "fiera"               # ingresso Fiera
    assert z.zone_of(44.5117, 11.3478) == "bolognina"
    assert z.zone_of(44.4922, 11.3099) == "saragozza"           # Stadio Dall'Ara
    assert z.zone_of(44.5300, 11.2880) == "borgopanigale"       # verso l'aeroporto
    assert z.zone_of(45.0, 12.0) == "altro"
    assert z.group_of("c_maggiore") == "centro" and z.group_of("fiera") == "cintura"
    assert z.quartiere_of(44.4938, 11.3426) in ("Santo Stefano", "Porto - Saragozza")
    assert z.quartiere_of(44.5117, 11.3478) == "Navile"
    assert len({x["id"] for x in z.zones}) == len(z.zones)


def test_tiles_cover_the_city_without_overlap():
    cfg = load_settings()
    b = cfg["bbox"]
    area = sum((t["ne"][0] - t["sw"][0]) * (t["ne"][1] - t["sw"][1]) for t in cfg["tiles"])
    assert abs(area - (b["ne_lat"] - b["sw_lat"]) * (b["ne_lon"] - b["sw_lon"])) < 1e-6
    for i, a in enumerate(cfg["tiles"]):
        for c in cfg["tiles"][i + 1:]:
            overlap_lat = min(a["ne"][0], c["ne"][0]) - max(a["sw"][0], c["sw"][0])
            overlap_lon = min(a["ne"][1], c["ne"][1]) - max(a["sw"][1], c["sw"][1])
            assert not (overlap_lat > 1e-9 and overlap_lon > 1e-9), (a["id"], c["id"])


def test_windows_plan():
    cfg = load_settings()
    today = date(2026, 11, 16)
    evs = ev.all_events(today, 370)
    w1 = build_windows(cfg, today, evs)
    w2 = build_windows(cfg, today + timedelta(days=1), evs)
    near1 = [w for w in w1 if w.kind == "near"]
    near2 = [w for w in w2 if w.kind == "near"]
    assert len(near1) == 30 and near1[0].checkin == today + timedelta(days=1)
    # il giorno dopo l'allineamento è sfalsato: ogni notte viene letta con entrambe le vicine
    assert {w.checkin for w in near1}.isdisjoint({w.checkin for w in near2})
    assert sum(w.census for w in near1) == 4
    assert all(w.nights == 2 for w in w1)
    assert len({w.checkin for w in w1}) == len(w1)
    assert 45 <= len(w1) <= 90
    # nell'arco della rotazione ogni grande evento entro l'anno viene letto
    seen = set()
    for i in range(4):
        seen |= {w.checkin for w in build_windows(cfg, today + timedelta(days=i), evs)}
    for e in evs:
        if e.weight >= 4 and e.nights()[0] > today + timedelta(days=62) and e.nights()[0] < today + timedelta(days=360):
            assert any(n in seen for n in e.nights()), e.name


def test_event_windows_stay_inside_event():
    cfg = load_settings()
    today = date(2026, 11, 16)
    cersaie = ev.Event("Cersaie", date(2027, 9, 27), date(2027, 10, 1), category="fiera", weight=5)   # 5 notti
    found = set()
    for i in range(4):
        found |= {w.checkin for w in build_windows(cfg, today + timedelta(days=i), [cersaie]) if w.kind == "event"}
    nights = cersaie.nights()
    assert found and all(c in nights and c + timedelta(days=1) in nights for c in found)


def test_store_merges_same_day(tmp_path):
    s = Store(str(tmp_path))
    d = date(2026, 11, 16)
    s.add_prices(d, [{"id": 1, "checkin": "2026-12-01", "nights": 2, "price": 200, "orig": None, "src": "t"}])
    n = s.add_prices(d, [{"id": 1, "checkin": "2026-12-01", "nights": 2, "price": 180, "orig": 220, "src": "c"},
                         {"id": 2, "checkin": "2026-12-01", "nights": 2, "price": 300, "orig": None, "src": "t"}])
    assert n == 2
    df = s.load_prices()
    assert float(df[df["id"] == 1]["price"].iloc[0]) == 180.0 and df["scan"].iloc[0] == "2026-11-16"
    s.add_counts(d, [{"checkin": "", "nights": 0, "scope": "city", "count": 3500, "pages": 1, "complete": 0}])
    s.add_counts(d, [{"checkin": "2026-12-01", "nights": 2, "scope": "city", "count": 900, "pages": 1, "complete": 0}])
    c = s.load_counts()
    assert set(c["checkin"]) == {"", "2026-12-01"}
    assert s.scan_days() == [d]
