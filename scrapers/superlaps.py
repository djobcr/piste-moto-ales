"""SuperLaps — page /lgs-events/calendrier + fiches /show.

Page calendrier : cards listant chaque journée avec date début (.day-journees,
.month-journees) et URL `/lgs-events/journee/{slug}/show`.

Fiche /show : contient le tarif "À partir de X €" sous `<h3 class="price">`.
Pour le nombre de places restantes par niveau (Débutant/Moyen/Pilote), le HTML
server-rendered ne contient que le label "places dispo" sans nombre — donc on
laisse `available=True` tant que la card est listée. Pour avoir les compteurs
exacts il faudra Playwright ou trouver un endpoint AJAX (à explorer plus tard).
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from db import Event, Level
from scrapers._common import (
    HTTP_TIMEOUT,
    USER_AGENT,
    circuit_display_for_slug,
    clean_text,
    euros_to_cents,
    normalize_circuit_name,
    normalize_level,
    parse_french_date,
)

ORGANIZER = "SuperLaps"
BASE_URL = "https://superlaps.fr"
LIST_URL = f"{BASE_URL}/lgs-events/calendrier"

_RE_YEAR = re.compile(r"\b(20\d{2})\b")


def fetch() -> list[Event]:
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers, follow_redirects=True) as client:
        resp = client.get(LIST_URL)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        events: list[Event] = []
        for link in soup.select("a.no-link-style"):
            if not link.find("div", class_="card-journees"):
                continue
            ev = _link_to_event(link, client)
            if ev is not None:
                events.append(ev)
    return events


def _link_to_event(link, client: httpx.Client) -> Event | None:
    href = link.get("href") or ""
    if "/lgs-events/journee/" not in href:
        return None

    title_el = link.find("div", class_="card-journees-title")
    if title_el is None:
        return None
    title = clean_text(title_el.get_text(" "))

    # Identification du circuit via title (ex: "Alès (Vendredi et/ou Samedi)" ou "Le Luc (Dimanche)")
    # On extrait la partie avant la parenthèse
    circuit_part = title.split("(")[0].strip()
    circuit_slug = normalize_circuit_name(circuit_part)
    if circuit_slug is None:
        return None
    circuit_display = circuit_display_for_slug(circuit_slug)

    show_url = urljoin(BASE_URL + "/", href)
    booking_url = _show_to_inscription_url(show_url)

    parsed = _parse_card_date(link, fallback_text=title)
    if parsed is None:
        return None

    source_id = href.rstrip("/").split("/")[-2] if href.endswith("/show") else href

    price_cents, levels = _fetch_show_details(client, show_url)

    return Event(
        organizer=ORGANIZER,
        source_id=source_id,
        circuit=circuit_display,
        date=parsed.isoformat(),
        title=title,
        price_cents=price_cents,
        currency="EUR",
        available=True,
        booking_url=booking_url,
        levels=levels,
        raw_data={"show_url": show_url, "circuit_slug": circuit_slug},
    )


def _fetch_show_details(client: httpx.Client, show_url: str) -> tuple[int | None, list[Level]]:
    """Récupère tarif + niveaux proposés depuis la fiche détail /show.

    Niveaux : SuperLaps affiche `<div class="miniPlaceNiveau">Débutant</div>` etc.
    Pas de nombre de places (label "places dispo" sans chiffre côté serveur).
    """
    try:
        resp = client.get(show_url)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None, []

    soup = BeautifulSoup(resp.text, "html.parser")

    price_cents: int | None = None
    for h3 in soup.select("h3.price"):
        text = clean_text(h3.get_text(" "))
        if any(c.isdigit() for c in text):
            price_cents = euros_to_cents(text)
            break

    seen: set[str] = set()
    levels: list[Level] = []
    for el in soup.select(".miniPlaceNiveau"):
        raw = clean_text(el.get_text(" "))
        if not raw:
            continue
        canon = normalize_level(raw)
        if canon in seen or canon == "autre":
            continue
        seen.add(canon)
        levels.append(Level(raw=raw, canonical=canon))

    return price_cents, levels


def _parse_card_date(link, fallback_text: str = "") -> "object | None":
    """Reconstruit la date de DÉBUT depuis .day-journees + .month-journees + année du texte.

    Évite l'écueil de parse_french_date() sur les ranges "du 08 au 09 mai 2026"
    qui renverrait la date de fin (09 mai). On prend le jour exposé dans
    `.day-journees` qui est la date de début.
    """
    from datetime import date as _date

    day_el = link.find("div", class_="day-journees")
    month_el = link.find("div", class_="month-journees")
    if day_el is None or month_el is None:
        return parse_french_date(fallback_text or link.get_text(" "))

    day_txt = clean_text(day_el.get_text())
    month_txt = clean_text(month_el.get_text())
    full_text = clean_text(link.get_text(" "))

    year_match = _RE_YEAR.search(full_text)
    if not (day_txt.isdigit() and year_match):
        return parse_french_date(full_text)

    # On reconstruit "JJ <mois> AAAA" et on délègue au parser FR pour mapper le mois.
    return parse_french_date(f"{int(day_txt)} {month_txt} {year_match.group(1)}")


def _show_to_inscription_url(show_url: str) -> str:
    """`/lgs-events/journee/SLUG/show` → `/lgs-events/journee/inscription/SLUG`.

    Pattern observé sur le bouton 'Inscriptions' dans la fiche détail.
    """
    if "/lgs-events/journee/" in show_url and show_url.endswith("/show"):
        slug = show_url.rsplit("/", 2)[-2]
        return f"{BASE_URL}/lgs-events/journee/inscription/{slug}"
    return show_url
