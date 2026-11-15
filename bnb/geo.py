"""Zone e quartieri: a quale zona appartiene un punto."""
from __future__ import annotations

import json
import math
import os

from .config import CONFIG_DIR


def _in_ring(lat: float, lon: float, ring: list) -> bool:
    """Ray casting; ring è una lista di [lat, lon]."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        yi, xi = ring[i][0], ring[i][1]
        yj, xj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanza in km (approssimazione piana, più che sufficiente a scala urbana)."""
    dy = (lat2 - lat1) * 111.2
    dx = (lon2 - lon1) * 111.2 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


class Zones:
    def __init__(self, path: str | None = None, quartieri_path: str | None = None):
        path = path or os.path.join(CONFIG_DIR, "zones.json")
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        self.ring = d["ring"]
        self.zones = d["zones"]
        self.groups = d["groups"]
        self.max_km = float(d.get("max_km", 4.5))
        self.venues = d.get("venues", {})
        self.by_id = {z["id"]: z for z in self.zones}
        self._inner = [z for z in self.zones if z.get("ring")]
        self._outer = [z for z in self.zones if not z.get("ring")]
        self.quartieri = []
        qp = quartieri_path or os.path.join(CONFIG_DIR, "quartieri.geojson")
        if os.path.exists(qp):
            with open(qp, encoding="utf-8") as f:
                g = json.load(f)
            for feat in g.get("features", []):
                geom = feat["geometry"]
                polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
                # GeoJSON è [lon, lat]: giro in [lat, lon] e tengo solo l'anello esterno
                rings = [[[p[1], p[0]] for p in poly[0]] for poly in polys if poly]
                self.quartieri.append((feat["properties"]["name"], rings))

    def zone_of(self, lat: float, lon: float) -> str:
        """Id della zona, oppure 'altro' se il punto è lontano da tutte."""
        if lat is None or lon is None or not (lat == lat) or not (lon == lon):
            return "altro"
        pool = self._inner if _in_ring(lat, lon, self.ring) else self._outer
        best, best_d = None, 1e9
        for z in pool:
            d = km(lat, lon, z["lat"], z["lon"])
            if d < best_d:
                best, best_d = z, d
        if best is None or best_d > self.max_km:
            return "altro"
        return best["id"]

    def group_of(self, zone_id: str) -> str:
        z = self.by_id.get(zone_id)
        return z["group"] if z else "fuori"

    def name_of(self, zone_id: str) -> str:
        z = self.by_id.get(zone_id)
        return z["name"] if z else "Fuori zona"

    def quartiere_of(self, lat: float, lon: float) -> str:
        for name, rings in self.quartieri:
            for r in rings:
                if _in_ring(lat, lon, r):
                    return name
        return ""

    def public(self) -> list:
        """Elenco zone per l'app."""
        out = [{"id": z["id"], "name": z["name"], "short": z.get("short", z["name"]), "group": z["group"],
                "lat": z["lat"], "lon": z["lon"]} for z in self.zones]
        return out
