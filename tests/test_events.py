from datetime import date

from bnb import events as ev

TODAY = date(2026, 11, 15)

BF = '''
<div class="col"><div class="card card_evento position-relative"><div class="venue">BolognaFiere</div>
 <div class="row"><div class="col-auto d-flex flex-column align-items-end">
  <div>03.03.2027</div> <div>05.03.2027</div> <div>Bologna</div> <div class="card_evento-format ">Fiera</div></div></div>
 <div class="fs-3 fw-bold text-uppercase mb-3">MECSPE</div></div></div>
<div class="col"><div class="card card_evento position-relative"><div class="venue">Worldwide</div>
 <div class="row"><div class="col-auto d-flex flex-column align-items-end">
  <div>18.11.2026</div> <div>19.11.2026</div> <div>Tashkent</div> <div class="card_evento-format ">Fiera</div></div></div>
 <div class="fs-3 fw-bold text-uppercase mb-3">MEDINIT EXPO</div></div></div>
<div class="col"><div class="card card_evento position-relative"><div class="venue">BolognaFiere</div>
 <div class="row"><div class="col-auto d-flex flex-column align-items-end">
  <div>09.05.2028</div> <div>12.05.2028</div> <div>Bologna</div> <div class="card_evento-format ">Fiera</div></div></div>
 <div class="fs-3 fw-bold text-uppercase mb-3">Fiera Nuova Mai Vista</div></div></div>
<div class="col"><div class="card card_evento position-relative"><div class="venue">BolognaFiere</div>
 <div class="row"><div class="col-auto d-flex flex-column align-items-end">
  <div>05.04.2027</div> <div>08.04.2027</div> <div>Bologna</div> <div class="card_evento-format ">Fiera</div></div></div>
 <div class="fs-3 fw-bold text-uppercase mb-3">BOLOGNA CHILDREN&#8217;S BOOK FAIRS</div></div></div>
'''

BW = '''<ul>
<li><a href="x">Max Pezzali</a>, 5, 6, 8 febbraio 2027 - Unipol Arena</li>
<li><a href="x">Cesare Cremonini</a>, <strike>27,28, 30 novembre,</strike>&nbsp;2, <strike>4</strike>-5-6 dicembre - Unipol Arena</li>
<li><strong><a href="x">Arisa</a></strong>, 10 dicembre 2026 – Teatro EuropAuditorium</li>
<li>Pinguini Tattici Nucleari, 8 giugno 2027 - Stadio Renato Dall'Ara</li>
<li>Vecchio Concerto, 3 giugno 2026 - Stadio Renato Dall'Ara</li>
</ul>'''


def test_bolognafiere_cards():
    got = ev.parse_bolognafiere(BF, TODAY)
    names = {x["name"]: x for x in got}
    assert names["MECSPE"]["start"] == date(2027, 3, 3) and names["MECSPE"]["end"] == date(2027, 3, 5)
    assert names["MECSPE"]["city"] == "Bologna" and names["MECSPE"]["venue"] == "BolognaFiere"
    assert names["MEDINIT EXPO"]["city"] == "Tashkent"


def test_concert_list():
    got = ev.parse_bolognawelcome_concerts(BW, TODAY)
    key = {(x["name"], x["start"], x["end"]) for x in got}
    assert ("Max Pezzali", date(2027, 2, 5), date(2027, 2, 6)) in key
    assert ("Max Pezzali", date(2027, 2, 8), date(2027, 2, 8)) in key
    assert ("Cesare Cremonini", date(2026, 12, 2), date(2026, 12, 2)) in key      # date barrate ignorate, anno sottinteso
    assert ("Cesare Cremonini", date(2026, 12, 5), date(2026, 12, 6)) in key
    assert not any(x["name"] == "Vecchio Concerto" for x in got)                   # già passato


def test_auto_events_filter_and_weights():
    _, rules = ev.load_seed()
    auto = ev.auto_events({"bolognafiere": BF, "bolognawelcome": BW}, TODAY, rules)
    by = {e.name: e for e in auto}
    assert "MEDINIT EXPO" not in by                       # non è a Bologna
    assert "Arisa" not in by                              # teatro: troppo piccolo per spostare i prezzi
    assert by["MECSPE"].weight == 4
    assert by["Pinguini Tattici Nucleari"].weight == 3    # stadio
    assert by["BOLOGNA CHILDREN’S BOOK FAIRS"].weight == 4
    assert by["Fiera Nuova Mai Vista"].weight == 2        # fiera feriale sconosciuta: peso medio


def test_merge_keeps_curated_and_adds_new():
    seed, rules = ev.load_seed()
    auto = ev.auto_events({"bolognafiere": BF, "bolognawelcome": BW}, TODAY, rules)
    merged = ev.merge(seed, [], auto, [])
    names = [e.name for e in merged]
    assert names.count("MECSPE") == 1 and "Fiera Nuova Mai Vista" in names
    assert not any(e.source == "auto" and "CHILDREN" in e.name for e in merged)
    assert ev.similar("ARTEFIERA", "Arte Fiera") and not ev.similar("Zoomark", "Autopromotec")


def test_nights_and_holidays():
    fair = ev.Event("X", date(2027, 3, 18), date(2027, 3, 21), category="fiera", weight=5)
    assert fair.nights()[0] == date(2027, 3, 17) and fair.nights()[-1] == date(2027, 3, 20)
    gig = ev.Event("Y", date(2027, 6, 8), date(2027, 6, 8), category="concerto", weight=3)
    assert gig.nights() == [date(2027, 6, 8)]
    assert ev.easter(2027) == date(2027, 3, 28) and ev.easter(2026) == date(2026, 4, 5)
    hol = ev.holidays(date(2026, 11, 15), date(2027, 11, 15))
    imm = next(e for e in hol if "Immacolata" in e.name)          # 8 dicembre 2026 è martedì: ponte da venerdì
    assert imm.nights()[0] == date(2026, 12, 4) and imm.nights()[-1] == date(2026, 12, 7)
    nw = ev.night_weights([fair, gig])
    assert nw[date(2027, 3, 18)][0] == 5


def test_seed_file_is_valid():
    seed, rules = ev.load_seed()
    assert len(seed) >= 50 and len(rules) >= 10
    assert all(e.end >= e.start and 1 <= e.weight <= 5 for e in seed)
    cos = next(e for e in seed if e.name.startswith("Cosmoprof"))
    assert (cos.start, cos.end, cos.weight) == (date(2027, 3, 18), date(2027, 3, 21), 5)
