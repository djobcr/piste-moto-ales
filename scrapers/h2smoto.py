"""H2S Moto — école de pilotage multi-circuit, site WooCommerce.

API publique : https://h2smoto.com/wp-json/wc/store/v1/products
Format produit : "Formule [Ultimate|Privilège|Premium|1 jour] – [Circuit] – [Dates] [Année]"
Identification du circuit via la catégorie WC : 'ales', 'ledenon', 'anneau-du-rhin',
'pau-arnos', 'issoire', 'le-vigeant', 'magny-cours', 'nogaro', 'trackdays-le-mans'.

Particularité : 1 event = jusqu'à 4 products distincts (un par formule). On les
garde tous tels quels — le user voit le nom de la formule dans le titre.
"""
from __future__ import annotations

from datetime import date

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

ORGANIZER = "H2S Moto"
API_URL = "https://h2smoto.com/wp-json/wc/store/v1/products"
PER_PAGE = 100

# Catégories à exclure (non-trackday)
_EXCLUDE_CATS = {
    "bons-cadeaux", "bapteme", "stage-eco", "pneumatique",
    "frais-et-accessoires", "bagagerie", "vetement",
}


def fetch(today: date | None = None) -> list[Event]:
    if today is None:
        today = date.today()

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    events: list[Event] = []
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers) as client:
        page = 1
        while True:
            resp = client.get(API_URL, params={"per_page": PER_PAGE, "page": page})
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for p in batch:
                ev = _product_to_event(p, today=today)
                if ev is not None:
                    events.append(ev)
            if len(batch) < PER_PAGE:
                break
            page += 1
    return events


def _product_to_event(p: dict, *, today: date) -> Event | None:
    cats_slugs = [(c.get("slug") or "").lower() for c in (p.get("categories") or [])]
    if any(c in _EXCLUDE_CATS for c in cats_slugs):
        return None

    # Identifier le circuit via les catégories (chacune testée contre normalize_circuit_name)
    circuit_slug = None
    for cat in cats_slugs:
        canon = normalize_circuit_name(cat) or normalize_circuit_name(cat.replace("-", " "))
        if canon:
            circuit_slug = canon
            break
    if circuit_slug is None:
        return None  # Pas un trackday sur un circuit reconnu

    name = clean_text(p.get("name") or "")
    if not name:
        return None

    parsed = parse_french_date(name)
    if parsed is None or parsed < today:
        return None

    prices = p.get("prices") or {}
    price_cents = wc_price_to_cents(prices.get("price"), prices.get("currency_minor_unit"))

    circuit_display = circuit_display_for_slug(circuit_slug)

    # Stock : H2S expose `is_in_stock` au niveau du product directement,
    # parfois `low_stock_remaining` quand le seuil est atteint
    in_stock = bool(p.get("is_in_stock", True))
    low_stock = p.get("low_stock_remaining") if isinstance(p.get("low_stock_remaining"), int) else None

    return Event(
        organizer=ORGANIZER,
        source_id=str(p["id"]),
        circuit=circuit_display,
        date=parsed.isoformat(),
        title=name,
        price_cents=price_cents,
        currency=prices.get("currency_code") or "EUR",
        available=in_stock,
        booking_url=p.get("permalink") or "",
        raw_data={
            "id": p.get("id"),
            "slug": p.get("slug"),
            "stock_status": p.get("stock_status"),
            "low_stock_remaining": low_stock,
            "categories": cats_slugs,
            "circuit_slug": circuit_slug,
        },
    )
