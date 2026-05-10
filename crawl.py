"""Orchestrator — lance tous les scrapers, upsert les events, regénère le HTML."""
from __future__ import annotations

import sys
import traceback
from importlib import import_module
from time import perf_counter

import db
import render as render_module

SCRAPERS = [
    "scrapers.pmmc",
    "scrapers.dbsport",
    "scrapers.dde34",
    "scrapers.superlaps",
    "scrapers.teamsla",
    "scrapers.spoonracing",
    "scrapers.akracing",
    "scrapers.rideapp",
    "scrapers.erdete",
    "scrapers.accespiste",
]


def main() -> int:
    db.init()
    total_inserted = 0
    total_updated = 0
    failures: list[tuple[str, str]] = []
    warnings: list[str] = []

    for module_name in SCRAPERS:
        t0 = perf_counter()
        try:
            mod = import_module(module_name)
            events = mod.fetch()
            inserted, updated = db.upsert_events(events)
            total_inserted += inserted
            total_updated += updated
            elapsed = perf_counter() - t0
            tag = "[OK]  " if events else "[WARN]"
            print(f"{tag} {module_name:30s} {len(events):3d} events  +{inserted} new  ~{updated} updated  ({elapsed:.2f}s)")
            if not events:
                warnings.append(f"{module_name}: 0 events returned (silent breakage?)")
        except Exception as e:
            elapsed = perf_counter() - t0
            print(f"[FAIL] {module_name:30s} {type(e).__name__}: {e}  ({elapsed:.2f}s)")
            traceback.print_exc()
            failures.append((module_name, str(e)))

    print()
    print(f"Summary: +{total_inserted} new, ~{total_updated} updated, {len(warnings)} warnings, {len(failures)} failed")
    for w in warnings:
        print(f"  WARN: {w}")

    # Regénère le site multi-page. Si un scraper a fail, on régénère quand même —
    # les données existantes restent valables.
    try:
        stats = render_module.render()
        print(f"Rendered {stats['circuits']} circuits, {stats['events']} events, {stats['pages']} HTML pages -> dist/")
    except Exception as e:
        print(f"[FAIL] render: {type(e).__name__}: {e}")
        traceback.print_exc()

    # Exit code : 1 si fail dur, 2 si seulement des warnings, 0 sinon.
    if failures:
        return 1
    if warnings:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
