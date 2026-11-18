"""Lettura della ricerca pubblica di Airbnb tramite la libreria open source pyairbnb.

Regole di comportamento, volute:
  - una richiesta alla volta, con una pausa casuale tra l'una e l'altra;
  - un tetto di richieste e di minuti per esecuzione;
  - se Airbnb risponde con errori ripetuti la scansione si ferma: niente proxy, niente tentativi
    di aggirare un blocco.
"""
from __future__ import annotations

import base64
import random
import re
import time
from dataclasses import dataclass, field


class Blocked(Exception):
    """Troppi errori di fila: ci si ferma."""


class BudgetExhausted(Exception):
    """Finito il tetto di richieste o di tempo."""


@dataclass
class Card:
    id: int
    name: str = ""
    kind: str = ""
    lat: float | None = None
    lon: float | None = None
    bedrooms: int | None = None
    beds: int | None = None
    baths: float | None = None
    rating: float | None = None
    reviews: int = 0
    superhost: bool = False
    fav: int = 0            # 0 no · 1 "Amato dagli ospiti" · 2 tra i migliori (top 1/5/10%)
    price: float | None = None   # prezzo del soggiorno dopo gli sconti, senza tasse, pulizie e costi del servizio
    extra: dict | None = None    # voci in più che Airbnb aveva messo nel prezzo mostrato e che sono state tolte
    orig: float | None = None    # prezzo pieno, se c'è uno sconto
    nights: int | None = None


@dataclass
class Page:
    cards: list = field(default_factory=list)
    count: int | None = None      # alloggi disponibili nel riquadro per quelle date
    cursors: list = field(default_factory=list)


_NUM = re.compile(r"\d+(?:\.\d+)?")


def parse_money(s: str | None) -> float | None:
    """'€1,172' -> 1172.0 · '€186.22' -> 186.22 · '1.172 €' -> 1172.0"""
    if not s:
        return None
    t = re.sub(r"[^\d.,]", "", s)
    if not t:
        return None
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):      # 1.172,50
            t = t.replace(".", "").replace(",", ".")
        else:                                 # 1,172.50
            t = t.replace(",", "")
    elif "," in t:
        head, _, tail = t.rpartition(",")
        t = head.replace(",", "") + tail if len(tail) == 3 else head.replace(",", "") + "." + tail
    elif "." in t:
        head, _, tail = t.rpartition(".")
        if len(tail) == 3 and head:           # 1.172 = migliaia
            t = head.replace(".", "") + tail
    try:
        return float(t)
    except ValueError:
        return None


def parse_content(lines: list) -> tuple[int | None, int | None, float | None]:
    """Righe come '2 bedrooms', '3 double beds', '1.5 baths' -> (camere, letti, bagni)."""
    bedrooms = beds = None
    baths = None
    for raw in lines:
        s = (raw or "").strip().lower()
        if not s:
            continue
        if "studio" in s:
            bedrooms = 0
        elif "bedroom" in s:
            m = _NUM.search(s)
            if m:
                bedrooms = int(float(m.group()))
        elif "bath" in s:
            if s.startswith("no "):
                baths = 0.0
            else:
                m = _NUM.search(s)
                if m:
                    baths = float(m.group())
                elif "half" in s:
                    baths = 0.5
        elif re.search(r"\bbeds?\b", s):
            m = _NUM.search(s)
            if m:
                beds = (beds or 0) + int(float(m.group()))
    return bedrooms, beds, baths


def parse_rating(s: str | None) -> tuple[float | None, int]:
    """'4.77 (190)' -> (4.77, 190) · 'New' -> (None, 0)"""
    if not s:
        return None, 0
    m = re.match(r"\s*(\d+(?:[.,]\d+)?)\s*\(([\d.,]+)\)", s)
    if not m:
        return None, 0
    try:
        return float(m.group(1).replace(",", ".")), int(re.sub(r"\D", "", m.group(2)) or 0)
    except ValueError:
        return None, 0


def decode_id(b64: str | None) -> int:
    if not b64:
        return 0
    try:
        m = re.search(r"(\d+)$", base64.b64decode(b64).decode("utf-8", "ignore"))
        return int(m.group(1)) if m else 0
    except Exception:  # noqa: BLE001
        return 0


def _dig(d, path, default=None):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


_EXTRA = (("tax", ("tax", "vat", "tassa", "imposta", "iva")), ("cleaning", ("cleaning", "pulizia")),
          ("service", ("service fee", "servizio")))


