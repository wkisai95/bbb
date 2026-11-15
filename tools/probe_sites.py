"""Prova di raggiungibilità degli altri portali: si possono leggere come fa un visitatore, senza forzare nulla?

Per ogni sito: legge robots.txt e, solo se la pagina di ricerca non è vietata, la chiede UNA volta.
Nessun nuovo tentativo, nessun proxy, nessuna soluzione di verifiche anti-robot: se il sito risponde con
un blocco o una verifica, la prova si ferma lì e lo riporta. Non salva le pagine: stampa solo un riepilogo.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import date, timedelta
from urllib import robotparser
from urllib.parse import urlsplit

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "it-IT,it;q=0.9,en;q=0.6"}
BLOCK = ["captcha", "px-captcha", "perimeterx", "datadome", "awswaf", "challenge-platform", "cf-chl", "access denied",
         "are you a robot", "sei un robot", "unusual traffic", "bot detection", "akamai", "incapsula", "request unsuccessful",
         "verifica di essere un essere umano", "just a moment", "pardon our interruption"]


def sites(ci: date, co: date) -> list[tuple[str, str]]:
    a, b = ci.isoformat(), co.isoformat()
    return [
        ("Airbnb (ricerca)", f"https://www.airbnb.it/s/Bologna/homes?checkin={a}&checkout={b}&adults=2"),
        ("Airbnb (interfaccia usata dallo scanner)", "https://www.airbnb.it/api/v3/StaysSearch/x"),
        ("Booking.com", f"https://www.booking.com/searchresults.it.html?ss=Bologna&checkin={a}&checkout={b}&group_adults=2&no_rooms=1&nflt=ht_id%3D201"),
        ("Vrbo", f"https://www.vrbo.com/it-it/search?destination=Bologna%2C%20Emilia-Romagna%2C%20Italia&startDate={a}&endDate={b}&adults=2"),
        ("Expedia", f"https://www.expedia.it/Hotel-Search?destination=Bologna%2C%20Emilia-Romagna%2C%20Italia&startDate={a}&endDate={b}&adults=2"),
        ("Hotels.com", f"https://it.hotels.com/Hotel-Search?destination=Bologna%2C%20Emilia-Romagna%2C%20Italia&startDate={a}&endDate={b}&adults=2"),
        ("Agoda", f"https://www.agoda.com/it-it/search?textToSearch=Bologna&checkIn={a}&los=2&rooms=1&adults=2"),
        ("Tripadvisor", "https://www.tripadvisor.it/VacationRentals-g187801-Reviews-Bologna_Province_of_Bologna_Emilia_Romagna-Vacation_Rentals.html"),
        ("Holidu", f"https://www.holidu.it/s/Bologna--Italia?checkin={a}&checkout={b}&adults=2"),
        ("HomeToGo", "https://www.hometogo.it/bologna/"),
        ("CaseVacanza.it", "https://www.casevacanza.it/bologna"),
    ]


def robots_for(host_url: str, cache: dict) -> tuple[robotparser.RobotFileParser | None, str]:
    parts = urlsplit(host_url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base in cache:
        return cache[base]
    try:
        r = requests.get(base + "/robots.txt", headers=UA, timeout=30)
        note = f"robots.txt {r.status_code}"
        if r.status_code == 200 and "<html" not in r.text[:300].lower():
            rp = robotparser.RobotFileParser()
            rp.parse(r.text.splitlines())
            cache[base] = (rp, note)
        else:
            cache[base] = (None, note + " (non leggibile)")
    except Exception as e:  # noqa: BLE001
        cache[base] = (None, f"robots.txt non raggiungibile: {type(e).__name__}")
    time.sleep(3)
    return cache[base]


def main() -> int:
    ci = date.today() + timedelta(days=23)
    rows, cache = [], {}
    for name, url in sites(ci, ci + timedelta(days=2)):
        rp, note = robots_for(url, cache)
        allowed = rp.can_fetch("*", url) if rp else None
        row = {"sito": name, "robots": note, "consentito": {True: "sì", False: "NO", None: "?"}[allowed], "esito": "-"}
        if allowed is False:
            row["esito"] = "non richiesta: robots.txt la vieta"
        elif name.startswith("Airbnb"):
            row["esito"] = "non richiesta (già nota)"
        else:
            try:
                r = requests.get(url, headers=UA, timeout=40, allow_redirects=True)
                text = r.text or ""
                low = text.lower()
                marks = sorted({m for m in BLOCK if m in low})[:4]
                title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
                prices = len(re.findall(r"(?:€\s?\d{2,4}|\d{2,4}\s?€)", text))
                row["esito"] = (f"{r.status_code}, {len(text) // 1000} kB, titolo «{(title.group(1).strip()[:60] if title else '')}», "
                                f"prezzi visibili: {prices}, segnali di blocco: {', '.join(marks) or 'nessuno'}")
            except Exception as e:  # noqa: BLE001
                row["esito"] = f"errore: {type(e).__name__}"
            time.sleep(6)
        rows.append(row)
        print(f"{row['sito']} | {row['robots']} | ricerca consentita: {row['consentito']} | {row['esito']}", flush=True)
    out = ["| Sito | robots.txt | Ricerca consentita | Risposta alla pagina di ricerca |", "|---|---|---|---|"]
    out += [f"| {r['sito']} | {r['robots']} | {r['consentito']} | {r['esito']} |" for r in rows]
    if len(sys.argv) > 1:
        open(sys.argv[1], "a", encoding="utf-8").write("### Prova dei portali\n\n" + "\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
