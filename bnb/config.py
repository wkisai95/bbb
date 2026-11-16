"""Impostazioni: valori predefiniti + config/settings.yaml."""
from __future__ import annotations

import copy
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")

DEFAULTS: dict = {
    "city": "Bologna",
    "currency": "EUR",
    "timezone": "Europe/Rome",
    "bbox": {"sw_lat": 44.421, "sw_lon": 11.230, "ne_lat": 44.556, "ne_lon": 11.433},
    "room_type": "Entire home/apt",
    "scan": {
        "nights": 2,
        "near_days": 60,
        "mid_days": 180,
        "far_days": 365,
        "mid_share": 2,
        "far_share": 4,
        "track_days": 30,
        "track_max_pages": 90,
        "track_requests": 950,
        "track_resplit_share": 0.06,
        "track_min_share": 0.95,
        "pause": [1.2, 2.2],
        "max_requests": 1600,
        "max_minutes": 65,
        "details_per_run": 60,
        "stop_after_errors": 4,
    },
    "tiles": [],
    "segments": [{"id": "br2", "min_bedrooms": 2}, {"id": "br3", "min_bedrooms": 3}],
    "model": {
        "fit_days": 21,
        "min_price": 25,
        "max_price": 1500,
        "ridge_zone": 6.0,
        "ridge_window": 4.0,
        "beta_prior": -2.2,
        "beta_prior_sd": 0.8,
        "target_occupancy": 0.90,
        "band": [10, 90],
        "max_discount": 0.25,
        "max_premium": 0.40,
        "nu": None,
        "cost_share": 0.30,
        "event_prior": {1: 1.03, 2: 1.10, 3: 1.25, 4: 1.50, 5: 1.90},
    },
    "sources": {"inside_airbnb": True, "inside_airbnb_every_days": 7, "events_auto": True},
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings(path: str | None = None) -> dict:
    path = path or os.path.join(CONFIG_DIR, "settings.yaml")
    extra = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            extra = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, extra)
    cfg["model"]["event_prior"] = {int(k): float(v) for k, v in cfg["model"]["event_prior"].items()}
    if not cfg["tiles"]:
        b = cfg["bbox"]
        cfg["tiles"] = [{"id": "all", "kind": "core", "sw": [b["sw_lat"], b["sw_lon"]], "ne": [b["ne_lat"], b["ne_lon"]]}]
    return cfg
