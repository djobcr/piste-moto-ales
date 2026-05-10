"""ActivBike — école de pilotage, page calendrier par circuit.

URL : `https://www.activbike.net/circuit/7_ledenon` (le `7` est l'ID circuit,
spécifique à ActivBike). Pour Phase 4 on cible uniquement Lédenon — peut être
étendu à d'autres circuits en ajoutant des entrées à `_CIRCUITS`.

Format HTML : chaque event est un `<p class="calendar-item-list ... [inactive]
{circuit_slug}">`. La classe `inactive` indique un event PASSÉ — sans cette
classe, c'est un event futur réservable. La date est dans `<b>` sans l'année,
qu'on déduit (year courant ou +1 si la date sans année est déjà passée).
Le prix est dans `<span class="hl">PRIX<sup>€</sup></span>`.
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
    circuit_display_for_slug,
    clean_text,
    euros_to_cents,
    parse_french_date,
)

ORGANIZER = "ActivBike"
BASE_URL = "https://www.activbike.net"

# Mapping ActivBike circuit ID → slug canonique de circuits.json
# (IDs spécifiques à ActivBike, on les a connus via l'URL fournie par le user)
_CIRCUITS = [
    {"activbike_id": 7, "url_slug": "L%C3%A9denon", "circuit_slug": "ledenon"},
]


def fetch(today: date | None = None) -> list[Event]:
    if today is None:
        today = date.today()

    headers = {"User-Agent": USER_AGENT}
    events: list[Event] = []
    with httpx.Client(timeout=HTTP_TIMEOUT, headers=headers, follow_redirects=True) as client:
        for cfg in _CIRCUITS:
            url = f"{BASE_URL}/circuit/{cfg['activbike_id']}_{cfg['url_slug'].lower()}"
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            events.extend(_parse_calendar(soup, cfg, today=today))
    return events


def _parse_calendar(soup: BeautifulSoup, cfg: dict, *, today: date) -> list[Event]:
    out: list[Event] = []
    for p in soup.select("p.calendar-item-list"):
        classes = p.get("class") or []
        if "inactive" in classes:
            continue  # event passé

        # Date dans <b>
        b = p.find("b")
        if b is None:
            continue
        date_text = clean_text(b.get_text(" "))  # "Samedi 14 mars"

        parsed = _parse_partial_date(date_text, today)
        if parsed is None or parsed < today:
            continue

        # Texte hors balise <b> = "Lédenon  1 jour" ou "Lédenon Pack 2 jours"
        full_text = clean_text(p.get_text(" "))
        # Retire la portion date (Samedi 14 mars) et le prix
        body_text = full_text.replace(date_text, "", 1).strip()

        # Prix
        price_el = p.find("span", class_="hl")
        price_cents = None
        if price_el is not None:
            # ex: "185€" — on strip le €
            txt = clean_text(price_el.get_text(" ")).replace("€", "").strip()
            price_cents = euros_to_cents(txt)

        # ID + URL via onclick="ouvreNouveauTab('/services-circuit/7_Lédenon/1343')"
        a_tag = p.find("a", attrs={"onclick": True})
        booking_url = ""
        event_id = ""
        if a_tag:
            m = re.search(r"ouvreNouveauTab\('([^']+)'\)", a_tag.get("onclick", ""))
            if m:
                path = m.group(1)
                booking_url = BASE_URL + path
                # Last segment = numeric id
                event_id = path.rstrip("/").split("/")[-1]

        circuit_display = circuit_display_for_slug(cfg["circuit_slug"])
        title = f"{circuit_display} — {body_text}".strip(" —") if body_text else f"{circuit_display} {date_text}"

        out.append(Event(
            organizer=ORGANIZER,
            source_id=event_id or f"activbike:{cfg['circuit_slug']}:{parsed.isoformat()}",
            circuit=circuit_display,
            date=parsed.isoformat(),
            title=title,
            price_cents=price_cents,
            currency="EUR",
            available=True,  # ActivBike montre les events réservables; passé = `inactive` déjà filtré
            booking_url=booking_url or f"{BASE_URL}/circuit/{cfg['activbike_id']}_{cfg['url_slug'].lower()}",
            raw_data={
                "activbike_id": event_id,
                "type_label": body_text,
                "circuit_slug": cfg["circuit_slug"],
            },
        ))
    return out


def _parse_partial_date(text: str, today: date):
    """ActivBike donne "Samedi 14 mars" sans année. On déduit l'année.

    Stratégie : on tente avec l'année courante. Si la date résultante est dans
    le passé (>30 jours en arrière), on ajoute 1 an.
    """
    if not text:
        return None
    # Ajoute year courant + tente parse
    for year_offset in (0, 1):
        candidate_year = today.year + year_offset
        d = parse_french_date(f"{text} {candidate_year}")
        if d is None:
            continue
        # On accepte si la date est >= today, ou bien <30j passés (tolerance)
        if d >= today:
            return d
        # Sinon on continue avec year+1
    return None
