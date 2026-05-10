"""Accès Piste — page /roulages-moto-sur-circuit, format Drupal.

Chaque sortie Alès est un `<a href="/circuit/ales?roulage={ID}">` qui contient :
- `<div class="roulage-vignette-circuit">Ales</div>` (event simple)
  ou `<div class="roulage-vignette-titre">Week-end Alès 26 et 27 juillet</div>` (pack)
- `<div class="roulage-vignette-date">Vendredi 27 mars 2026</div>` (absent pour les packs)
- `<div class="roulage-vignette-prix">115.00 €</div>`
- `<div class="block-img-epuise"></div>` à l'intérieur si l'event est complet

Pour les packs week-end (pas de `roulage-vignette-date`), on remonte au `<h2>` du
mois courant ("Juillet 2026") via `find_previous("h2")` pour obtenir l'année.
Les noms de mois sont extraits du titre.

Pas de PDF parsing : tout est dans le HTML.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from db import Event
from scrapers._common import (
    HTTP_TIMEOUT,
    USER_AGENT,
    circuit_display_for_slug,
    clean_text,
    euros_to_cents,
    normalize_circuit_name,
    parse_french_date,
)

ORGANIZER = "Accès Piste"
BASE_URL = "https://www.acces-piste.com"
LIST_URL = f"{BASE_URL}/roulages-moto-sur-circuit"

# /circuit/{nom}?roulage={id} — multi-circuit
_RE_ROULAGE_ID = re.compile(r"/circuit/([a-z-]+)\?roulage=(\d+)")
_RE_YEAR = re.compile(r"\b(20\d{2})\b")


def fetch(today: date | None = None) -> list[Event]:
    if today is None:
        today = date.today()

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers, follow_redirects=True) as client:
        resp = client.get(LIST_URL)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

    events: list[Event] = []
    seen_keys: set[str] = set()
    for a in soup.find_all("a", href=_RE_ROULAGE_ID):
        m = _RE_ROULAGE_ID.search(a["href"])
        if not m:
            continue
        circuit_url_slug = m.group(1)  # ex: "ales", "ledenon", "vaison-piste"
        roulage_id = m.group(2)
        key = f"{circuit_url_slug}:{roulage_id}"
        if key in seen_keys:
            continue
        seen_keys.add(key)

        canonical_slug = normalize_circuit_name(circuit_url_slug)
        if canonical_slug is None:
            continue  # Circuit pas encore référencé dans data/circuits.json

        ev = _link_to_event(a, roulage_id, canonical_slug=canonical_slug, today=today)
        if ev is not None:
            events.append(ev)
    return events


def _link_to_event(link, roulage_id: str, *, canonical_slug: str, today: date) -> Event | None:
    date_el = link.find("div", class_="roulage-vignette-date")
    title_el = link.find("div", class_="roulage-vignette-titre")
    circuit_el = link.find("div", class_="roulage-vignette-circuit")
    price_el = link.find("div", class_="roulage-vignette-prix")

    parsed: "date | None"
    if date_el is not None:
        parsed = parse_french_date(clean_text(date_el.get_text(" ")))
    elif title_el is not None:
        parsed = _parse_pack_date(link, clean_text(title_el.get_text(" ")))
    else:
        return None

    if parsed is None or parsed < today:
        return None

    circuit_display = circuit_display_for_slug(canonical_slug)

    if title_el is not None:
        title = clean_text(title_el.get_text(" "))
    elif date_el is not None:
        title = f"Roulage {circuit_display} — {clean_text(date_el.get_text(' '))}"
    else:
        title = f"Roulage {circuit_display} — {parsed.isoformat()}"

    price_cents = None
    if price_el is not None:
        price_cents = euros_to_cents(clean_text(price_el.get_text(" ")))

    available = link.find("div", class_="block-img-epuise") is None

    booking_url = urljoin(BASE_URL + "/", link["href"])

    return Event(
        organizer=ORGANIZER,
        source_id=f"{canonical_slug}:{roulage_id}",
        circuit=circuit_display,
        date=parsed.isoformat(),
        title=title,
        price_cents=price_cents,
        currency="EUR",
        available=available,
        booking_url=booking_url,
        raw_data={
            "roulage_id": roulage_id,
            "is_pack": title_el is not None,
            "circuit_slug": canonical_slug,
        },
    )


def _parse_pack_date(link, title_text: str) -> "date | None":
    """Date d'un pack week-end : on a 'Week-end Alès 26 et 27 juillet'.

    On combine avec le `<h2>` mois précédent qui contient l'année (ex:
    'Juillet 2026'). Approche robuste : on synthétise 'JJ MOIS AAAA' à partir du
    premier jour du titre et de l'année du H2 mois.
    """
    if not title_text:
        return None

    # Premier jour numérique du titre (ex: "26" dans "26 et 27 juillet")
    day_match = re.search(r"\b(\d{1,2})\b", title_text)
    if not day_match:
        return None

    # Mois en lettres dans le titre
    parsed_from_title = parse_french_date(f"{day_match.group(1)} {title_text}")
    if parsed_from_title is not None:
        return parsed_from_title

    # Fallback : fusionner jour du titre + mois/année du H2
    h2 = link.find_previous("h2")
    if h2 is None:
        return None
    h2_text = clean_text(h2.get_text(" "))
    year_match = _RE_YEAR.search(h2_text)
    if not year_match:
        return None
    return parse_french_date(f"{day_match.group(1)} {h2_text} {year_match.group(1)}")
