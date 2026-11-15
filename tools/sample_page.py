"""Scarica UNA pagina pubblica e la salva, per studiarne la struttura prima di scrivere un lettore."""
import sys
from datetime import date, timedelta

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "it-IT,it;q=0.9,en;q=0.6"}
ci = date.today() + timedelta(days=23)
url = f"https://www.holidu.it/s/Bologna--Italia?checkin={ci.isoformat()}&checkout={(ci + timedelta(days=2)).isoformat()}&adults=2"
r = requests.get(url, headers=UA, timeout=40)
print(url, r.status_code, len(r.text))
open(sys.argv[1], "w", encoding="utf-8").write(r.text)
open(sys.argv[1] + ".url", "w", encoding="utf-8").write(url + "\n" + str(r.status_code) + "\n")
