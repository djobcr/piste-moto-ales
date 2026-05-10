"""Pôle Mécanique MC — moto club du circuit, vend aussi des trackdays.

Source : API WooCommerce Store v1 (publique, JSON). Tous les produits du shop
sont des journées de roulage Alès, donc pas de filtre.

Niveaux : extraits via attribut 'Catégorie' (Débutant/Initié/Confirmé/Expert).
Stock par niveau : on fetch chaque variation individuellement (`/products/{id}`)
qui renvoie `is_in_stock` — signal binaire qui permet de marquer un niveau
"complet" et l'exclure du filtre bookable côté UI.
"""
from __future__ import annotations

import httpx

from db import Event, Level
from scrapers._common import (
    HTTP_TIMEOUT,
    USER_AGENT,
    normalize_level,
    parse_french_date,
    wc_price_to_cents,
)

ORGANIZER = "PMMC"
CIRCUIT = "Alès"
API_URL = "https://polemecanique-mc.com/wp-json/wc/store/v1/products"


def fetch() -> list[Event]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers) as client:
        resp = client.get(API_URL, params={"per_page": 100})
        resp.raise_for_status()
        products = resp.json()

        events: list[Event] = []
        for p in products:
            ev = _product_to_event(p, client)
            if ev is not None:
                events.append(ev)
    return events


def _product_to_event(p: dict, client: httpx.Client) -> Event | None:
    name = p.get("name") or ""
    parsed = parse_french_date(name)
    if parsed is None:
        return None

    prices = p.get("prices") or {}
    price_cents = wc_price_to_cents(prices.get("price"), prices.get("currency_minor_unit"))

    return Event(
        organizer=ORGANIZER,
        source_id=str(p["id"]),
        circuit=CIRCUIT,
        date=parsed.isoformat(),
        title=name,
        price_cents=price_cents,
        currency=prices.get("currency_code") or "EUR",
        available=bool(p.get("is_in_stock", True)),
        booking_url=p.get("permalink") or "",
        levels=_extract_levels(p, client),
        raw_data={
            "id": p.get("id"),
            "slug": p.get("slug"),
            "stock_status": p.get("stock_status"),
            "categories": [c.get("slug") for c in (p.get("categories") or [])],
            "circuit_slug": "ales",  # PMMC = moto-club résident d'Alès
        },
    )


def _extract_levels(p: dict, client: httpx.Client) -> list[Level]:
    """Pour chaque variation Catégorie, fetch /products/{variation_id} pour
    son `is_in_stock`. Déduplique sur le canonical (rare, mais garde-fou)."""

    # 1) Trouver l'attribut 'Catégorie' et map variation_id → niveau raw
    categorie_attr_name: str | None = None
    for attr in (p.get("attributes") or []):
        if "categorie" in (attr.get("name") or "").lower() or "catégorie" in (attr.get("name") or "").lower():
            categorie_attr_name = attr.get("name")
            break
    if categorie_attr_name is None:
        return []

    # 2) Map: variation_id → "debutant"/"initie"/etc (la value WC est en slug)
    variations = p.get("variations") or []
    variation_to_label: dict[int, str] = {}
    for v in variations:
        vid = v.get("id")
        if not isinstance(vid, int):
            continue
        for a in (v.get("attributes") or []):
            if a.get("name") == categorie_attr_name and a.get("value"):
                variation_to_label[vid] = a["value"]
                break

    # 3) Récupérer les terms (raw labels — "Débutant", "Initié"…) pour mapper slug → label
    terms_by_slug: dict[str, str] = {}
    for attr in (p.get("attributes") or []):
        if attr.get("name") == categorie_attr_name:
            for t in (attr.get("terms") or []):
                slug = (t.get("slug") or "").lower()
                name = (t.get("name") or "").strip()
                if slug and name:
                    terms_by_slug[slug] = name

    # 4) Pour chaque variation, fetch son in_stock
    seen_canonical: set[str] = set()
    levels: list[Level] = []
    for vid, slug in variation_to_label.items():
        raw = terms_by_slug.get(slug, slug.capitalize())
        canon = normalize_level(raw)
        if canon in seen_canonical:
            continue
        seen_canonical.add(canon)
        is_in_stock = _fetch_variation_stock(client, vid)
        levels.append(Level(raw=raw, canonical=canon, is_in_stock=is_in_stock))
    return levels


def _fetch_variation_stock(client: httpx.Client, variation_id: int) -> bool | None:
    """Renvoie True/False pour `is_in_stock` ou None si la requête échoue."""
    try:
        r = client.get(f"https://polemecanique-mc.com/wp-json/wc/store/v1/products/{variation_id}")
        if r.status_code != 200:
            return None
        data = r.json()
        val = data.get("is_in_stock")
        if isinstance(val, bool):
            return val
        return None
    except (httpx.HTTPError, ValueError):
        return None