def parse_breakdown(sdp: dict | None) -> dict | None:
    """Dettaglio del prezzo che Airbnb manda con ogni risultato ("2 nights x €95.50", sconti, eventuali pulizie,
    costi del servizio e tasse). Il primo gruppo sono le voci, gli altri i totali ("Price after discount", "Total")."""
    groups = [g for g in (_dig(sdp or {}, "explanationData.priceDetails", []) or []) if isinstance(g, dict) and g.get("items")]
    if not groups:
        return None
    out = {"base": 0.0, "discount": 0.0, "tax": 0.0, "cleaning": 0.0, "service": 0.0, "other": 0.0, "total": None, "lines": []}
    for gi, g in enumerate(groups):
        for it in g.get("items") or []:
            if not isinstance(it, dict):
                continue
            ps = str(it.get("priceString") or "")
            v = parse_money(ps)
            if v is None:
                continue
            if ps.strip().startswith(("-", "\u2212")) or it.get("__typename") == "DiscountedExplanationLineItem":
                v = -v
            d = str(it.get("description") or "").strip()
            out["lines"].append([d, v])
            t = d.lower()
            if gi > 0:
                out["total"] = v
            elif re.search(r"\bnights?\s*x\b", t):
                out["base"] += v
            elif v < 0:
                out["discount"] += v
            else:
                for k, words in _EXTRA:
                    if any(w in t for w in words):
                        out[k] += v
                        break
                else:
                    out["other"] += v
    return out


def strip_extras(price: float | None, orig: float | None, bd: dict | None):
    """Se nel prezzo mostrato ci sono tasse, pulizie o costi del servizio, li toglie: resta il prezzo per notte
    come lo imposta chi affitta, dopo gli sconti. Oggi la ricerca non li mette; serve se Airbnb cambia."""
    if not price or not bd:
        return price, orig, None
    extras = {k: round(bd[k], 2) for k in ("tax", "cleaning", "service") if bd[k] > 0}
    if not extras:
        return price, orig, None
    tot = sum(extras.values())
    with_extras = bd["base"] + bd["discount"] + bd["other"] + tot
    if abs(with_extras - price) > max(2.0, 0.02 * price):     # il prezzo mostrato non le comprende
        return price, orig, None
    keep = (price - tot) / price
    return round(price - tot, 2), (round(orig * keep, 2) if orig else None), extras


def parse_price(sdp: dict | None, nights: int | None) -> tuple[float | None, float | None, int | None]:
    """(prezzo totale del soggiorno, prezzo pieno se scontato, notti)."""
    pl = dict((sdp or {}).get("primaryLine") or {})
    for comp in pl.get("orderedComponents") or []:      # variante "a componenti" della stessa riga
        if isinstance(comp, dict):
            for k in ("price", "discountedPrice", "originalPrice", "qualifier"):
                if comp.get(k) and not pl.get(k):
                    pl[k] = comp[k]
    label = (pl.get("accessibilityLabel") or "").lower()
    amount = parse_money(pl.get("discountedPrice") or pl.get("price"))
    orig = parse_money(pl.get("originalPrice"))
    if amount is None and label:                         # ultima risorsa: l'etichetta per lettori vocali
        m0 = re.match(r"\s*([^\d\s]{0,3}\s?[\d.,]*\d)", label)
        amount = parse_money(m0.group(1)) if m0 else None
        m1 = re.search(r"originally\s+([^\d\s]{0,3}\s?[\d.,]*\d)", label)
        orig = orig or (parse_money(m1.group(1)) if m1 else None)
    q = (pl.get("qualifier") or "").lower()
    n = None
    m = re.search(r"for\s+(\d+)\s+night", q) or re.search(r"for\s+(\d+)\s+night", label)
    if m:
        n = int(m.group(1))
    elif "month" in q or "month" in label:
        return None, None, None          # prezzi mensili: non confrontabili
    elif re.search(r"\bnight\b", q) or re.search(r"per night|a night|/ ?night", label):
        if amount is not None and nights:
            amount, n = amount * nights, nights
            orig = orig * nights if orig else None
    elif "total" in q or "total" in label:
        n = nights
    if amount is None or not n:
        return None, None, None
    if orig is not None and orig <= amount:
        orig = None
    return amount, orig, n


