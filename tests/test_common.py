"""Tests unitaires sur scrapers/_common.py — fondations partagées par tous les scrapers."""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

# Permet de lancer `python -m unittest discover -s tests` depuis la racine.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers._common import (  # noqa: E402
    clean_text,
    euros_to_cents,
    normalize_circuit_name,
    normalize_level,
    parse_french_date,
    wc_price_to_cents,
)


class ParseFrenchDateTextual(unittest.TestCase):
    def test_lowercase(self):
        self.assertEqual(parse_french_date("10 mai 2026"), date(2026, 5, 10))

    def test_uppercase(self):
        self.assertEqual(parse_french_date("DIMANCHE 10 MAI 2026"), date(2026, 5, 10))

    def test_with_weekday_prefix(self):
        self.assertEqual(parse_french_date("Mardi 12 mai 2026"), date(2026, 5, 12))

    def test_with_dash_separator_prefix(self):
        # entité &#8211; = – (en dash)
        self.assertEqual(parse_french_date("ALES – Samedi 15 août 2026"), date(2026, 8, 15))

    def test_aout_without_accent(self):
        self.assertEqual(parse_french_date("Lundi 24 aout 2026"), date(2026, 8, 24))

    def test_aout_with_accent(self):
        self.assertEqual(parse_french_date("Lundi 24 août 2026"), date(2026, 8, 24))

    def test_fevrier_without_accent(self):
        self.assertEqual(parse_french_date("3 fevrier 2026"), date(2026, 2, 3))

    def test_decembre_with_accent(self):
        self.assertEqual(parse_french_date("31 décembre 2026"), date(2026, 12, 31))

    def test_picks_first_match(self):
        # Si plusieurs dates dans le texte, on prend la première (semantique attendue).
        self.assertEqual(
            parse_french_date("du 08 au 09 mai 2026, mis à jour le 1 janvier 2026"),
            date(2026, 5, 9),  # "09 mai 2026" est le premier match d'un triplet complet
        )

    def test_invalid_day(self):
        # 32 mai n'existe pas → None (pas de raise)
        self.assertIsNone(parse_french_date("32 mai 2026"))

    def test_premier_du_mois(self):
        # "1er novembre" doit être interprété comme jour 1
        self.assertEqual(parse_french_date("Dimanche 1er novembre 2026"), date(2026, 11, 1))
        self.assertEqual(parse_french_date("1ère décembre 2026"), date(2026, 12, 1))

    def test_abbreviated_month_with_dot(self):
        # SuperLaps utilise "sept.", "oct.", "nov."
        self.assertEqual(parse_french_date("11 sept. 2026"), date(2026, 9, 11))
        self.assertEqual(parse_french_date("31 oct. 2026"), date(2026, 10, 31))

    def test_abbreviated_month_no_dot(self):
        self.assertEqual(parse_french_date("11 sept 2026"), date(2026, 9, 11))
        self.assertEqual(parse_french_date("5 nov 2026"), date(2026, 11, 5))
        self.assertEqual(parse_french_date("3 janv 2026"), date(2026, 1, 3))

    def test_long_form_preferred_over_abbrev(self):
        # Vérifie que "septembre" ne matche pas comme "sept" + "embre"
        self.assertEqual(parse_french_date("12 septembre 2026"), date(2026, 9, 12))


class ParseFrenchDateNumeric(unittest.TestCase):
    def test_dd_mm_yyyy_slash(self):
        self.assertEqual(parse_french_date("21/03/2026"), date(2026, 3, 21))

    def test_dd_mm_yyyy_dash(self):
        self.assertEqual(parse_french_date("JOURNEE DU 27-05-2026 (ALES)"), date(2026, 5, 27))

    def test_invalid_numeric(self):
        self.assertIsNone(parse_french_date("99/99/2026"))


