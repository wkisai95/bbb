"""L'app (JavaScript) e il programma (Python) devono dare gli stessi prezzi dallo stesso modello."""
import json
import os
import shutil
import subprocess
from datetime import date, timedelta

import pytest

from bnb import pricing

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.skipif(shutil.which("node") is None, reason="node non installato")
def test_app_and_program_agree(world, tmp_path):
    model = world["store"].read_json("public/model.json")
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model), encoding="utf-8")
    t = world["today"]
    cases = [
        dict(zone="c_maggiore", bedrooms=2, baths=1, guests=4, strategy="resa", quality="media", channel="airbnb", payers=2),
        dict(zone="c_maggiore", bedrooms=2, baths=1, guests=4, strategy="occupazione", quality="media", channel="booking", payers=4),
        dict(zone="fiera", bedrooms=1, baths=1, guests=2, strategy="resa", quality="alta", cost=20, channel="vrbo", payers=1),
        dict(zone="borgopanigale", bedrooms=3, baths=2, guests=7, strategy="resa", quality="base", minPrice=150,
             channel="direct", payers=6),
        dict(zone="colli", bedrooms=0, baths=1.5, guests=2, strategy="occupazione", quality="media", cost=0,
             channel="airbnb", feePct=12, vat=False, payers=2),
    ]
    spans = [(1, 90), (1, 90), (100, 140), (0, 40), (200, 360)]
    for c, (a, b) in zip(cases, spans):
        c["start"], c["end"] = (t + timedelta(days=a)).isoformat(), (t + timedelta(days=b)).isoformat()
    out = subprocess.run(["node", os.path.join(HERE, "js_recommend.js"), str(path), json.dumps(cases)],
                         capture_output=True, text=True, check=True)
    js = json.loads(out.stdout)
    for c, j in zip(cases, js):
        fee = pricing.fee_share(model, c["channel"], c.get("feePct"), c.get("vat"))
        assert abs(fee - j["feeShare"]) < 1e-9
        py = pricing.recommend(model, c["zone"], c["bedrooms"], c["baths"], c["guests"], date.fromisoformat(c["start"]),
                               date.fromisoformat(c["end"]), c["strategy"], c["quality"], c.get("minPrice", 0.0), c.get("cost"),
                               fee=fee, payers=c["payers"], tax_mode=pricing.channel(model, c["channel"])["tax"])
        assert abs(py["net"] - j["net"]) <= max(3, 0.002 * abs(py["net"]))
        assert abs(py["fees"] - j["fees"]) <= max(2, 0.002 * py["fees"]) and abs(py["tax"] - j["tax"]) <= max(2, 0.004 * py["tax"])
        assert len(py["nights"]) == len(j["nights"]) > 0
        assert abs(py["cost"] - j["cost"]) <= 1
        assert abs(py["avg_price"] - j["avgPrice"]) <= 1
        assert abs(py["occupancy"] - j["occupancy"]) < 2e-3
        assert abs(py["revenue"] - j["revenue"]) <= max(3, 0.002 * py["revenue"])
        assert py["reliability"]["label"] == j["reliability"]["label"]
        assert abs(py["reliability"]["score"] - j["reliability"]["score"]) < 2e-3
        for a, b in zip(py["nights"], j["nights"]):
            assert a["date"] == b["date"] and a["events"] == b["events"] and a["measured"] == b["measured"]
            assert abs(a["price"] - b["price"]) <= 1 and abs(a["market"] - b["market"]) <= 1
            assert abs(a["sell"] - b["sell"]) < 2e-3
            if abs(a["price"] - b["price"]) == 0:
                assert abs(a["fee"] - b["fee"]) < 0.01 and abs(a["payout"] - b["payout"]) < 0.01 and abs(a["tax"] - b["tax"]) < 0.01
            assert [p["lead"] for p in a["plan"]] == [p["lead"] for p in b["plan"]]
            assert all(abs(x["price"] - y["price"]) <= 1 for x, y in zip(a["plan"], b["plan"]))
