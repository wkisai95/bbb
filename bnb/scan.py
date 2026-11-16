"""Scansione giornaliera: prezzi per finestra di date, conteggi di disponibilità, catalogo."""
from __future__ import annotations

import time
import zlib
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from . import events as ev
from . import insideairbnb
from .airbnb import Blocked, BudgetExhausted, Card
from .geo import Zones
from .store import LISTING_COLS, Store

PAGE = 40          # annunci per pagina restituiti dalla ricerca
MAX_PAGES = 7      # pagine consultabili per riquadro
SPLIT_DEPTH = 2    # quante volte un riquadro può essere diviso in quattro durante una lettura completa


@dataclass
class Window:
    checkin: date
    nights: int
    kind: str            # near | mid | far | event
    census: bool = False     # lettura completa: tutti gli alloggi liberi, per vedere chi viene prenotato

    @property
    def checkout(self) -> date:
        return self.checkin + timedelta(days=self.nights)

    def key(self) -> str:
        return self.checkin.isoformat()


# ------------------------------------------------------------------ piano delle date

def build_windows(cfg: dict, today: date, events: list) -> list[Window]:
    sc = cfg["scan"]
    n = int(sc["nights"])
    near, mid, far = int(sc["near_days"]), int(sc["mid_days"]), int(sc["far_days"])
    day = today.toordinal()
    out: dict[date, Window] = {}

    def put(w: Window):
        if w.checkin not in out:
            out[w.checkin] = w

    # vicine: una finestra ogni 2 giorni; il giorno dopo l'allineamento si sposta di uno,
    # così ogni notte viene letta in coppia sia con la precedente sia con la successiva
    # entro track_days la lettura è completa: due giorni dopo la stessa finestra viene riletta, e chi non
    # c'è più è stato prenotato
    track = int(sc.get("track_days", 30))
    k = 0
    while 1 + n * k <= near:
        ci = today + timedelta(days=1 + n * k)
        put(Window(ci, n, "near", (ci - today).days <= track))
        k += 1

    # eventi oltre l'orizzonte vicino: le loro notti, a rotazione
    for e in events:
        if e.weight < 2:
            continue
        ns = e.nights()
        lead = (ns[0] - today).days
        if lead <= near or lead > far:
            continue
        share = int(sc["mid_share"]) if lead <= mid else int(sc["far_share"])
        if (zlib.crc32(e.id.encode()) + day) % max(1, share) != 0:
            continue
        starts = list(range(0, max(1, len(ns) - n + 1), n))
        if len(ns) > n and len(ns) % n:
            starts.append(len(ns) - n)          # l'ultima finestra arretra, così resta dentro l'evento
        for j in starts[:3]:
            put(Window(ns[j], n, "event"))

    # medie: ogni settimana un fine settimana (ven-dom) e un infrasettimanale (mar-gio)
    d = today + timedelta(days=near + 1)
    while (d - today).days <= mid:
        if d.weekday() in (1, 4) and (d.toordinal() // 7 + day) % max(1, int(sc["mid_share"])) == 0:
            put(Window(d, n, "mid"))
        d += timedelta(days=1)

    # lontane: secondo venerdì e secondo martedì di ogni mese
    d = today + timedelta(days=mid + 1)
    while (d - today).days <= far:
        if d.weekday() in (1, 4) and 8 <= d.day <= 14 and (d.month + day) % max(1, int(sc["far_share"])) == 0:
            put(Window(d, n, "far"))
        d += timedelta(days=1)

    order = {"near": 0, "event": 1, "mid": 2, "far": 3}
    return sorted(out.values(), key=lambda w: (order[w.kind], w.checkin))


# ------------------------------------------------------------------ catalogo in memoria

class Catalog:
    def __init__(self, store: Store, zones: Zones):
        self.store = store
        self.zones = zones
        df = store.load_listings()
        self.rows: dict[int, dict] = {int(r["id"]): r for r in df.to_dict("records")}

    @staticmethod
    def _blank(lid: int) -> dict:
        r = {c: None for c in LISTING_COLS}
        r.update(id=int(lid), name="", kind="", zone="", district="", guests_src="", first_seen="", last_seen="",
                 reviews=0, superhost=0, fav=0, n_seen=0)
        return r

    def _place(self, r: dict):
        if r.get("lat") is not None and r.get("lon") is not None and r["lat"] == r["lat"]:
            r["zone"] = self.zones.zone_of(float(r["lat"]), float(r["lon"]))
            if not r.get("district"):
                r["district"] = self.zones.quartiere_of(float(r["lat"]), float(r["lon"]))

    def upsert(self, c: Card, today: date):
        r = self.rows.get(c.id)
        if r is None:
            r = self._blank(c.id)
            r["first_seen"] = today.isoformat()
            self.rows[c.id] = r
        if c.name:
            r["name"] = c.name.replace("\n", " ").replace("\r", " ")
        if c.kind:
            r["kind"] = c.kind
        if c.lat is not None and c.lon is not None:
            moved = r.get("lat") is None or r["lat"] != r["lat"] or abs(float(r["lat"]) - c.lat) > 2e-4 or abs(float(r["lon"]) - c.lon) > 2e-4
            if moved or not r.get("zone"):
                r["lat"], r["lon"] = round(c.lat, 5), round(c.lon, 5)
                self._place(r)
        for k in ("bedrooms", "beds", "baths", "rating"):
            v = getattr(c, k)
            if v is not None:
                r[k] = v
        if c.reviews:
            r["reviews"] = int(c.reviews)
        r["superhost"] = int(bool(c.superhost))
        r["fav"] = int(c.fav)
        if not r.get("first_seen"):
            r["first_seen"] = today.isoformat()
        if r.get("last_seen") != today.isoformat():
            r["last_seen"] = today.isoformat()
            r["n_seen"] = int(r.get("n_seen") or 0) + 1

    def merge_inside_airbnb(self, ia: pd.DataFrame, room_type: str) -> dict:
        """Completa il catalogo con capienza, soggiorno minimo e quartiere."""
        ia = ia[ia["room_type"] == room_type] if room_type else ia
        added = filled = 0
        for x in ia.to_dict("records"):
            lid = int(x["id"])
            r = self.rows.get(lid)
            if r is None:
                r = self._blank(lid)
                r["name"] = str(x.get("name") or "")[:120].replace("\n", " ")
                r["kind"] = str(x.get("kind") or "").replace("Entire ", "").strip().capitalize()
                r["lat"], r["lon"] = x.get("lat"), x.get("lon")
                self._place(r)
                self.rows[lid] = r
                added += 1
            for k in ("bedrooms", "beds", "baths", "rating"):
                if (r.get(k) is None or r[k] != r[k]) and x.get(k) == x.get(k) and x.get(k) is not None:
                    r[k] = float(x[k])
            if (r.get("guests") is None or r["guests"] != r["guests"] or r.get("guests_src") == "est") and x.get("guests") == x.get("guests"):
                r["guests"] = float(x["guests"])
                r["guests_src"] = "ia"
                filled += 1
            if x.get("min_nights") == x.get("min_nights"):
                r["min_nights"] = float(x["min_nights"])
            if x.get("district"):
                r["district"] = str(x["district"])
            if not r.get("reviews") and x.get("reviews") == x.get("reviews"):
                r["reviews"] = int(x["reviews"])
        return {"rows": int(len(ia)), "added": added, "guests_filled": filled}

    def missing_guests(self, today: date, limit: int) -> list[int]:
        cut = (today - timedelta(days=30)).isoformat()
        cand = [r for r in self.rows.values()
                if (r.get("guests") is None or r["guests"] != r["guests"] or r.get("guests_src") == "est")
                and (r.get("last_seen") or "") >= cut]
        cand.sort(key=lambda r: -int(r.get("n_seen") or 0))
        return [int(r["id"]) for r in cand[:limit]]

    def save(self):
        df = pd.DataFrame(list(self.rows.values()), columns=LISTING_COLS)
        self.store.save_listings(df)


# ------------------------------------------------------------------ scansione

def _box(tile: dict) -> dict:
    return {"sw": list(tile["sw"]), "ne": list(tile["ne"])}


def _quarters(box: dict) -> list[dict]:
    (s, w), (n, e) = box["sw"], box["ne"]
    ms, me = (s + n) / 2, (w + e) / 2
    return [{"sw": [s, w], "ne": [ms, me]}, {"sw": [s, me], "ne": [ms, e]},
            {"sw": [ms, w], "ne": [n, me]}, {"sw": [ms, me], "ne": [n, e]}]


def fetch_event_pages(log=print) -> dict:
    import requests

    ua = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
          "Accept-Language": "it-IT,it;q=0.9"}
    out = {}
    for name, url in {"bolognafiere": "https://bolognafiere.it/calendario/",
                      "bolognawelcome": "https://www.bolognawelcome.com/it/blog/concerti-da-vedere-a-bologna"}.items():
        try:
            r = requests.get(url, headers=ua, timeout=60)
            if r.status_code == 200:
                out[name] = r.text
            else:
                log(f"calendario {name}: risposta {r.status_code}")
        except Exception as e:  # noqa: BLE001
            log(f"calendario {name} non raggiungibile: {e!r}"[:200])
    return out