class ParseFrenchDateEmpty(unittest.TestCase):
    def test_empty_string(self):
        self.assertIsNone(parse_french_date(""))

    def test_no_date(self):
        self.assertIsNone(parse_french_date("Pas de date ici"))


class EurosToCents(unittest.TestCase):
    def test_int_euros(self):
        # 110 € → 11000 cents (DDE 34 microdata content="110")
        self.assertEqual(euros_to_cents(110), 11000)

    def test_string_int_euros(self):
        # "139" interprété comme euros (SuperLaps "139 €")
        self.assertEqual(euros_to_cents("139"), 13900)

    def test_float_euros(self):
        self.assertEqual(euros_to_cents(139.0), 13900)

    def test_string_decimal_euros(self):
        self.assertEqual(euros_to_cents("139.00"), 13900)

    def test_string_french_comma_decimal(self):
        # AK Racing affiche "299,00" (virgule décimale FR)
        self.assertEqual(euros_to_cents("299,00"), 29900)
        self.assertEqual(euros_to_cents("110,50"), 11050)

    def test_string_with_euro_symbol(self):
        self.assertEqual(euros_to_cents("139,00 €"), 13900)
        self.assertEqual(euros_to_cents("139 €"), 13900)

    def test_none(self):
        self.assertIsNone(euros_to_cents(None))

    def test_empty_string(self):
        self.assertIsNone(euros_to_cents(""))

    def test_invalid_string(self):
        self.assertIsNone(euros_to_cents("abc"))


class CleanText(unittest.TestCase):
    def test_none(self):
        self.assertEqual(clean_text(None), "")

    def test_empty(self):
        self.assertEqual(clean_text(""), "")

    def test_strips_html_tags(self):
        # Team SLA WC names: "CIRCUIT ALÈS <br> DIMANCHE 31 MAI 2026"
        self.assertEqual(
            clean_text("CIRCUIT ALÈS <br> DIMANCHE 31 MAI 2026"),
            "CIRCUIT ALÈS DIMANCHE 31 MAI 2026",
        )

    def test_strips_paired_tags(self):
        self.assertEqual(clean_text("Foo <strong>Bar</strong> Baz"), "Foo Bar Baz")

    def test_decodes_html_entities(self):
        # &#8211; = – (en dash), &amp; = &
        self.assertEqual(clean_text("Alès &#8211; Lundi"), "Alès – Lundi")
        self.assertEqual(clean_text("Foo &amp; Bar"), "Foo & Bar")

    def test_normalizes_whitespace(self):
        self.assertEqual(clean_text("  too\t\nmany   spaces  "), "too many spaces")


class WcPriceToCents(unittest.TestCase):
    def test_standard_minor_unit_2(self):
        # PMMC, DB Sport, Team SLA : price déjà en cents
        self.assertEqual(wc_price_to_cents("14000", 2), 14000)
        self.assertEqual(wc_price_to_cents("13900", 2), 13900)

    def test_unusual_minor_unit_0(self):
        # Spoon Racing : price en unités majeures
        self.assertEqual(wc_price_to_cents("140", 0), 14000)
        self.assertEqual(wc_price_to_cents("75", 0), 7500)

    def test_int_input(self):
        self.assertEqual(wc_price_to_cents(14000, 2), 14000)

    def test_none_minor_unit_falls_back_to_2(self):
        self.assertEqual(wc_price_to_cents("14000", None), 14000)

    def test_none_price(self):
        self.assertIsNone(wc_price_to_cents(None, 2))

    def test_empty_price(self):
        self.assertIsNone(wc_price_to_cents("", 2))

    def test_invalid_price(self):
        self.assertIsNone(wc_price_to_cents("not a number", 2))


