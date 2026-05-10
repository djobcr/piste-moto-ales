"""DB Sport (Denis Bouan) — école/structure de pilotage moto.

Source : API WooCommerce Store v1 publique. Renvoie 100+ produits dont seulement
quelques-uns sont des journées Alès. On filtre par préfixe `ALES ` ou `Ales ` dans
le nom (plus robuste que par catégorie : 4 produits Alès n'ont pas la catégorie
`inscription-circuit-ales` mais ont bien le préfixe dans le nom).

On ignore les éditions passées : seul l'avenir nous intéresse côté agrégateur.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

import httpx

from db import Event
from scrapers._common import (
    HTTP_TIMEOUT,
    USER_AGENT,
    circuit_display_for_slug,
    clean_text,
    normalize_circuit_name,
    parse_french_date,
    wc_price_to_cents,
)

ORGANIZER = "DB Sport"
API_URL = "https://denisbouan.fr/wp-json/wc/store/v1/products"
PER_PAGE = 100


def fetch(today: date | None = None) -> list[Event]:
    """Récupère toutes les journées Alès à venir chez DB Sport.

    `today` est injectable pour faciliter les tests; défaut = aujourd'hui.
    """
    if today is None:
        today = date.today()

    products = list(_iter_all_products())

    events: list[Event] = []
    for p in products:
        ev = _product_to_event(p, today=today)
        if ev is not None:
            events.append(ev)
    return events


def _iter_all_products() -> Iterable[dict]:
    """Pagine sur l'API jusqu'à épuisement."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers) as client:
        page = 1
        while True:
            resp = client.get(API_URL, params={"per_page": PER_PAGE, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                return
            yield from batch
            if len(batch) < PER_PAGE:
                return
            page += 1


def _detect_circuit(name: str, slug: str, categories: list) -> str | None:
    """Identifie le circuit d'un product DB Sport.

    Stratégie : (1) le nom lui-même contient parfois 'ALES', 'LEDENON', etc.
    (2) sinon, les slugs catégorie sont du type 'inscription-circuit-ales',
    'inscription-circuit-ledenon'. (3) sinon, le slug du product.
    """
    # Try name first
    s = normalize_circuit_name(name)
    if s:
        return s
    # Try categories
    for c in categories:
        cat_slug = (c.get("slug") or "").lower()
        if "circuit-" in cat_slug:
            # ex: "inscription-circuit-ales" → "ales"
            tail = cat_slug.split("circuit-", 1)[1]
            s = normalize_circuit_name(tail)
            if s:
                return s
        elif "-circuit" in cat_slug:
            head = cat_slug.split("-circuit", 1)[0]
            s = normalize_circuit_name(head)
            if s:
                return s
    # Try slug
    s = normalize_circuit_name(slug)
    return s


def _product_to_event(p: dict, *, today: date) -> Event | None:
    name = clean_text(p.get("name") or "")
    if not name:
        return None

    parsed = parse_french_date(name)
    if parsed is None or parsed < today:
        return None

    circuit_slug = _detect_circuit(name, p.get("slug") or "", p.get("categories") or [])
    if circuit_slug is None:
        # Probablement un produit non-trackday (stage route-circuit, individuel,
        # bon cadeau, pneu). On skippe.
        return None
    circuit_display = circuit_display_for_slug(circuit_slug)

    prices = p.get("prices") or {}
    price_cents = wc_price_to_cents(prices.get("price"), prices.get("currency_minor_unit"))

    return Event(
        organizer=ORGANIZER,
        source_id=str(p["id"]),
        circuit=circuit_display,
        date=parsed.isoformat(),
        title=name,
        price_cents=price_cents,
        currency=prices.get("currency_code") or "EUR",
        available=bool(p.get("is_in_stock", True)),
        booking_url=p.get("permalink") or "",
        raw_data={
            "id": p.get("id"),
            "slug": p.get("slug"),
            "stock_status": p.get("stock_status"),
            "categories": [c.get("slug") for c in (p.get("categories") or [])],
            "circuit_slug": circuit_slug,
        },
    )
