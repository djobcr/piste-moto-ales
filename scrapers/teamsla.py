"""Team SLA — site WooCommerce multi-circuit.

L'API WC Store /wp-json/wc/store/v1/products expose tous les produits (74),
on filtre ceux dont le slug commence par `circuit-ales-` pour ne garder que
les sorties Alès. Les noms contiennent du HTML (`<br>`) qu'on nettoie via
`clean_text`.

Niveaux : attribut `GROUPE SOUHAITE` (Débutant / Intermédiaire / Confirmé /
Pilote, +variantes "AVEC COACHING"). Pour chaque variation on fetch
`/products/{id}` qui expose `is_in_stock` ET `low_stock_remaining` quand le
seuil de "stock bas" est atteint — on a donc parfois un nombre exact.
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

ORGANIZER = "Team SLA"
CIRCUIT = "Alès"
API_URL = "https://www.team-sla.fr/wp-json/wc/store/v1/products"
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

        # Pour chaque event Alès, on reste dans le même client pour fetch les variations
        for p in products:
            ev = _product_to_event(p, today=today, client=client)
            if ev is not None:
                events.append(ev)
    return events


def _product_to_event(p: dict, *, today: date, client: httpx.Client) -> Event | None:
    slug = (p.get("slug") or "").lower()
    if not slug.startswith("circuit-ales-"):
        return None

    name = clean_text(p.get("name") or "")
    if not name:
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
        },
    )


def _extract_levels(p: dict, client: httpx.Client) -> list[Level]:
    """Extrait les niveaux depuis l'attribut 'GROUPE SOUHAITE' avec stock par variation.

    Plusieurs variations partagent le même canonical (Débutant / Débutant +
    coaching → debutant). Agrégation :
      - is_in_stock : OR (au moins une variation dispo)
      - remaining : somme des `low_stock_remaining` connus, sinon None
        (None = soit l'info n'existe pas, soit il y a beaucoup de places)
    """
    groupe_attr_name: str | None = None
    for attr in (p.get("attributes") or []):
        if "groupe" in (attr.get("name") or "").lower():
            groupe_attr_name = attr.get("name")
            break
    if groupe_attr_name is None:
        return []

    terms_by_slug: dict[str, str] = {}
    for attr in (p.get("attributes") or []):
        if attr.get("name") == groupe_attr_name:
            for t in (attr.get("terms") or []):
                slug = (t.get("slug") or "")
                name = (t.get("name") or "").strip()
                if slug and name:
                    terms_by_slug[slug] = name

    # canonical → {raw, remaining_sum, all_have_low_stock, in_stock_any}
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

        raw_full = terms_by_slug.get(slug_value, slug_value)
        # "DEBUTANT AVEC COACHING" → "DEBUTANT" (on garde le canonical de base)
        base = raw_full.upper().replace(" AVEC COACHING", "").strip()
        # Re-formate proprement : "Débutant" / "Intermédiaire" / etc.
        base_pretty = base.capitalize()
        canon = normalize_level(base)
        if canon == "autre":
            continue

        in_stock, low_remaining = _fetch_variation_stock(client, vid)

        if canon not in aggregated:
            aggregated[canon] = {
                "raw": base_pretty,
                "remaining_sum": low_remaining,
                "all_have_low_stock": (low_remaining is not None),
                "in_stock_any": in_stock,
            }
        else:
            entry = aggregated[canon]
            if low_remaining is not None and entry["all_have_low_stock"]:
                entry["remaining_sum"] = (entry["remaining_sum"] or 0) + low_remaining
            else:
                # Au moins une variation n'a pas de low_stock_remaining
                # (soit elle est complète, soit elle a beaucoup de places)
                # → on perd l'exactitude du compteur
                entry["all_have_low_stock"] = False
                entry["remaining_sum"] = None
            if in_stock is True:
                entry["in_stock_any"] = True
            elif in_stock is False and entry["in_stock_any"] is None:
                entry["in_stock_any"] = False

    levels: list[Level] = []
    for canon, info in aggregated.items():
        levels.append(Level(
            raw=info["raw"],
            canonical=canon,
            remaining=info["remaining_sum"] if info["all_have_low_stock"] else None,
            is_in_stock=info["in_stock_any"],
        ))
    return levels


def _fetch_variation_stock(client: httpx.Client, variation_id: int) -> tuple[bool | None, int | None]:
    """Renvoie (is_in_stock, low_stock_remaining) pour une variation Team SLA.

    `low_stock_remaining` n'est défini que quand le seuil de stock bas est
    atteint — sinon None (= "il y a des places, sans précision").
    """
    try:
        r = client.get(f"https://www.team-sla.fr/wp-json/wc/store/v1/products/{variation_id}")
        if r.status_code != 200:
            return None, None
        data = r.json()
        in_stock = data.get("is_in_stock") if isinstance(data.get("is_in_stock"), bool) else None
        low = data.get("low_stock_remaining") if isinstance(data.get("low_stock_remaining"), int) else None
        return in_stock, low
    except (httpx.HTTPError, ValueError):
        return None, None