def refresh_auto_events(store: Store, today: date, pages: dict | None, log=print) -> list:
    """Rilegge i calendari pubblici; se non rispondono resta valido l'ultimo elenco salvato."""
    _, rules = ev.load_seed()
    cached = store.read_json("data/events_auto.json", {}) or {}
    if pages:
        found = ev.auto_events(pages, today, rules)
        got = {k: bool(pages.get(k)) for k in ("bolognafiere", "bolognawelcome")}
        # una fonte che non ha risposto non cancella i suoi eventi già noti
        keep = [x for x in cached.get("events", [])
                if not got.get("bolognawelcome" if x.get("category") == "concerto" else "bolognafiere")]
        items = [e.to_json() for e in found] + keep
        store.write_json("data/events_auto.json", {"updated": today.isoformat(), "sources": got, "events": items})
        log(f"eventi dai calendari pubblici: {len(found)} letti, {len(keep)} conservati")
    else:
        items = cached.get("events", [])
    out = []
    for x in items:
        e = ev._from_dict(x, "auto")  # noqa: SLF001
        if e:
            out.append(e)
    return out


def run_scan(cfg: dict, store: Store, client, today: date, log=print, pages: dict | None = None,
             inside: pd.DataFrame | None = None, inside_date: str = "") -> dict:
    """Esegue la scansione e salva ciò che ha raccolto, anche se si interrompe a metà."""
    t0 = time.time()
    zones = Zones()
    cat = Catalog(store, zones)
    state = store.load_state()
    sc = cfg["scan"]
    n_nights = int(sc["nights"])
    city = {"sw": [cfg["bbox"]["sw_lat"], cfg["bbox"]["sw_lon"]], "ne": [cfg["bbox"]["ne_lat"], cfg["bbox"]["ne_lon"]]}
    tiles = cfg["tiles"]
    core = [t for t in tiles if t.get("kind", "core") == "core"]
    outer = [t for t in tiles if t.get("kind") == "outer"]
    day = today.toordinal()

    run = {"date": today.isoformat(), "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "stopped": "",
           "windows": 0, "census": 0, "prices": 0, "details": 0, "phases": []}

    # --- fonti di contorno
    if inside is not None and len(inside):
        res = cat.merge_inside_airbnb(inside, cfg.get("room_type", ""))
        state["inside_airbnb"] = {"snapshot": inside_date, "merged": today.isoformat(), **res}
        log(f"Inside Airbnb unito al catalogo: {res}")
    auto = refresh_auto_events(store, today, pages, log)
    events = ev.all_events(today, int(sc["far_days"]) + 5, auto)
    windows = build_windows(cfg, today, events)
    near = [w for w in windows if w.kind == "near"]
    light = [w for w in windows if w.kind != "near"]

    price_rows: list[dict] = []
    count_rows: list[dict] = []
    seen_rows: list[dict] = []
    first: dict[tuple, dict] = {}        # (checkin, riquadro) -> prima pagina: {"box", "cursors", "count", "ids"}
    seen: dict[str, set] = {}            # finestra -> alloggi visti liberi, con o senza prezzo
    priced: dict[str, set] = {}
    track = {"windows": 0, "complete": 0, "requests": 0, "splits": 0, "resplits": 0, "found": 0, "expected": 0}
    track_left = [int(sc.get("track_requests", 950))]

    def take(page, w: Window | None, src: str) -> set:
        """Mette da parte ciò che c'è nella pagina; restituisce gli alloggi liberi per quella finestra."""
        ids = set()
        for c in page.cards:
            cat.upsert(c, today)
            if w is None or (c.nights is not None and c.nights != w.nights):
                continue
            ids.add(c.id)
            if c.price:
                price_rows.append({"id": c.id, "checkin": w.key(), "nights": w.nights, "price": round(c.price, 2),
                                   "orig": round(c.orig, 2) if c.orig else None, "src": src})
                priced.setdefault(w.key(), set()).add(c.id)
        if w is not None:
            seen.setdefault(w.key(), set()).update(ids)
        return ids

    def count_row(w: Window | None, scope: str, page, pages: int = 1, complete: int = 0):
        if page is not None and page.count is not None:
            count_rows.append({"checkin": w.key() if w else "", "nights": w.nights if w else 0, "scope": scope,
                               "count": int(page.count), "pages": pages, "complete": complete})

    def supply():
        p = client.search(city)
        if p:
            count_row(None, "city", p)
            take(p, None, "")
        for seg in cfg.get("segments", []):
            p = client.search(city, min_bedrooms=int(seg.get("min_bedrooms", 0)), min_bathrooms=int(seg.get("min_bathrooms", 0)))
            if p:
                count_row(None, f"seg:{seg['id']}", p)
                take(p, None, "")
        if day % 7 == 0 or not state.get("tile_supply_day"):
            for t in tiles:
                p = client.search(_box(t))
                if p:
                    count_row(None, f"tile:{t['id']}", p)
                    take(p, None, "")
            state["tile_supply_day"] = today.isoformat()

    def scan_window(w: Window, idx: int, mode: str):
        """Conteggi e prima pagina. mode: track = tutti i riquadri · near = metà di quelli centrali · light = un terzo."""
        ci, co = w.key(), w.checkout.isoformat()
        p = client.search(city, ci, co)
        if p:
            count_row(w, "city", p)
            take(p, w, "g")
        for seg in cfg.get("segments", []):
            p = client.search(city, ci, co, min_bedrooms=int(seg.get("min_bedrooms", 0)), min_bathrooms=int(seg.get("min_bathrooms", 0)))
            if p:
                count_row(w, f"seg:{seg['id']}", p)
                take(p, w, "g")
        if mode == "track":
            todo = list(tiles)
        elif mode == "near":
            todo = ([t for i, t in enumerate(core) if (idx + day + i) % 2 == 0]
                    + [t for i, t in enumerate(outer) if (idx + day + i) % 3 == 0])
        else:
            todo = [t for i, t in enumerate(core) if (idx + day + i) % 3 == 0]
        for t in todo:
            p = client.search(_box(t), ci, co)
            if p:
                count_row(w, f"tile:{t['id']}", p)
                ids = take(p, w, "t")
                first[(ci, t["id"])] = {"box": _box(t), "cursors": p.cursors, "count": p.count or 0, "ids": ids}
        run["windows"] += 1

    def complete_read(w: Window):
        """Elenco completo di chi è ancora libero in quelle date: tutte le pagine di tutti i riquadri.

        Le pagine profonde della ricerca sono le meno affidabili: qualche annuncio si ripete, qualcuno salta.
        Un riquadro con troppi risultati per essere sfogliato, o in cui si è visto molto meno di quanto la
        ricerca dichiara, viene diviso in quattro e riletto, al massimo due volte. Qualche alloggio sfugge
        comunque: per questo chi "sparisce" viene contato come prenotato solo se manca anche alla lettura dopo.
        """
        ci, co = w.key(), w.checkout.isoformat()
        r0 = client.requests
        left = [int(sc.get("track_max_pages", 90))]
        cut = [False]
        resplit = float(sc.get("track_resplit_share", 0.06))
        queue = [dict(first[(ci, t["id"])], depth=0) for t in tiles if (ci, t["id"]) in first]
        if len(queue) < len(tiles):
            cut[0] = True                                   # manca la prima pagina di un riquadro: lettura non completa

        def ask(box: dict, cursor: str = ""):
            if left[0] <= 0 or track_left[0] <= 0:
                cut[0] = True
                return None
            left[0] -= 1
            track_left[0] -= 1
            p = client.search(box, ci, co, cursor=cursor) if cursor else client.search(box, ci, co)
            if p is None:
                cut[0] = True
            return p

        def split(box: dict, depth: int):
            for q in _quarters(box):
                p = ask(q)
                if p:
                    queue.append({"box": q, "cursors": p.cursors, "count": p.count or 0, "ids": take(p, w, "c"), "depth": depth + 1})

        while queue:
            item = queue.pop(0)
            count, ids, depth = item["count"], set(item["ids"]), item["depth"]
            if count > PAGE * MAX_PAGES and depth < SPLIT_DEPTH:
                track["splits"] += 1
                split(item["box"], depth)
                continue
            for cur in item["cursors"][1:]:
                p = ask(item["box"], cur)
                if p:
                    ids |= take(p, w, "c")
            if count > PAGE and count - len(ids) > max(4, resplit * count) and depth < SPLIT_DEPTH and not cut[0]:
                track["resplits"] += 1
                split(item["box"], depth)
        expected = next((r["count"] for r in count_rows if r["checkin"] == ci and r["scope"] == "city"), 0)
        got = len(seen.get(ci, ()))
        ok = int(bool(expected) and not cut[0] and got >= float(sc.get("track_min_share", 0.95)) * expected)
        count_rows.append({"checkin": ci, "nights": w.nights, "scope": "census", "count": got,
                           "pages": client.requests - r0, "complete": ok})
        if ok:
            seen_rows.extend({"checkin": ci, "id": i} for i in sorted(seen[ci] - priced.get(ci, set())))
        track["windows"] += 1
        track["complete"] += ok
        track["requests"] += client.requests - r0
        track["found"] += got
        track["expected"] += int(expected)
        run["census"] += ok

    def left() -> int:
        return client.remaining() if hasattr(client, "remaining") else 10 ** 9

    def phase(name: str, fn):
        t = time.time()
        r0 = client.requests
        try:
            fn()
        finally:
            run["phases"].append({"name": name, "requests": client.requests - r0, "secs": round(time.time() - t, 1)})

    try:
        client.start()
        phase("offerta", supply)
        def followed():
            for i, w in enumerate(w for w in near if w.census):
                scan_window(w, i, "track")
                complete_read(w)                    # subito dopo la prima pagina: l'ordine dei risultati cambia nel tempo

        def far():
            for i, w in enumerate(light):
                if left() < 15:                     # poche richieste rimaste: le date lontane possono aspettare domani
                    break
                scan_window(w, i, "light")

        phase("date seguite", followed)
        phase("date vicine", lambda: [scan_window(w, i, "near") for i, w in enumerate(w for w in near if not w.census)])
        phase("date lontane ed eventi", far)

        def details():
            for lid in cat.missing_guests(today, int(sc.get("details_per_run", 60))):
                if left() < 5:
                    break
                d = client.details(lid)
                if d and d.get("guests"):
                    r = cat.rows[lid]
                    r["guests"], r["guests_src"] = float(d["guests"]), "pdp"
                    run["details"] += 1
        phase("capienze", details)
    except BudgetExhausted:
        run["stopped"] = "tetto di richieste o di tempo raggiunto"
    except Blocked as e:
        run["stopped"] = f"fermata dopo errori ripetuti: {e}"[:300]
    except Exception as e:  # noqa: BLE001 - salvo comunque quello che ho
        run["stopped"] = f"errore imprevisto: {e!r}"[:300]
    finally:
        run["prices"] = store.add_prices(today, price_rows) if price_rows else 0
        if count_rows:
            store.add_counts(today, count_rows)
        if seen_rows:
            store.add_seen(today, seen_rows)
        run["track"] = track
        cat.save()
        run.update(requests=client.requests, errors=client.errors, error_samples=getattr(client, "error_samples", []),
                   listings=len(cat.rows), secs=round(time.time() - t0, 1),
                   finished=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   windows_planned=len(windows), source=type(client).__name__)
        store.write_json(f"data/runs/{today.isoformat()}.json", run)
        state["last_scan"] = today.isoformat()
        state["last_run"] = {k: run[k] for k in ("date", "requests", "errors", "prices", "windows", "census", "stopped", "secs")}
        store.save_state(state)
        log(f"scansione: {run['windows']} finestre, {run['census']} letture complete su {track['windows']} "
            f"({track['found']} alloggi visti su {track['expected']} dichiarati, {track['resplits']} riquadri riletti), {run['prices']} prezzi, "
            f"{client.requests} richieste, {client.errors} errori, {run['secs']} s. {run['stopped']}")
    return run
