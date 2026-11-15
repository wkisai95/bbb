"""Lettura delle risposte di ricerca: la fixture è una risposta vera (ridotta ai campi usati)."""
import json
import os

from bnb.airbnb import parse_content, parse_money, parse_page, parse_price, parse_rating

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "search_page.json")


def test_real_page():
    page = parse_page(json.load(open(FIX, encoding="utf-8")), 2)
    assert len(page.cards) == 40
    assert page.count == 248
    assert len(page.cursors) == 7
    assert all(c.price and c.nights == 2 for c in page.cards)
    first = page.cards[0]
    assert (first.id, first.bedrooms, first.beds, first.baths) == (17263358, 2, 6, 1.0)
    assert first.price == 187.0 and first.rating == 4.77 and first.reviews == 190 and first.superhost
    assert 44.4 < first.lat < 44.6 and 11.2 < first.lon < 11.45
    discounted = next(c for c in page.cards if c.id == 53649558)
    assert discounted.price == 193.0 and discounted.orig == 231.0
    component_style = next(c for c in page.cards if c.id == 2280311)      # riga prezzo "a componenti"
    assert component_style.price == 196.0 and component_style.orig == 217.0


def test_money():
    assert parse_money("€1,172") == 1172.0
    assert parse_money("€186.22") == 186.22
    assert parse_money("1.172 €") == 1172.0
    assert parse_money("1.172,50 €") == 1172.5
    assert parse_money("") is None and parse_money(None) is None


def test_content_and_rating():
    assert parse_content(["Studio", "1 bed", "1 bath"]) == (0, 1, 1.0)
    assert parse_content(["3 bedrooms", "3 double beds", "2.5 baths"]) == (3, 3, 2.5)
    assert parse_content(["1 double bed", "1 sofa bed", "Shared half-bath"]) == (None, 2, 0.5)
    assert parse_content(["2 rooms available"]) == (None, None, None)
    assert parse_rating("4.77 (190)") == (4.77, 190)
    assert parse_rating("5.0 (1,232)") == (5.0, 1232)
    assert parse_rating("New") == (None, 0)


def test_price_variants():
    assert parse_price({"primaryLine": {"price": "€95", "qualifier": "night"}}, 2) == (190.0, None, 2)
    assert parse_price({"primaryLine": {"price": "€2,100", "qualifier": "month"}}, 2) == (None, None, None)
    assert parse_price({"primaryLine": {"accessibilityLabel": "€1,196 for 3 nights, originally €1,417"}}, 3) == (1196.0, 1417.0, 3)
    assert parse_price({"primaryLine": {"discountedPrice": "€215", "originalPrice": "€250", "qualifier": "for 2 nights"}}, 2) == (215.0, 250.0, 2)
    assert parse_price(None, 2) == (None, None, None)
