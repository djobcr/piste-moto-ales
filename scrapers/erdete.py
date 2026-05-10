"""Erdete — petit organisateur suisse, 1 sortie/an à Alès.

Le site est un Squarespace tout simple. La date est en texte libre dans la
homepage : "Succès indiscutable de nos roulages à l'Ascension 2025: ... nous
incitent à réorganiser notre sortie les 15 et 16 mai 2026."

Le bouton d'inscription pointe vers un Google Form. Pas d'info de places dispo,
pas de tarif structuré (170 CHF / 320 CHF mentionnés en texte). On stocke en CHF
pour ne pas mentir sur la devise.

Source ID : `erdete:{ISO start date}` — il n'y a qu'un seul event/an.
"""
from __future__ import annotations

import re
from datetime import date

import httpx
from bs4 import BeautifulSoup

from db import Event
from scrapers._common import (
    HTTP_TIMEOUT,
    USER_AGENT,
    clean_text,
    parse_french_date,
)

ORGANIZER = "Erdete"
CIRCUIT = "Alès"
HOMEPAGE = "https://www.erdete.ch/"

_RE_FORMS_GLE = re.compile(r"https?://forms\.gle/[\w-]+")
_RE_DATE_NEAR_ALES = re.compile(
    r"(?P<sentence>[^.]*?\b(?:Alès|Ales)\b[^.]*?\b\d{1,2}\b[^.]*?\d{4}[^.]*?\.)",
    re.IGNORECASE,
)
# Le HTML d'Erdete utilise le format suisse "Un jour 170.-" / "Deux jours 320.-"
# (le `.-` signifie "francs"). On cible "Un jour" pour avoir le tarif 1 journée.
_RE_PRICE_UN_JOUR = re.compile(r"Un\s+jour\s+(\d{2,4})", re.IGNORECASE)
_RE_PRICE_FALLBACK = re.compile(r"(\d{2,4})\s*CHF", re.IGNORECASE)


def fetch(today: date | None = None) -> list[Event]:
    if today is None:
        today = date.today()

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers, follow_redirects=True) as client:
        resp = client.get(HOMEPAGE)
        resp.raise_for_status()
        html_text = resp.text

    soup = BeautifulSoup(html_text, "html.parser")
    full_text = clean_text(soup.get_text(" "))

    parsed_date = _find_event_date(full_text)
    if parsed_date is None or parsed_date < today:
        return []

    booking_url = _find_inscription_url(soup) or HOMEPAGE
    price_chf = _find_price_chf(full_text)

    title = f"Erdete — Roulage Alès {parsed_date.year}"

    return [
        Event(
            organizer=ORGANIZER,
            source_id=f"erdete:{parsed_date.isoformat()}",
            circuit=CIRCUIT,
            date=parsed_date.isoformat(),
            title=title,
            price_cents=price_chf * 100 if price_chf is not None else None,
            currency="CHF",
            available=True,
            booking_url=booking_url,
            raw_data={
                "matched_text_excerpt": _excerpt_around_date(full_text),
                "circuit_slug": "ales",  # Erdete fait 1 sortie/an à Alès
            },
        )
    ]


def _find_event_date(text: str) -> "date | None":
    """Cherche une date FR dans une phrase qui mentionne Alès."""
    if not text:
        return None
    m = _RE_DATE_NEAR_ALES.search(text)
    if m:
        d = parse_french_date(m.group("sentence"))
        if d is not None:
            return d
    return parse_french_date(text)


def _find_inscription_url(soup: BeautifulSoup) -> str | None:
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if _RE_FORMS_GLE.match(href):
            return href
    return None


def _find_price_chf(text: str) -> int | None:
    """Renvoie le prix CHF pour 1 jour de roulage, ou None."""
    m = _RE_PRICE_UN_JOUR.search(text)
    if m:
        return int(m.group(1))
    matches = [int(m.group(1)) for m in _RE_PRICE_FALLBACK.finditer(text)]
    return min(matches) if matches else None


def _excerpt_around_date(text: str, window: int = 120) -> str:
    m = _RE_DATE_NEAR_ALES.search(text)
    if m is None:
        return ""
    return clean_text(m.group("sentence"))[:window]
