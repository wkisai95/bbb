import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bnb import model, publish, scan  # noqa: E402
from bnb.config import load_settings  # noqa: E402
from bnb.sim import SimClient, SimMarket  # noqa: E402
from bnb.store import Store  # noqa: E402

START = date(2026, 11, 16)
DAYS = 16


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    cfg = load_settings()
    cfg["model"]["nu"] = 2.0                       # nel simulatore le notti si prenotano una per una
    secret = START + timedelta(days=DAYS + 20)     # un evento che il calendario non conosce
    mk = SimMarket(START, n=1500, seed=3, beta=-3.0, days=DAYS + 5, secret_peak=secret)
    store = Store(str(tmp_path_factory.mktemp("out")))
    run = None
    for i in range(DAYS):
        today = START + timedelta(days=i)
        run = scan.run_scan(cfg, store, SimClient(mk, today), today, log=lambda *a: None)
        assert not run["stopped"]
    today = START + timedelta(days=DAYS - 1)
    m = model.build(store, cfg, today, log=lambda *a: None)
    status = publish.publish(store, m, today, run, cfg)
    return {"cfg": cfg, "mk": mk, "store": store, "model": m, "today": today, "secret": secret, "status": status, "run": run}