class NormalizeLevel(unittest.TestCase):
    def test_debutant(self):
        self.assertEqual(normalize_level("Débutant"), "debutant")
        self.assertEqual(normalize_level("DEBUTANT"), "debutant")
        self.assertEqual(normalize_level("Novice"), "debutant")

    def test_intermediaire(self):
        # Initié (PMMC), Moyen (RideApp/Spoon), Intermédiaire générique
        self.assertEqual(normalize_level("Initié"), "intermediaire")
        self.assertEqual(normalize_level("Moyen"), "intermediaire")
        self.assertEqual(normalize_level("Intermédiaire"), "intermediaire")

    def test_confirme(self):
        self.assertEqual(normalize_level("Confirmé"), "confirme")
        self.assertEqual(normalize_level("CONFIRME"), "confirme")

    def test_expert(self):
        # Pilote (RideApp/Spoon), Expert (PMMC)
        self.assertEqual(normalize_level("Pilote"), "expert")
        self.assertEqual(normalize_level("Expert"), "expert")

    def test_open(self):
        self.assertEqual(normalize_level("Tous niveaux"), "open")
        self.assertEqual(normalize_level("Open"), "open")
        self.assertEqual(normalize_level("All levels"), "open")

    def test_vip(self):
        self.assertEqual(normalize_level("VIP Day"), "vip")

    def test_side_car(self):
        self.assertEqual(normalize_level("Side"), "side_car")
        self.assertEqual(normalize_level("Side-car"), "side_car")

    def test_unknown(self):
        self.assertEqual(normalize_level("Quelque chose"), "autre")
        self.assertEqual(normalize_level(""), "autre")
        self.assertEqual(normalize_level(None), "autre")


class NormalizeCircuitName(unittest.TestCase):
    def test_ales_variants(self):
        self.assertEqual(normalize_circuit_name("Alès"), "ales")
        self.assertEqual(normalize_circuit_name("ALES"), "ales")
        self.assertEqual(normalize_circuit_name("ales"), "ales")
        self.assertEqual(normalize_circuit_name("circuit d'Alès"), "ales")
        self.assertEqual(normalize_circuit_name("Pôle Mécanique Alès Cévennes"), "ales")

    def test_ledenon_variants(self):
        self.assertEqual(normalize_circuit_name("Lédenon"), "ledenon")
        self.assertEqual(normalize_circuit_name("LEDENON"), "ledenon")
        self.assertEqual(normalize_circuit_name("circuit de Lédenon"), "ledenon")
        # Edge case Team SLA : slug "ledenonsamedi" sans tiret
        self.assertEqual(normalize_circuit_name("ledenonsamedi"), "ledenon")

    def test_substring_fallback(self):
        # Title comme "Stage de pilotage moto, Circuit de Lédenon 28 juillet 2026"
        # doit matcher 'ledenon' en substring
        self.assertEqual(
            normalize_circuit_name("Stage de pilotage moto, Circuit de Lédenon 28 juillet 2026"),
            "ledenon",
        )

    def test_le_luc_with_separator(self):
        self.assertEqual(normalize_circuit_name("Le Luc"), "le-luc")
        self.assertEqual(normalize_circuit_name("Le Luc-en-Provence"), "le-luc")

    def test_european_circuits(self):
        self.assertEqual(normalize_circuit_name("Spa-Francorchamps"), "spa-francorchamps")
        self.assertEqual(normalize_circuit_name("Mugello"), "mugello")
        self.assertEqual(normalize_circuit_name("Brno"), "brno")
        self.assertEqual(normalize_circuit_name("Portimão"), "portimao")
        self.assertEqual(normalize_circuit_name("Alcarràs"), "alcarras")

    def test_unknown_returns_none(self):
        self.assertIsNone(normalize_circuit_name("Circuit Inexistant XYZ"))
        self.assertIsNone(normalize_circuit_name(""))
        self.assertIsNone(normalize_circuit_name(None))

    def test_no_false_positive_on_short_substring(self):
        # 'spa' pourrait matcher dans 'espace' — on exige >= 4 chars donc OK
        self.assertIsNone(normalize_circuit_name("espace"))


if __name__ == "__main__":
    unittest.main()
