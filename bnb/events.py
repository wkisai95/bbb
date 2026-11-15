"""Eventi che spostano la domanda: fiere, concerti, festività, eventi aggiunti a mano.

Tre fonti, in ordine di fiducia:
  1. config/events.yaml         elenco curato
  2. config/events_custom.json  eventi aggiunti dall'app
  3. calendari pubblici letti in automatico (BolognaFiere, Bologna Welcome)
più le festività italiane, calcolate.
"""
from __future__ import annotations

import html as htmllib
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

import yaml

from .config import CONFIG_DIR

MONTHS_IT = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7,
             "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12}
CATEGORIES = ("fiera", "congresso", "concerto", "sport", "cultura", "festivita", "altro", "mercato")


@dataclass
class Event:
    name: str
    start: date
    end: date
    venue: str = ""
    category: str = "altro"
    weight: int = 1
    source: str = "seed"
    note: str = ""
    nights_from: date | None = None
    nights_to: date | None = None
    id: str = field(default="")

    def __post_init__(self):
        if self.end < self.start:
            self.end = self.start
        self.weight = max(1, min(5, int(self.weight)))
        if self.category not in CATEGORIES:
            self.category = "altro"
        if not self.id:
            self.id = f"{slug(self.name)[:40]}-{self.start.isoformat()}"

    def nights(self) -> list[date]:
        """Notti in cui l'evento porta ospiti (la notte 'd' è quella tra d e d+1)."""
        if self.nights_from or self.nights_to:
            a = self.nights_from or self.start
            b = self.nights_to or self.end
        elif self.category in ("fiera", "congresso", "sport"):
            # si arriva la sera prima e si riparte l'ultimo giorno
            a, b = self.start - timedelta(days=1), self.end - timedelta(days=1)
        elif self.category == "cultura":
            a, b = self.start, max(self.start, self.end - timedelta(days=1))
        else:  # concerto, festività, altro: la notte di ogni giornata
            a, b = self.start, self.end
        if b < a:
            b = a
        return [a + timedelta(days=i) for i in range((b - a).days + 1)]

    def to_json(self) -> dict:
        d = asdict(self)
        for k in ("start", "end", "nights_from", "nights_to"):
            d[k] = d[k].isoformat() if d[k] else None
        ns = self.nights()
        d["night_first"] = ns[0].isoformat()
        d["night_last"] = ns[-1].isoformat()
        return d


def slug(s: str) -> str:
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _tokens(s: str) -> set:
    stop = {"di", "del", "della", "e", "the", "by", "il", "la", "le", "bologna", "bolognafiere", "fiera", "2026", "2027", "2028"}
    return {t for t in slug(s).split("-") if len(t) > 1 and t not in stop}


def _same_token(x: str, y: str) -> bool:
    if x == y:
        return True
    n = min(len(x), len(y))
    return n >= 4 and x[:n] == y[:n]  # fair / fairs, cosmoprof / cosmoprofs


def similar(a: str, b: str) -> bool:
    """Due nomi indicano lo stesso evento? (ARTEFIERA ~ Arte Fiera, Book Fairs ~ Book Fair)"""
    ca, cb = slug(a).replace("-", ""), slug(b).replace("-", "")
    if min(len(ca), len(cb)) >= 5 and (ca in cb or cb in ca):
        return True
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    inter = sum(1 for x in small if any(_same_token(x, y) for y in big))
    return inter >= 1 and inter / len(small) >= 0.6


def _as_date(v) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _from_dict(d: dict, source: str) -> Event | None:
    try:
        return Event(
            name=str(d["name"]).strip(), start=_as_date(d["start"]), end=_as_date(d.get("end") or d["start"]),
            venue=str(d.get("venue") or ""), category=str(d.get("category") or "altro"), weight=int(d.get("weight") or 1),
            source=source, note=str(d.get("note") or ""),
            nights_from=_as_date(d["nights_from"]) if d.get("nights_from") else None,
            nights_to=_as_date(d["nights_to"]) if d.get("nights_to") else None,
        )
    except Exception:  # noqa: BLE001 - una riga scritta male non deve fermare tutto
        return None


def load_seed(path: str | None = None) -> tuple[list[Event], list[dict]]:
    path = path or os.path.join(CONFIG_DIR, "events.yaml")
    if not os.path.exists(path):
        return [], []
    with open(path, encoding="utf-8") as f:
        d = yaml.safe_load(f) or {}
    evs = [e for e in (_from_dict(x, "seed") for x in d.get("events", [])) if e]
    return evs, d.get("auto_weights", [])


