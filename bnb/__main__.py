"""Uso:  python -m bnb all --out out      scansione + modello + file per l'app + backup
          python -m bnb scan --out out     solo scansione
          python -m bnb model --out out    solo modello e file per l'app (dai dati già raccolti)
          python -m bnb backup --out out   solo archivio ZIP
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import insideairbnb, model, publish, scan
from .config import load_settings
from .store import Store


def log(*a):
    print(datetime.now(timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


def inside_airbnb(cfg: dict, store: Store, today: date, with_listings: bool):
    """Dati aperti di Inside Airbnb: l'elenco degli annunci ogni N giorni, i calendari una volta per istantanea.

    I calendari servono a tarare il modello della domanda (quanto pesa una finestra di più notti, come cambia
    la disponibilità con l'anticipo). Se la taratura manca si riprova, al massimo una volta ogni due giorni.
    Restituisce (tabella, data) quando l'elenco va riversato nel catalogo, altrimenti (None, "").
    """
    if not cfg["sources"].get("inside_airbnb"):
        return None, ""
    state = store.load_state()
    ia = state.get("inside_airbnb") or {}
    cal = state.get("calibration") or {}
    every = int(cfg["sources"].get("inside_airbnb_every_days", 7))
    due = with_listings and (not ia.get("merged") or (today - date.fromisoformat(ia["merged"])) >= timedelta(days=every))
    tried = state.get("calibration_tried") or ""
    need_cal = not cal.get("profile") and (not tried or (today - date.fromisoformat(tried)) >= timedelta(days=2))
    if not (due or need_cal):
        return None, ""
    got = insideairbnb.download(log)
    if not got:
        return None, ""
    ia_df, ia_date = got
    if need_cal or cal.get("snapshot") != ia_date:
        n = int(cfg["scan"]["nights"])
        ok = ia_df[(ia_df["room_type"] == cfg.get("room_type", "")) & (ia_df["min_nights"] <= n)]
        res = insideairbnb.download_calendar_factor(ia_date, ok["id"].tolist(), n, log)
        state = store.load_state()
        state["calibration_tried"] = today.isoformat()
        if res:
            state["calibration"] = {"nu": res["nu"], "snapshot": ia_date, "source": f"Inside Airbnb {ia_date}",
                                    "listings": res["listings"], "profile": res.get("profile")}
        store.save_state(state)
    return (ia_df, ia_date) if due else (None, "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bnb")
    ap.add_argument("cmd", choices=["all", "scan", "model", "backup"])
    ap.add_argument("--out", default="out")
    ap.add_argument("--date", default="", help="data di riferimento (AAAA-MM-GG), solo per prove")
    ap.add_argument("--max-requests", type=int, default=0, help="tetto di richieste per questa esecuzione")
    ap.add_argument("--no-inside", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_settings()
    if args.max_requests:
        cfg["scan"]["max_requests"] = args.max_requests
    today = date.fromisoformat(args.date) if args.date else datetime.now(ZoneInfo(cfg.get("timezone", "Europe/Rome"))).date()
    store = Store(args.out)
    run = None

    if args.cmd in ("all", "scan"):
        from .airbnb import AirbnbClient

        ia_df, ia_date = (None, "") if args.no_inside else inside_airbnb(cfg, store, today, with_listings=True)
        pages = scan.fetch_event_pages(log) if cfg["sources"].get("events_auto") else None
        run = scan.run_scan(cfg, store, AirbnbClient(cfg, log), today, log, pages=pages, inside=ia_df, inside_date=ia_date)
    elif args.cmd == "model" and not args.no_inside:
        inside_airbnb(cfg, store, today, with_listings=False)      # solo la taratura, se manca

    if args.cmd in ("all", "model"):
        if run is None:
            run = store.read_json(f"data/runs/{today.isoformat()}.json")
        m = model.build(store, cfg, today, log)
        status = publish.publish(store, m, today, run, cfg, log)
        log(f"pubblicato: modello={'sì' if m else 'no'}, avvisi={status['warnings']}")

    if args.cmd in ("all", "backup"):
        publish.make_backup(store, os.path.join(args.out, "backup", "bbb-backup.zip"), log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
