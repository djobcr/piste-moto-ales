"""MotoClub DDE 34 — site Joomla / HikaShop, page /reserver-une-journee.

Stratégie en 2 passes :

1. La page liste expose des cards `<div class="column_journee">` avec date dans
   un `<h4>` et une jauge visuelle de places (largeur en pixels, max 40px).
2. Pour chaque card, on fetch la fiche détail `/detail-journee-du-JJ-MM-YYYY`
   qui expose des microdata schema.org plus fiables :
     - `<meta itemprop="price" content="110" />`
     - `<meta itemprop="availability" content="https://schema.org/InStock" />`

Le signal "availability" est plus fiable que la jauge homepage : on a observé
des journées avec jauge à 7px (rouge, "réservez rapidement") qui sont en fait
"OutOfstock" côté shop. La fiche détail a la vérité.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from db import Event
from scrapers._common import (
    HTTP_TIMEOUT,
    USER_AGENT,
    clean_text,
    euros_to_cents,
    parse_french_date,
)

ORGANIZER = "MotoClub DDE 34"
CIRCUIT = "Alès"
BASE_URL = "https://www.motoclubdde34.fr"
LIST_URL = f"{BASE_URL}/reserver-une-journee"
# Le serveur Joomla peut être lent — observé jusqu'à 30s. On laisse de la marge.
SCRAPER_TIMEOUT = 60.0

# La jauge a 3 zones : 5px rouge + 7px orange + 28px vert = 40px max.
_GAUGE_MAX_PX = 40
_RE_GAUGE_WIDTH = re.compile(r"width\s*:\s*(\d+)\s*px", re.IGNORECASE)


def fetch() -> list[Event]:
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=SCRAPER_TIMEOUT, headers=headers, follow_redirects=True) as client:
        resp = client.get(LIST_URL)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        events: list[Event] = []
        for card in soup.select("div.column_journee"):
            ev = _card_to_event(card, client)
            if ev is not None:
                events.append(ev)
    return events


def _card_to_event(card, client: httpx.Client) -> Event | None:
    h4 = card.find("h4")
    if h4 is None:
        return None
    title = clean_text(h4.get_text(" "))
    if not title:
        return None

    parsed = parse_french_date(title)
    if parsed is None:
        return None

    # Jauge places : signal de fallback (homepage)
    gauge_px = _extract_gauge_pixels(card)

    # URL détail
    booking_url = ""
    link = card.find("a", href=True)
    if link is not None:
        booking_url = urljoin(BASE_URL + "/", link["href"])

    # Fiche détail : prix + disponibilité fiables via microdata schema.org
    detail = _fetch_detail(client, booking_url) if booking_url else {}
    price_cents = detail.get("price_cents")
    availability = detail.get("availability")  # schema.org URL or None

    if availability is not None:
        available = "instock" in availability.lower()
    else:
        # Fallback à la jauge si la fiche détail n'expose pas la microdata
        available = gauge_px is None or gauge_px > 0

    return Event(
        organizer=ORGANIZER,
        source_id=parsed.isoformat(),  # 1 sortie par jour
        circuit=CIRCUIT,
        date=parsed.isoformat(),
        title=title,
        price_cents=price_cents,
        currency="EUR",
        available=available,
        booking_url=booking_url or LIST_URL,
        raw_data={
            "gauge_px": gauge_px,
            "gauge_max_px": _GAUGE_MAX_PX,
            "availability_schema": availability,
            "circuit_slug": "ales",  # DDE 34 ne fait que des journées Alès
        },
    )


def _fetch_detail(client: httpx.Client, url: str) -> dict:
    """Récupère prix + statut depuis la fiche détail HikaShop. Renvoie {} si KO."""
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except httpx.HTTPError:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    out: dict = {}

    price_meta = soup.find("meta", attrs={"itemprop": "price"})
    if price_meta is not None:
        out["price_cents"] = euros_to_cents(price_meta.get("content"))

    avail_meta = soup.find("meta", attrs={"itemprop": "availability"})
    if avail_meta is not None:
        out["availability"] = avail_meta.get("content")

    return out


def _extract_gauge_pixels(card) -> int | None:
    """Cherche le span de jauge (overflow:hidden + width:Npx) et renvoie N."""
    for span in card.find_all("span", style=True):
        style = span["style"]
        if "overflow" not in style or "hidden" not in style:
            continue
        m = _RE_GAUGE_WIDTH.search(style)
        if m:
            return int(m.group(1))
    return None
