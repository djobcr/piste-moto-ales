"""Helpers partagés entre scrapers — principalement le parsing de dates FR."""
from __future__ import annotations

import html
import json
import re
import unicodedata
from datetime import date
from functools import lru_cache
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (compatible; piste-moto-aggregator/0.1; +https://github.com/local)"
HTTP_TIMEOUT = 30.0

_CIRCUITS_JSON_PATH = Path(__file__).resolve().parent.parent / "data" / "circuits.json"


def _ascii_normalize(s: str | None) -> str:
    """Lower + NFD strip accents + alphanumeric only + compress whitespace.

    Permet de matcher 'Alès', 'ALES', 'circuit-d-ales' sur la même clé 'ales'.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=1)
def _circuits_alias_index() -> dict[str, str]:
    """Construit {alias_normalisé : slug} depuis data/circuits.json (cached)."""
    if not _CIRCUITS_JSON_PATH.exists():
        return {}
    with open(_CIRCUITS_JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    index: dict[str, str] = {}
    for c in data.get("circuits", []):
        slug = c["slug"]
        # Le slug lui-même (avec tirets remplacés par espaces) est un alias par défaut
        index[_ascii_normalize(slug.replace("-", " "))] = slug
        index[_ascii_normalize(c.get("name", ""))] = slug
        for a in c.get("aliases", []):
            key = _ascii_normalize(a)
            if key:
                index[key] = slug
    return index


@lru_cache(maxsize=1)
def _circuits_by_slug() -> dict[str, dict]:
    """{slug: full_circuit_dict_from_json}."""
    if not _CIRCUITS_JSON_PATH.exists():
        return {}
    with open(_CIRCUITS_JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {c["slug"]: c for c in data.get("circuits", [])}


def circuit_display_for_slug(slug: str | None) -> str:
    """Renvoie le nom display ('Alès', 'Spa Francorchamps') d'un slug canonique."""
    if not slug:
        return ""
    c = _circuits_by_slug().get(slug)
    if c:
        return c.get("name", slug)
    return slug.replace("-", " ").title()


@lru_cache(maxsize=1024)
def normalize_circuit_name(raw: str | None) -> str | None:
    """Mappe un nom de circuit libre vers un slug canonique de data/circuits.json.

    Stratégie :
      1. Normalisation ASCII du raw (lowercase, sans accents, alphanum uniquement)
      2. Match exact dans le dict des aliases
      3. Fallback : recherche d'un alias en substring du raw
         (utile pour "circuit de ledenon samedi 28 mai 2026" → ledenon).
         On exige len(alias) >= 4 pour éviter les faux positifs.

    Renvoie le slug (ex: "ales", "ledenon", "spa-francorchamps") ou None si
    aucun match trouvé.
    """
    if not raw:
        return None
    n = _ascii_normalize(raw)
    if not n:
        return None
    idx = _circuits_alias_index()
    if n in idx:
        return idx[n]
    # Substring fallback, alias les plus longs en premier (plus spécifiques)
    for alias in sorted(idx.keys(), key=len, reverse=True):
        if len(alias) >= 4 and alias in n:
            return idx[alias]
    return None


def normalize_level(name: str | None) -> str:
    """Mappe un nom de niveau libre vers un slug canonique.

    Buckets utilisés côté UI : debutant, intermediaire, confirme, expert, open, vip.
    Retombe sur 'autre' si rien ne matche (à filtrer/inspecter manuellement).
    """
    if not name:
        return "autre"
    import unicodedata
    n = unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode("ascii").lower()
    if "debutant" in n or "novice" in n:
        return "debutant"
    if "initie" in n or "moyen" in n or "intermediaire" in n:
        return "intermediaire"
    if "confirme" in n:
        return "confirme"
    if "pilote" in n or "expert" in n:
        return "expert"
    if "vip" in n:
        return "vip"
    if "open" in n or "tous" in n or "all level" in n:
        return "open"
    if "side" in n:  # "Side", "Side-car" : pas un niveau de pilotage mais un type de véhicule
        return "side_car"
    return "autre"


_RE_HTML_TAG = re.compile(r"<[^>]+>")


def clean_text(s: str | None) -> str:
    """Strip tags HTML simples, décode entités, normalise les espaces.

    Conçu pour des champs courts (noms de produits, titres). Pas pour parser
    du HTML complet — utiliser BeautifulSoup pour ça.
    """
    if not s:
        return ""
    no_tags = _RE_HTML_TAG.sub(" ", s)
    return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()

MONTHS_FR = {
    # Forme longue
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12, "décembre": 12,
    # Abréviations courantes (avec ou sans point — voir _RE_TEXTUAL)
    "janv": 1, "févr": 2, "fevr": 2, "avr": 4,
    "juil": 7, "juill": 7,
    "sept": 9, "oct": 10, "nov": 11,
    "déc": 12, "dec": 12,
}

# "10 mai 2026", "DIMANCHE 10 MAI 2026", "ALES – Samedi 15 août 2026",
# "1er novembre 2026", "11 sept. 2026", "31 oct 2026".
# - day suivi optionnellement de "er"/"ère" (pour "1er", "1ère")
# - mois optionnellement suivi d'un point (pour les abréviations)
# - ordonnés par longueur décroissante pour que "septembre" matche avant "sept"
_MONTHS_PATTERN = "|".join(sorted(MONTHS_FR.keys(), key=len, reverse=True))
_RE_TEXTUAL = re.compile(
    r"(?P<day>\d{1,2})(?:er|ère|re)?\s+(?P<month>" + _MONTHS_PATTERN + r")\.?\s+(?P<year>\d{4})",
    re.IGNORECASE,
)
# "21/03/2026", "21-03-2026"
_RE_NUMERIC = re.compile(r"(?P<day>\d{1,2})[/-](?P<month>\d{1,2})[/-](?P<year>\d{4})")


def parse_french_date(text: str) -> date | None:
    """Extrait la première date trouvée dans `text`, format français.

    Renvoie None si rien trouvé. Ignore le jour de la semaine (lundi, mardi, …).
    """
    if not text:
        return None

    m = _RE_TEXTUAL.search(text)
    if m:
        day = int(m.group("day"))
        month = MONTHS_FR[m.group("month").lower()]
        year = int(m.group("year"))
        try:
            return date(year, month, day)
        except ValueError:
            return None

    m = _RE_NUMERIC.search(text)
    if m:
        try:
            return date(int(m.group("year")), int(m.group("month")), int(m.group("day")))
        except ValueError:
            return None

    return None


def euros_to_cents(value: str | float | int | None) -> int | None:
    """Convertit un montant en EUROS vers des cents.

    Accepte: 110, 110.0, '110', '110,50', '139 €', '299,00 €'.
    Retourne None si vide / invalide.

    Pour les API WooCommerce Store, utiliser plutôt `wc_price_to_cents` qui
    respecte le `currency_minor_unit` du shop (l'API WC renvoie déjà en cents
    selon une convention spécifique).
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value * 100
    if isinstance(value, float):
        return int(round(value * 100))

    s = value.strip().replace("€", "").replace(" ", "").replace(",", ".")
    if not s:
        return None

    try:
        return int(round(float(s) * 100))
    except ValueError:
        return None


def wc_price_to_cents(price: str | int | None, minor_unit: int | None) -> int | None:
    """Convertit un prix WooCommerce Store API en cents EUR (convention 2 décimales).

    L'API WC renvoie `prices.price` en 'minor units' selon la config de devise du
    shop, indiquée par `prices.currency_minor_unit`:

    - `minor_unit=2` (standard) : '14000' = 140€  → 14000 cents
    - `minor_unit=0` (vu chez Spoon Racing) : '140' = 140€ → 14000 cents

    Si `minor_unit` est None ou inconnu, fallback à 2 (cas le plus courant).
    """
    if price is None or price == "":
        return None
    try:
        n = int(price)
    except (ValueError, TypeError):
        return None

    if minor_unit is None:
        minor_unit = 2

    factor = 10 ** (2 - minor_unit)
    if factor >= 1:
        return n * factor
    # minor_unit > 2 : on tronque les sous-cents (rare, hors EUR standard).
    return n // (10 ** (minor_unit - 2))
