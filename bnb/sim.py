"""Mercato finto, usato SOLO dai test: stessa interfaccia della ricerca vera, ma con la verità nota.

Serve a controllare che scanner e modello ritrovino ciò che c'è davvero sotto: effetto di zona e
taglia, rincaro degli eventi, andamento delle prenotazioni, sensibilità al prezzo.
I dati generati qui non vengono mai pubblicati né mescolati a quelli reali.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np

from . import events as ev
from .airbnb import Card, Page
from .geo import Zones

TRUE = {
    "group": {"centro": 0.22, "cintura": 0.0, "periferia": -0.20, "fuori": -0.28},
    "br": {0: -0.12, 1: 0.0, 2: 0.27, 3: 0.48, 4: 0.66},
    "bath": 0.11, "guests": 0.22, "rating": 0.55, "fav2": 0.05,
    "weekend": 0.10,
    "event": {1: 1.03, 2: 1.10, 3: 1.25, 4: 1.50, 5: 1.90},
}


class SimMarket:
    def __init__(self, start: date, n: int = 1500, seed: int = 7, beta: float = -3.0, days: int = 60,
                 secret_peak: date | None = None):
        self.rng = np.random.default_rng(seed)
        self.zones = Zones()
        self.beta = beta
        self.origin = start - timedelta(days=130)             # il mercato "vive" da prima dell'inizio
        self.h = 130 + days + 400
        self.n = n
        rng = self.rng
        zs = self.zones.zones
        wts = np.array([6.0 if z["group"] == "centro" else 2.2 if z["group"] == "cintura" else 0.7 for z in zs])
        zi = rng.choice(len(zs), size=n, p=wts / wts.sum())
        self.lat = np.array([zs[i]["lat"] for i in zi]) + rng.normal(0, 0.0022, n)
        self.lon = np.array([zs[i]["lon"] for i in zi]) + rng.normal(0, 0.0030, n)
        self.zone = np.array([self.zones.zone_of(a, b) for a, b in zip(self.lat, self.lon)])
        self.group = np.array([self.zones.group_of(z) if z in self.zones.by_id else "periferia" for z in self.zone])
        self.zone_eff = {z["id"]: TRUE["group"][z["group"]] + rng.normal(0, 0.05) for z in zs}
        self.br = rng.choice([0, 1, 2, 3, 4], size=n, p=[0.06, 0.60, 0.25, 0.07, 0.02])
        self.baths = 1.0 + (self.br >= 2) * (rng.random(n) < 0.4) + (self.br >= 3) * (rng.random(n) < 0.3) * 1.0
        self.guests = np.clip(np.round(2 + 1.6 * self.br + rng.normal(0, 0.7, n)), 1, 12)
        self.beds = np.clip(np.round(self.guests / 1.7), 1, 8)
        self.rating = np.clip(rng.normal(4.82, 0.13, n), 4.2, 5.0).round(2)
        self.fav = np.where(self.rating > 4.9, 2, np.where(self.rating > 4.8, 1, 0))
        self.superhost = (rng.random(n) < 0.3).astype(int)
        self.value = rng.normal(0, 0.07, n)        # qualità che non si vede: alza prezzo E domanda
        self.markup = rng.normal(0, 0.20, n)       # puro ricarico: alza il prezzo, abbassa le prenotazioni
        self.min_n = rng.choice([1, 2, 3, 5], size=n, p=[0.2, 0.68, 0.08, 0.04])
        # calendari non ancora aperti: come nel mercato vero, a 300 giorni è prenotabile meno della metà degli alloggi
        self.open_days = rng.choice([100, 130, 170, 210, 260, 320, 9999], size=n, p=[0.08, 0.10, 0.10, 0.10, 0.10, 0.10, 0.42])
        self.rank = self.value - 0.5 * self.markup + rng.normal(0, 0.3, n)
        self.ids = 1_000_000 + np.arange(n)
        self.base = (4.42 + np.array([self.zone_eff.get(z, -0.2) for z in self.zone]) + np.vectorize(TRUE["br"].get)(self.br)
                     + TRUE["bath"] * (self.baths - 1) + TRUE["guests"] * np.log(self.guests / 4) + TRUE["rating"] * (self.rating - 4.8)
                     + TRUE["fav2"] * (self.fav == 2) + self.value + self.markup)
        # effetto delle notti
        evs = ev.all_events(start, days + 400)
        nw = ev.night_weights(evs)
        self.weight = np.zeros(self.h, dtype=int)
        self.nightfx = np.zeros(self.h)
        self.final_occ = np.zeros(self.h)
        for k in range(self.h):
            d = self.origin + timedelta(days=k)
            w = nw.get(d, (0, []))[0]
            we = d.weekday() in (4, 5)
            mult = TRUE["event"].get(w, 1.0)
            if secret_peak and 0 <= (d - secret_peak).days <= 1:
                mult, w = 1.45, 9
            self.weight[k] = w
            self.nightfx[k] = TRUE["weekend"] * we + math.log(mult) + 0.04 * math.sin(2 * math.pi * d.timetuple().tm_yday / 365)
            boost = {0: 0.0, 1: 0.02, 2: 0.07, 3: 0.14, 4: 0.22, 5: 0.30, 9: 0.2}[int(w)]
            self.final_occ[k] = min(0.96, 0.56 + 0.10 * we + boost)
        self.noise = rng.normal(0, 0.04, (n, self.h)).astype(np.float32)
        self.booked = np.zeros((n, self.h), dtype=bool)
        self.blocked = np.zeros((n, self.h), dtype=bool)
        for i in np.flatnonzero(rng.random(n) < 0.07):
            a = rng.integers(0, self.h - 30)
            self.blocked[i, a:a + rng.integers(20, 90)] = True
        self.day = self.origin
        self.history: dict[date, np.ndarray] = {}
        self.advance(start)

    @staticmethod
    def _share(lead):
        """Quota delle prenotazioni finali già arrivata a un certo anticipo."""
        lead = np.maximum(lead, 0)
        return 0.82 * np.exp(-lead / 26.0) + 0.18 * np.exp(-lead / 140.0)

    def advance(self, to: date):
        """Fa passare i giorni: ogni notte ancora libera può essere prenotata."""
        while self.day < to:
            t = (self.day - self.origin).days
            ks = np.arange(t, min(self.h, t + 380))
            lead = ks - t
            g_now, g_next = self.final_occ[ks] * self._share(lead), self.final_occ[ks] * self._share(lead - 1)
            hz = np.clip((g_next - g_now) / np.maximum(1 - g_now, 1e-6), 1e-6, 0.999)
            lg = np.log(hz / (1 - hz))[None, :] + self.beta * self.markup[:, None]
            p = 1 / (1 + np.exp(-lg))
            free = ~self.booked[:, ks] & ~self.blocked[:, ks]
            self.booked[:, ks] |= free & (self.rng.random(p.shape) < p)
            self.day += timedelta(days=1)
        self.history[to] = self.booked.copy()             # com'era quel giorno: serve ai test per sapere la verità

    def price(self, i, k):
        return np.exp(self.base[i] + self.nightfx[k] + self.noise[i, k])


class SimClient:
    """Stessa interfaccia di AirbnbClient, senza rete e senza pause."""

    def __init__(self, market: SimMarket, today: date, max_requests: int = 10 ** 9, jitter: float = 0.0):
        self.m = market
        self.today = today
        self.max_requests = max_requests
        self.jitter = jitter                      # ordine dei risultati instabile tra una pagina e l'altra, come dal vivo
        self.rng = np.random.default_rng(today.toordinal())
        self.requests = 0
        self.errors = 0
        self.error_samples: list = []
        market.advance(today)

    def start(self):
        self.requests += 2

    def remaining(self) -> int:
        return max(0, self.max_requests - self.requests)

    def search(self, box, check_in="", check_out="", cursor="", min_bedrooms=0, min_bathrooms=0, adults=0):
        from .airbnb import BudgetExhausted

        if self.requests >= self.max_requests:
            raise BudgetExhausted()
        self.requests += 1
        m = self.m
        inside = (m.lat >= box["sw"][0]) & (m.lat < box["ne"][0]) & (m.lon >= box["sw"][1]) & (m.lon < box["ne"][1])
        ok = inside & (m.br >= min_bedrooms) & (m.baths >= min_bathrooms) & (m.guests >= adults)
        if check_in and check_out:
            k0 = (date.fromisoformat(check_in) - m.origin).days
            nn = (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days
            lead = (date.fromisoformat(check_in) - self.today).days
            ks = np.arange(k0, k0 + nn)
            ok &= ~m.booked[:, ks].any(axis=1) & ~m.blocked[:, ks].any(axis=1) & (m.min_n <= nn) & (m.open_days >= lead)
        else:
            k0, nn = (self.today - m.origin).days + 10, 5
            ks = np.arange(k0, k0 + nn)
        idx = np.flatnonzero(ok)
        rank = m.rank[idx] + (self.rng.normal(0, self.jitter, len(idx)) if self.jitter else 0.0)
        idx = idx[np.argsort(-rank)]
        count = len(idx)
        n_pages = min(7, max(1, math.ceil(count / 40)))
        p = int(cursor[1:]) if cursor else 0
        page = Page(count=count, cursors=[f"p{j}" for j in range(n_pages)])
        for i in idx[p * 40:(p + 1) * 40]:
            total = float(sum(m.price(i, k) for k in ks))
            page.cards.append(Card(id=int(m.ids[i]), name=f"Simulato {i}", kind="Apartment", lat=float(m.lat[i]), lon=float(m.lon[i]),
                                   bedrooms=int(m.br[i]), beds=int(m.beds[i]), baths=float(m.baths[i]), rating=float(m.rating[i]),
                                   reviews=50, superhost=bool(m.superhost[i]), fav=int(m.fav[i]), price=round(total), orig=None, nights=nn))
        return page

    def details(self, listing_id: int):
        self.requests += 1
        i = int(listing_id) - 1_000_000
        return {"guests": int(self.m.guests[i]), "room_type": "Entire home/apt", "lat": float(self.m.lat[i]), "lon": float(self.m.lon[i])}