def load_custom(path: str | None = None) -> list[Event]:
    path = path or os.path.join(CONFIG_DIR, "events_custom.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:  # noqa: BLE001
        return []
    items = d.get("events", []) if isinstance(d, dict) else d
    return [e for e in (_from_dict(x, "custom") for x in items) if e]


# ---------------------------------------------------------------- festività

def easter(year: int) -> date:
    """Pasqua (calendario gregoriano, algoritmo di Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _bridge_nights(h: date) -> tuple[date, date]:
    """Notti del 'ponte' attorno a una festività."""
    wd = h.weekday()  # lun=0
    if wd == 0:   # lunedì: da venerdì a domenica
        return h - timedelta(days=3), h - timedelta(days=1)
    if wd == 1:   # martedì: da venerdì a lunedì
        return h - timedelta(days=4), h - timedelta(days=1)
    if wd == 2:   # mercoledì: solo la vigilia
        return h - timedelta(days=1), h - timedelta(days=1)
    if wd == 3:   # giovedì: da mercoledì a sabato
        return h - timedelta(days=1), h + timedelta(days=2)
    if wd == 4:   # venerdì: da giovedì a sabato
        return h - timedelta(days=1), h + timedelta(days=1)
    if wd == 5:   # sabato
        return h - timedelta(days=1), h
    return h - timedelta(days=2), h - timedelta(days=1)  # domenica


def holidays(start: date, end: date) -> list[Event]:
    out: list[Event] = []
    for y in range(start.year - 1, end.year + 2):
        e = easter(y)
        out.append(Event("Pasqua", e, e + timedelta(days=1), category="festivita", weight=2, source="festivita",
                         nights_from=e - timedelta(days=2), nights_to=e))
        out.append(Event("Capodanno", date(y, 12, 31), date(y + 1, 1, 1), category="festivita", weight=3, source="festivita",
                         nights_from=date(y, 12, 30), nights_to=date(y, 12, 31)))
        out.append(Event("Natale", date(y, 12, 25), date(y, 12, 26), category="festivita", weight=1, source="festivita",
                         nights_from=date(y, 12, 24), nights_to=date(y, 12, 26)))
        for name, (m, d) in {"Epifania": (1, 6), "Festa della Liberazione": (4, 25), "Primo Maggio": (5, 1),
                             "Festa della Repubblica": (6, 2), "Ognissanti": (11, 1), "Immacolata": (12, 8),
                             "San Petronio (patrono)": (10, 4)}.items():
            h = date(y, m, d)
            a, b = _bridge_nights(h)
            w = 1 if name.startswith(("Epifania", "San Petronio")) else 2
            out.append(Event(f"Ponte · {name}" if (b - a).days >= 2 else name, h, h, category="festivita", weight=w,
                             source="festivita", nights_from=a, nights_to=b))
    return [e for e in out if e.nights()[-1] >= start and e.nights()[0] <= end]


# ---------------------------------------------------------------- calendari pubblici

def _text(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_bolognafiere(page: str, today: date) -> list[dict]:
    """Schede del calendario di bolognafiere.it: sede, data inizio, data fine, città, formato, nome."""
    out = []
    for chunk in page.split('class="card card_evento')[1:]:
        chunk = chunk[:6000]
        dates = re.findall(r"<div>\s*(\d{2})\.(\d{2})\.(\d{4})\s*</div>", chunk)
        if not dates:
            continue
        try:
            start = date(int(dates[0][2]), int(dates[0][1]), int(dates[0][0]))
            last = dates[1] if len(dates) > 1 else dates[0]
            end = date(int(last[2]), int(last[1]), int(last[0]))
        except ValueError:
            continue
        venue = _text((re.search(r'<div class="venue">(.*?)</div>', chunk, re.S) or [None, ""])[1])
        m_city = re.search(r"<div>\s*\d{2}\.\d{2}\.\d{4}\s*</div>\s*<div>\s*\d{2}\.\d{2}\.\d{4}\s*</div>\s*<div>([^<]*)</div>", chunk)
        city = _text(m_city.group(1)) if m_city else ""
        fmt = _text((re.search(r'card_evento-format[^>]*>(.*?)</div>', chunk, re.S) or [None, ""])[1])
        name = _text((re.search(r'<div class="fs-3[^"]*">(.*?)</div>', chunk, re.S) or [None, ""])[1])
        if not name:
            m = re.search(r'aria-label="Visita il sito di ([^"]+)"', chunk)
            name = htmllib.unescape(m.group(1)).strip() if m else ""
        if not name or end < today:
            continue
        out.append({"name": name, "start": start, "end": end, "venue": venue, "city": city, "format": fmt})
    return out


def parse_bolognawelcome_concerts(page: str, today: date) -> list[dict]:
    """Voci del tipo «Artista, 5, 6, 8 febbraio 2027 - Unipol Arena»."""
    page = re.sub(r"<(s|del|strike)\b[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    months = "|".join(MONTHS_IT)
    pat = re.compile(rf"^(?P<name>.+?),\s*(?P<days>[\d\s,e\-–]+?)\s+(?P<month>{months})(?:\s+(?P<year>\d{{4}}))?\s*[-–]\s*(?P<venue>.+)$", re.I)
    seen, out = set(), []
    for li in re.findall(r"<li\b[^>]*>(.*?)</li>", page, flags=re.S | re.I):
        txt = _text(li.replace('\\"', '"'))
        m = pat.match(txt)
        if not m:
            continue
        try:
            month = MONTHS_IT[m.group("month").lower()]
            if m.group("year"):
                year = int(m.group("year"))
            else:  # anno sottinteso: la prossima volta che capita quel mese
                year = today.year if month >= today.month else today.year + 1
            days: list[int] = []
            for part in re.split(r"[,e]", m.group("days")):
                nums = [int(x) for x in re.findall(r"\d{1,2}", part)]
                if len(nums) == 2 and 0 < nums[1] - nums[0] <= 3 and re.match(r"^\s*\d{1,2}\s*[-–]\s*\d{1,2}\s*$", part):
                    days += list(range(nums[0], nums[1] + 1))  # "27-28" = dal 27 al 28
                else:
                    days += nums
            dates = sorted({date(year, month, d) for d in days if 1 <= d <= 31})
        except (ValueError, KeyError):
            continue
        name = m.group("name").strip(" ,")
        venue = re.sub(r"\s*\(.*$", "", m.group("venue")).strip()
        # giorni consecutivi diventano un solo evento
        runs: list[list[date]] = []
        for d in dates:
            if runs and (d - runs[-1][-1]).days == 1:
                runs[-1].append(d)
            else:
                runs.append([d])
        for run in runs:
            key = (slug(name), run[0])
            if run[-1] < today or key in seen or len(name) > 80:
                continue
            seen.add(key)
            out.append({"name": name, "start": run[0], "end": run[-1], "venue": venue})
    return out


def _weight_for(name: str, venue: str, rules: list[dict], default: int) -> int:
    hay = f"{name} {venue}".lower().replace("\u2019", "'")
    for r in rules:
        if str(r.get("match", "")).lower() in hay:
            return int(r.get("weight", default))
    return default


def auto_events(pages: dict, today: date, rules: list[dict]) -> list[Event]:
    """pages = {"bolognafiere": html, "bolognawelcome": html}; ciò che manca viene saltato."""
    out: list[Event] = []
    for x in parse_bolognafiere(pages.get("bolognafiere") or "", today):
        if x["city"].lower() != "bologna" or (x["end"] - x["start"]).days > 12:
            continue
        fmt = x["format"].lower()
        cat = "congresso" if fmt.startswith("congresso") else "sport" if fmt.startswith("sport") else "fiera"
        weekdays = sum(1 for i in range((x["end"] - x["start"]).days + 1) if (x["start"] + timedelta(days=i)).weekday() < 4)
        default = 2 if weekdays >= 2 else 1
        out.append(Event(x["name"], x["start"], x["end"], venue=x["venue"], category=cat,
                         weight=_weight_for(x["name"], "", rules, default), source="auto"))
    for x in parse_bolognawelcome_concerts(pages.get("bolognawelcome") or "", today):
        v = x["venue"].lower()
        if "stadio" in v:
            w = 3
        elif "unipol arena" in v:
            w = 1
        elif "imola" in v or "parco nord" in v:
            w = 2
        else:
            continue  # locali piccoli: non spostano i prezzi della città
        out.append(Event(x["name"], x["start"], x["end"], venue=x["venue"], category="concerto", weight=w, source="auto"))
    return out


# ---------------------------------------------------------------- unione

def merge(seed: list[Event], custom: list[Event], auto: list[Event], hol: list[Event]) -> list[Event]:
    """Curati e manuali vincono; un evento automatico entra solo se non è già coperto."""
    kept: list[Event] = list(custom) + list(seed)

    curated = list(kept)

    def covered(e: Event) -> bool:
        for k in kept:
            if k.start <= e.end + timedelta(days=1) and e.start <= k.end + timedelta(days=1) and similar(k.name, e.name):
                return True
        for k in curated:      # salone "ospite" nelle stesse date e nella stessa sede di un evento già in elenco
            if (k.venue and slug(k.venue) == slug(e.venue) and k.weight >= e.weight
                    and e.start >= k.start - timedelta(days=1) and e.end <= k.end + timedelta(days=1)):
                return True
        return False

    for e in auto:
        if not covered(e):
            kept.append(e)
    kept += hol
    seen, out = set(), []
    for e in sorted(kept, key=lambda x: (x.start, -x.weight, x.name)):
        if e.id in seen:
            continue
        seen.add(e.id)
        out.append(e)
    return out


def night_weights(events: list[Event]) -> dict[date, tuple[int, list[str]]]:
    """Per ogni notte: peso massimo e nomi degli eventi (dal più pesante)."""
    acc: dict[date, list[tuple[int, str]]] = {}
    for e in events:
        for n in e.nights():
            acc.setdefault(n, []).append((e.weight, e.name))
    out = {}
    for n, lst in acc.items():
        lst.sort(key=lambda t: -t[0])
        names = []
        for _, nm in lst:
            if nm not in names:
                names.append(nm)
        out[n] = (lst[0][0], names)
    return out


def all_events(today: date, horizon_days: int, auto: list[Event] | None = None,
               seed_path: str | None = None, custom_path: str | None = None) -> list[Event]:
    seed, _ = load_seed(seed_path)
    custom = load_custom(custom_path)
    end = today + timedelta(days=horizon_days)
    hol = holidays(today - timedelta(days=40), end)
    evs = merge(seed, custom, auto or [], hol)
    return [e for e in evs if e.nights()[-1] >= today - timedelta(days=40) and e.nights()[0] <= end]
