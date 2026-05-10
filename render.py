"""Génère le site multi-page depuis la base SQLite.

Architecture :
- `dist/index.html`                    : grille des circuits ayant ≥1 event
- `dist/circuits/{slug}/index.html`    : détail d'un circuit (info + CTA)
- `dist/circuits/{slug}/dates.html`    : calendrier/liste des dates pour 1 circuit

Chaque crawl régénère l'ensemble. Les templates partagent `_base.html` (head,
styles communs, footer) et héritent via `extends`.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).parent
DB_PATH = ROOT / "piste.db"
TEMPLATES_DIR = ROOT / "templates"
CIRCUITS_JSON = ROOT / "data" / "circuits.json"
DIST_DIR = ROOT / "dist"

WEEKDAYS_FR = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
WEEKDAYS_FR_ABBREV = ["Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim."]
MONTHS_FR_LONG = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
MONTHS_FR_SHORT = [
    "", "Jan", "Fév", "Mars", "Avr", "Mai", "Juin",
    "Juil", "Août", "Sept", "Oct", "Nov", "Déc",
]
COUNTRY_FLAGS = {
    "FR": "🇫🇷", "BE": "🇧🇪", "ES": "🇪🇸", "CZ": "🇨🇿",
    "IT": "🇮🇹", "PT": "🇵🇹", "CH": "🇨🇭", "DE": "🇩🇪", "GB": "🇬🇧",
}

_LEVEL_ORDER = {
    "debutant": 1, "intermediaire": 2, "confirme": 3, "expert": 4,
    "open": 5, "side_car": 6, "vip": 7, "autre": 99,
}


@dataclass
class RenderedEvent:
    organizer: str
    title: str
    booking_url: str
    available: bool
    day_num: int
    month_short: str
    weekday_short: str
    weekday_long: str
    price_display: str | None
    seats_label: str | None
    seats_class: str
    search_blob: str
    date_iso: str
    price_cents: int
    has_price: bool
    currency: str
    seats_num: int
    is_weekend: bool
    levels: list[dict]
    canonical_levels_csv: str
    bookable_levels_csv: str
    organizer_logo_url: str
    organizer_initials: str
    organizer_color: str


def render(db_path: Path = DB_PATH) -> dict:
    """Génère toutes les pages du site multi-page. Renvoie un dict de stats."""
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Charge data/circuits.json
    with open(CIRCUITS_JSON, encoding="utf-8") as f:
        circuits_data = json.load(f)
    circuits_by_slug = {c["slug"]: c for c in circuits_data["circuits"]}

    # 2. Charge events_active depuis la DB
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT organizer, source_id, circuit, date, title, price_cents, currency,
                   available, booking_url, levels, raw_data
            FROM events_active
            ORDER BY date, organizer
        """).fetchall()
        count_total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # 3. Group by circuit_slug (depuis raw_data)
    events_by_slug: dict[str, list[sqlite3.Row]] = defaultdict(list)
    organizers_global: set[str] = set()
    images_by_slug: dict[str, str] = {}

    for r in rows:
        raw = json.loads(r["raw_data"] or "{}")
        slug = raw.get("circuit_slug")
        if not slug:
            continue  # event sans circuit identifié, ignoré côté rendu
        events_by_slug[slug].append(r)
        organizers_global.add(r["organizer"])
        # Image du circuit : si on n'en a pas encore et qu'un event en a une, on la garde
        img = raw.get("circuit_image_url")
        if img and slug not in images_by_slug:
            images_by_slug[slug] = img

    count_active = len(rows)
    generated_at = datetime.now().strftime("%d/%m/%Y %H:%M")

    # 4. Build env Jinja
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )

    # 5. Pour chaque circuit avec events, calcule les métadonnées agrégées
    circuit_summaries = []
    for slug, evs in events_by_slug.items():
        meta = circuits_by_slug.get(slug)
        if not meta:
            continue  # circuit dans la DB mais pas dans circuits.json (devrait pas arriver)

        # Min price
        prices_eur = [r["price_cents"] for r in evs
                      if r["price_cents"] is not None and (r["currency"] or "EUR") == "EUR"]
        min_price_cents = min(prices_eur) if prices_eur else None
        min_price_display = _price_display(min_price_cents, "EUR") if min_price_cents else None

        country = meta.get("country", "")
        flag = COUNTRY_FLAGS.get(country, "")

        circuit_summaries.append({
            **meta,
            "event_count": len(evs),
            "min_price_cents": min_price_cents or 0,
            "min_price_display": min_price_display,
            "image_url": images_by_slug.get(slug, ""),
            "flag": flag,
            "hue": _color_hue_for(slug),
            "search_blob": " ".join([
                meta["name"], meta.get("city", ""), meta.get("region", ""),
                country, slug,
            ]).lower(),
        })

    # Tri : par pays, puis par nb events desc, puis par nom
    country_order = ["FR", "BE", "CH", "ES", "IT", "PT", "CZ"]
    circuit_summaries.sort(key=lambda c: (
        country_order.index(c["country"]) if c["country"] in country_order else 999,
        -c["event_count"],
        c["name"],
    ))

    # Compteur par pays (pour les chips de filtre côté client)
    country_counts: dict[str, int] = defaultdict(int)
    for c in circuit_summaries:
        country_counts[c["country"]] += 1
    countries_for_chips = [
        {"code": code, "count": n, "flag": COUNTRY_FLAGS.get(code, "")}
        for code, n in sorted(country_counts.items(), key=lambda x: -x[1])
    ]

    # 6. Render index page
    tpl_index = env.get_template("circuit_index.html")
    (DIST_DIR / "index.html").write_text(
        tpl_index.render(
            circuits=circuit_summaries,
            countries=countries_for_chips,
            count_active=count_active,
            count_total=count_total,
            count_organizers=len(organizers_global),
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )

    # 7. Render circuit_detail + circuit_dates pour chaque circuit
    tpl_detail = env.get_template("circuit_detail.html")
    tpl_dates = env.get_template("circuit_dates.html")

    for c in circuit_summaries:
        slug = c["slug"]
        circuit_dir = DIST_DIR / "circuits" / slug
        circuit_dir.mkdir(parents=True, exist_ok=True)

        # Détail
        (circuit_dir / "index.html").write_text(
            tpl_detail.render(
                circuit=c,
                count_active=count_active,
                count_total=count_total,
                generated_at=generated_at,
            ),
            encoding="utf-8",
        )

        # Dates : convertir les rows en RenderedEvent + grouper par mois
        evs = [_row_to_rendered(r) for r in events_by_slug[slug]]
        events_by_month: dict[str, list[RenderedEvent]] = defaultdict(list)
        organizers_for_circuit: set[str] = set()
        for ev in evs:
            d = date.fromisoformat(ev.date_iso)
            month_key = f"{MONTHS_FR_LONG[d.month].capitalize()} {d.year}"
            events_by_month[month_key].append(ev)
            organizers_for_circuit.add(ev.organizer)

        (circuit_dir / "dates.html").write_text(
            tpl_dates.render(
                circuit=c,
                events=evs,
                events_by_month=events_by_month.items(),
                organizers=sorted(organizers_for_circuit, key=str.lower),
                count_active=count_active,
                count_total=count_total,
                generated_at=generated_at,
            ),
            encoding="utf-8",
        )

    return {
        "circuits": len(circuit_summaries),
        "events": count_active,
        "pages": 1 + 2 * len(circuit_summaries),
    }


# ─────── Helpers ───────

def _row_to_rendered(r: sqlite3.Row) -> RenderedEvent:
    d = date.fromisoformat(r["date"])
    raw = json.loads(r["raw_data"] or "{}")
    levels = json.loads(r["levels"] or "[]")
    levels.sort(key=lambda lv: _LEVEL_ORDER.get(lv.get("canonical", "autre"), 99))

    organizer_logo_url = raw.get("organizer_logo_url") or ""
    organizer_initials = _initials_for(r["organizer"] or "")
    organizer_color = _color_hue_for(r["organizer"] or "")

    seats_label, seats_class, seats_num = _seats_display(r, raw)
    price_display = _price_display(r["price_cents"], r["currency"])

    canonical_set: set[str] = {lv.get("canonical", "autre") for lv in levels}
    if not canonical_set:
        canonical_set = {"open"}
    canonical_csv = ",".join(sorted(canonical_set))

    if levels:
        bookable_set: set[str] = set()
        for lv in levels:
            canon = lv.get("canonical", "autre")
            rem = lv.get("remaining")
            in_stock = lv.get("is_in_stock")
            if rem is not None:
                if rem > 0:
                    bookable_set.add(canon)
            elif in_stock is not None:
                if in_stock:
                    bookable_set.add(canon)
            else:
                bookable_set.add(canon)
    else:
        bookable_set = {"open"}
    bookable_csv = ",".join(sorted(bookable_set))

    search_blob = " ".join([
        r["organizer"] or "", r["title"] or "", r["circuit"] or "",
        r["date"], d.strftime("%d/%m/%Y"), MONTHS_FR_LONG[d.month],
        " ".join(lv.get("raw", "") for lv in levels),
    ]).lower()

    return RenderedEvent(
        organizer=r["organizer"],
        title=r["title"],
        booking_url=r["booking_url"] or "",
        available=bool(r["available"]),
        day_num=d.day,
        month_short=MONTHS_FR_SHORT[d.month],
        weekday_short=WEEKDAYS_FR[d.weekday()],
        weekday_long=WEEKDAYS_FR_ABBREV[d.weekday()],
        price_display=price_display,
        seats_label=seats_label,
        seats_class=seats_class,
        search_blob=search_blob,
        date_iso=r["date"],
        price_cents=int(r["price_cents"]) if r["price_cents"] is not None else 0,
        has_price=r["price_cents"] is not None,
        currency=(r["currency"] or "EUR"),
        seats_num=seats_num,
        is_weekend=d.weekday() >= 5,
        levels=levels,
        canonical_levels_csv=canonical_csv,
        bookable_levels_csv=bookable_csv,
        organizer_logo_url=organizer_logo_url,
        organizer_initials=_initials_for(r["organizer"] or ""),
        organizer_color=organizer_color,
    )


def _seats_display(r: sqlite3.Row, raw: dict) -> tuple[str | None, str, int]:
    if not r["available"]:
        return ("Complet", "out", 0)
    remaining = raw.get("remaining_seats")
    if isinstance(remaining, int):
        if remaining <= 0:
            return ("Complet", "out", 0)
        cls = "low" if remaining < 10 else ""
        return (f"{remaining} place{'s' if remaining > 1 else ''}", cls, remaining)
    return ("Dispo", "", 9999)


def _price_display(price_cents: int | None, currency: str | None) -> str | None:
    if price_cents is None:
        return None
    cur = currency or "EUR"
    symbol = {"EUR": "€", "CHF": "CHF", "USD": "$"}.get(cur, cur)
    amount = price_cents / 100
    if amount == int(amount):
        return f"{int(amount)} {symbol}"
    return f"{amount:.2f} {symbol}"


def _initials_for(name: str) -> str:
    if not name:
        return "?"
    parts = [p for p in name.replace("-", " ").replace("/", " ").split() if p and p[0].isalnum()]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return name[:2].upper()


def _color_hue_for(name: str) -> str:
    """Hash → teinte HSL stable."""
    if not name:
        return "200deg"
    h = 0
    for c in name:
        h = (h * 31 + ord(c)) % 360
    return f"{h}deg"


if __name__ == "__main__":
    stats = render()
    print(f"Rendered {stats['circuits']} circuits, {stats['events']} events, {stats['pages']} pages → {DIST_DIR}")