def parse_page(raw: dict, nights: int | None = None) -> Page:
    """Estrae schede, conteggio e cursori da una risposta StaysSearch."""
    res = _dig(raw, "data.presentation.staysSearch.results", {}) or {}
    page = Page()
    cnt = _dig(res, "filters.filterPanel.resultCount")
    if isinstance(cnt, (int, float)):
        page.count = int(cnt)
    page.cursors = list(_dig(res, "paginationInfo.pageCursors", []) or [])
    for x in res.get("searchResults") or []:
        if not isinstance(x, dict) or x.get("__typename") not in (None, "StaySearchResult"):
            continue
        lid = decode_id(_dig(x, "demandStayListing.id"))
        if not lid:
            continue
        c = Card(id=lid)
        c.name = (_dig(x, "demandStayListing.description.name.localizedStringWithTranslationPreference")
                  or _dig(x, "nameLocalized.localizedStringWithTranslationPreference") or x.get("subtitle") or "")[:120]
        title = x.get("title") or ""
        c.kind = title.split(" in ")[0].strip() if " in " in title else title.strip()
        c.lat = _dig(x, "demandStayListing.location.coordinate.latitude")
        c.lon = _dig(x, "demandStayListing.location.coordinate.longitude")
        lines = [m.get("body") for m in (_dig(x, "structuredContent.primaryLine", []) or []) if isinstance(m, dict)]
        c.bedrooms, c.beds, c.baths = parse_content(lines)
        c.rating, c.reviews = parse_rating(x.get("avgRatingLocalized"))
        for b in x.get("badges") or []:
            t = (_dig(b, "loggingContext.badgeType") or "").upper()
            if t == "SUPERHOST":
                c.superhost = True
            elif t == "GUEST_FAVORITE":
                c.fav = max(c.fav, 1)
            elif "GUEST_FAVORITE" in t:
                c.fav = 2
        sdp = x.get("structuredDisplayPrice")
        c.price, c.orig, c.nights = parse_price(sdp, nights)
        c.price, c.orig, c.extra = strip_extras(c.price, c.orig, parse_breakdown(sdp))
        page.cards.append(c)
    return page


class AirbnbClient:
    """Ricerca dal vivo. L'interfaccia (start, search, details) è la stessa del simulatore."""

    def __init__(self, cfg: dict, log=print):
        sc = cfg["scan"]
        self.currency = cfg.get("currency", "EUR")
        self.room_type = cfg.get("room_type", "Entire home/apt")
        self.pause = tuple(sc.get("pause", (1.2, 2.2)))
        self.max_requests = int(sc.get("max_requests", 1400))
        self.deadline = time.time() + 60 * float(sc.get("max_minutes", 55))
        self.stop_after = int(sc.get("stop_after_errors", 4))
        self.log = log
        self.requests = 0
        self.errors = 0
        self._row = 0
        self._last = 0.0
        self.api_key = ""
        self.hash = ""
        self.error_samples: list[str] = []

    # -- ritmo e limiti
    def _wait(self):
        if self.requests >= self.max_requests or time.time() > self.deadline:
            raise BudgetExhausted()
        gap = random.uniform(*self.pause)
        dt = time.time() - self._last
        if dt < gap:
            time.sleep(gap - dt)
        self._last = time.time()
        self.requests += 1

    def _fail(self, e: Exception):
        self.errors += 1
        self._row += 1
        msg = re.sub(r"\s+", " ", repr(e))[:220]
        if len(self.error_samples) < 5:
            self.error_samples.append(msg)
        self.log(f"  errore ({self._row} di fila): {msg}")
        if self._row >= self.stop_after:
            raise Blocked(msg)
        time.sleep(15)  # prima di riprovare con la richiesta successiva

    def remaining(self) -> int:
        return max(0, self.max_requests - self.requests)

    # -- chiamate
    def start(self):
        import pyairbnb

        self._wait()
        self.api_key = pyairbnb.get_api_key("")
        self._wait()
        self.hash = pyairbnb.fetch_stays_search_hash()

    def search(self, box: dict, check_in: str = "", check_out: str = "", cursor: str = "",
               min_bedrooms: int = 0, min_bathrooms: int = 0, adults: int = 0) -> Page | None:
        """Una pagina di risultati per un riquadro; None se la richiesta fallisce."""
        import pyairbnb.search as s

        nights = None
        if check_in and check_out:
            from datetime import date

            nights = (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days
        self._wait()
        try:
            raw = s.get(
                api_key=self.api_key, cursor=cursor, check_in=check_in, check_out=check_out,
                ne_lat=box["ne"][0], ne_long=box["ne"][1], sw_lat=box["sw"][0], sw_long=box["sw"][1],
                zoom_value=14, currency=self.currency, place_type=self.room_type, price_min=0, price_max=0,
                amenities=[], free_cancellation=False, adults=adults, children=0, infants=0,
                min_bedrooms=min_bedrooms, min_beds=0, min_bathrooms=min_bathrooms, language="en",
                proxy_url="", hash=self.hash,
            )
        except Exception as e:  # noqa: BLE001
            self._fail(e)
            return None
        page = parse_page(raw, nights)
        self._row = 0
        return page

    def details(self, listing_id: int) -> dict | None:
        """Capienza e tipo di un annuncio (pagina pubblica dell'annuncio)."""
        import pyairbnb.details as d

        self._wait()
        try:
            data, _price_input, _cookies = d.get(f"https://www.airbnb.com/rooms/{listing_id}", "en", "")
        except Exception as e:  # noqa: BLE001
            self._fail(e)
            return None
        self._row = 0
        cap = data.get("person_capacity") or 0
        return {
            "guests": int(cap) if cap else None,
            "room_type": data.get("room_type") or "",
            "lat": _dig(data, "coordinates.latitude"),
            "lon": _dig(data, "coordinates.longitude"),
        }
