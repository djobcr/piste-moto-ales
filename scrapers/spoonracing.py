"""Spoon Racing — organisateur dédié au circuit d'Alès.

API WooCommerce Store, mais avec `currency_minor_unit=0` (price "140" = 140€)
contrairement à la plupart des shops WP. Le helper `wc_price_to_cents` gère
cette particularité.

Le shop contient aussi quelques produits non-trackday (baptêmes, adhésion,
karting), donc on filtre par préfixe nom "Alès" / "Ales".
"""
from __future__ import annotations

from datetime import date

import httpx

from db import Event, Level
from scrapers._common import (
    HTTP_TIMEOUT,
    USER_AGENT,
    clean_text,
    normalize_level,
    parse_french_date,
    wc_price_to_cents,
)

ORGANIZER = "Spoon Racing"
CIRCUIT = "Alès"
API_URL = "https://www.spoonracing.fr/wp-json/wc/store/v1/products"
PER_PAGE = 100


def fetch(today: date | None = None) -> list[Event]:
    if today is None:
        today = date.today()

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    events: list[Event] = []
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers) as client:
        # Pagination
        products: list[dict] = []
        page = 1
        while True:
            resp = client.get(API_URL, params={"per_page": PER_PAGE, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            products.extend(batch)
            if len(batch) < PER_PAGE:
                break
            page += 1

        # On reste dans le client httpx pour les fetches de variations
        for p in products:
            ev = _product_to_event(p, today=today, client=client)
            if ev is not None:
                events.append(ev)
    return events


def _is_ales_product(name: str) -> bool:
    n = name.strip().lower()
    return n.startswith("alès") or n.startswith("ales")


def _product_to_event(p: dict, *, today: date, client: httpx.Client) -> Event | None:
    name = clean_text(p.get("name") or "")
    if not _is_ales_product(name):
        return None

    parsed = parse_french_date(name)
    if parsed is None or parsed < today:
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
            "circuit_slug": "ales",  # Spoon Racing roule 100% à Alès
        },
    )


def _extract_levels(p: dict, client: httpx.Client) -> list[Level]:
    """Extrait les niveaux Spoon Racing (Débutant/Moyen/Pilote) avec stock binaire.

    Plusieurs variations partagent le même niveau (sans coaching / avec coaching).
    On agrège : un niveau est "in_stock" si AU MOINS UNE de ses variations a
    `is_in_stock=True`. On utilise la liste `variations` du produit pour
    récupérer les variation_ids puis on fetch chacune.
    """
    # Trouver l'attribut "Groupe"
    groupe_attr_name: str | None = None
    for attr in (p.get("attributes") or []):
        if "groupe" in (attr.get("name") or "").lower():
            groupe_attr_name = attr.get("name")
            break
    if groupe_attr_name is None:
        return []

    # Map slug → raw label (depuis les terms)
    terms_by_slug: dict[str, str] = {}
    for attr in (p.get("attributes") or []):
        if attr.get("name") == groupe_attr_name:
            for t in (attr.get("terms") or []):
                slug = (t.get("slug") or "").lower()
                name = (t.get("name") or "").strip()
                if slug and name:
                    terms_by_slug[slug] = name

    # Pour chaque variation, on fetch + on agrège par canonical
    # Format: canonical → {"raw": str, "is_in_stock": True/False/None}
    aggregated: dict[str, dict] = {}

    for v in (p.get("variations") or []):
        vid = v.get("id")
        if not isinstance(vid, int):
            continue

        slug_value: str | None = None
        for a in (v.get("attributes") or []):
            if a.get("name") == groupe_attr_name and a.get("value"):
                slug_value = a["value"]
                break
        if slug_value is None:
            continue

        raw_full = terms_by_slug.get(slug_value, slug_value.replace("-", " ").capitalize())
        # "Débutant + coaching 3 sessions (+140€)" → "Débutant"
        base = raw_full.split(" + ")[0].split(" (")[0].strip()
        canon = normalize_level(base)
        if canon == "autre":
            continue

        in_stock = _fetch_variation_stock(client, vid)

        if canon not in aggregated:
            aggregated[canon] = {"raw": base, "is_in_stock": in_stock}
        else:
            # Agrégation : si AU MOINS une variation est in_stock, le niveau l'est
            existing = aggregated[canon]["is_in_stock"]
            if in_stock is True:
                aggregated[canon]["is_in_stock"] = True
            elif in_stock is False and existing is None:
                aggregated[canon]["is_in_stock"] = False

    return [
        Level(raw=info["raw"], canonical=canon, is_in_stock=info["is_in_stock"])
        for canon, info in aggregated.items()
    ]


def _fetch_variation_stock(client: httpx.Client, variation_id: int) -> bool | None:
    """`is_in_stock` pour une variation Spoon Racing, ou None si requête KO."""
    try:
        r = client.get(f"https://www.spoonracing.fr/wp-json/wc/store/v1/products/{variation_id}")
        if r.status_code != 200:
            return None
        data = r.json()
        val = data.get("is_in_stock")
        return val if isinstance(val, bool) else None
    except (httpx.HTTPError, ValueError):
        return None
