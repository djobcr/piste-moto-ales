# Context — Piste Moto Aggregator

> Document de handoff pour reprendre le projet dans une nouvelle conversation Claude
> sans perdre le fil. Lis ce fichier en premier avant toute modification du code.
> Dernière mise à jour : 2026-05-10.

## 1. Identité du projet

**User** : pilote moto à Alès (Pôle Mécanique Cévennes), Windows 11, Python 3.13.
Travaille en français, préfère itérer petit à petit, donne carte blanche sur la
technique quand le scope est cadré.

**Pitch** : agrégateur de sorties piste moto. Crawl 1×/h les sites des
organisateurs publics, stocke en SQLite, génère un site HTML statique avec
filtres + tri + bouton "Réserver" en deep-link vers l'organisateur. **Pas de
transaction côté agrégateur** — on aiguille du trafic, ils gèrent la billetterie.

**Site live** : https://djobcr.github.io/piste-moto/ (mis à jour chaque heure UTC)
**Repo** : https://github.com/djobcr/piste-moto

## 2. Chiffres actuels (mai 2026)

| | |
|---|---|
| Circuits couverts (référencés dans `circuits.json`) | 35 (22 FR + 5 ES + 3 IT + 1 BE + 1 CZ + 1 PT + 2 ajoutés via H2S) |
| Circuits actifs (avec ≥1 event futur) | ~33 |
| Sources scrapées (modules `scrapers/*.py`) | 12 |
| Organisateurs distincts en DB | 16 |
| Events futurs agrégés | ~300 |
| Pages HTML générées par crawl | ~88 |
| Tests unitaires | 56+ |

## 3. Stack

- **Python 3.13** sans framework. Stdlib (sqlite3, json, dataclasses) + 3 deps :
  - `httpx` (HTTP client)
  - `beautifulsoup4` (parsing HTML pour les scrapers non-API)
  - `jinja2` (templating)
- **SQLite** stdlib, fichier `piste.db` à la racine, commité dans le repo
  (persistence de `first_seen_at` entre les runs CI)
- **Frontend** : HTML statique généré, CSS via **Tailwind CDN + DaisyUI 4 CDN**
  (zéro build step, JS vanilla, thème `dim`)
- **Déploiement** : GitHub Actions (cron horaire) + GitHub Pages "from Actions"

## 4. Structure du repo

```
.
├── .github/workflows/crawl.yml    # cron horaire + push + deploy Pages
├── data/
│   └── circuits.json              # référentiel 35 circuits (slug, name, lat/lon, image_url, aliases)
├── scrapers/
│   ├── _common.py                 # helpers partagés (parse dates FR, normalize_circuit/level, USER_AGENT)
│   ├── pmmc.py                    # WC Store API (Pôle Mécanique MC)
│   ├── dbsport.py                 # WC Store API (Denis Bouan), multi-circuit
│   ├── teamsla.py                 # WC Store API + variations stock par niveau
│   ├── spoonracing.py             # WC Store API (currency_minor_unit=0 !)
│   ├── h2smoto.py                 # WC Store API multi-circuit
│   ├── dde34.py                   # HTML Joomla + microdata schema.org pour stock/availability
│   ├── superlaps.py               # 2 endpoints HTML : Vikings + Acid Tracks
│   ├── accespiste.py              # HTML Drupal multi-circuit
│   ├── akracing.py                # HTML Odoo + microdata schema.org
│   ├── rideapp.py                 # API JSON publique multi-orga (MGB, BMC, FOT, PA13)
│   ├── erdete.py                  # HTML Squarespace, 1 event/an, prix CHF
│   └── activbike.py               # HTML custom Lédenon uniquement
├── templates/
│   ├── _base.html                 # head + tabs nav + footer (extends par tous)
│   ├── circuit_index.html         # / : grille des circuits
│   ├── circuit_detail.html        # /circuits/{slug}/ : info + CTA
│   ├── circuit_dates.html         # /circuits/{slug}/dates.html : calendrier+liste
│   ├── organizer_index.html       # /organisateurs/ : grille des orgas
│   └── organizer_detail.html      # /organisateurs/{slug}/ : events de l'orga
├── tests/
│   └── test_common.py             # 56+ tests sur les helpers (pas de mock HTTP)
├── db.py                          # schéma SQLite + dataclass Event/Level + upsert
├── crawl.py                       # orchestrateur : pour chaque scraper appelle fetch()
├── render.py                      # génère dist/ multi-page depuis events_active
├── piste.db                       # généré, commité par le workflow
├── requirements.txt               # httpx + beautifulsoup4 + jinja2
└── README.md                      # doc user/quickstart
```

## 5. Pipeline (à chaque exécution de `python crawl.py`)

```
1. db.init() : crée la table events + view events_active si absente
2. Pour chaque scraper dans SCRAPERS :
   - fetch() → list[Event]
   - upsert via db.upsert_events() (clé : organizer + source_id)
3. render.render() :
   - lit events_active (filtre date >= today via SQL view)
   - groupe par circuit_slug (via raw_data) puis par mois
   - génère index circuits + détail*N + dates*N + index orgas + détail orgas
4. Workflow GitHub Actions :
   - commit piste.db (avec [skip ci]) si modifiée
   - upload dist/ vers GitHub Pages
```

## 6. Modèle de données

### Table `events`
```sql
id INT PK, organizer TEXT, source_id TEXT, circuit TEXT, date TEXT (ISO),
title TEXT, price_cents INT, currency TEXT, available INT,
booking_url TEXT, levels TEXT (JSON), raw_data TEXT (JSON),
first_seen_at TEXT, last_seen_at TEXT,
UNIQUE(organizer, source_id)
```

### View `events_active`
```sql
SELECT * FROM events WHERE date >= date('now')
```

### Conventions raw_data (JSON par event)
- `circuit_slug` : slug canonique (obligatoire pour le rendu, sinon event ignoré)
- `circuit_image_url` : URL image RideApp si dispo (fallback sur circuits.json)
- `organizer_logo_url` : URL logo RideApp si dispo
- `organizer_slug`, `event_day_id` : RideApp uniquement
- `remaining_seats` : nombre exact de places (RideApp uniquement)
- `low_stock_remaining` : nombre exact si stock bas (Team SLA)
- `gauge_px`, `availability_schema` : DDE 34 (jauge + microdata)
- `roulage_id`, `is_pack` : Accès Piste

### Dataclass Level (champ `levels` est un JSON list de Level)
```python
{
  "raw": "Débutant",            # nom display
  "canonical": "debutant",      # slug normalisé pour filtre
  "remaining": 4,               # int ou None — RideApp + Team SLA
  "max": 32,                    # int ou None
  "is_in_stock": True           # bool ou None — PMMC, Spoon, Team SLA
}
```

Les 7 buckets canoniques : `debutant`, `intermediaire` (Moyen/Initié),
`confirme`, `expert` (Pilote), `open` (tous niveaux), `side_car` (rare, MGB), `vip`.

## 7. Décisions clés (à connaître pour ne pas refaire les erreurs)

### Pourquoi pas calendrier-piste.fr
Audit fait au début : leurs CGU interdisent explicitement la reproduction.
On a basculé sur "scrape directement chez les organisateurs" — beaucoup mieux
juridiquement, et données plus riches (places dispo, prix par niveau).

### WooCommerce Store API
**Toujours tester** `/{base}/wp-json/wc/store/v1/products` sur les sites WP
avant de scraper du HTML. 6 sources sur 12 utilisent cette API publique sans
auth. Pour le stock par variation : `/wp-json/wc/store/v1/products/{variation_id}`.

### currency_minor_unit
**Spoon Racing** utilise `currency_minor_unit=0` (price "140" = 140€) alors
que tous les autres WP ont `minor_unit=2` (price en cents). Le helper
`wc_price_to_cents(price, minor_unit)` gère les deux.

### RideApp
Plateforme SaaS multi-orga (MGB, BMC, First on Track, Team PA13) avec API
JSON publique sans auth. C'est la **meilleure source** : `remainingSeats`
exact par niveau via `/api/v1/events/{id}/groups`. Multi-circuit, multi-pays.
**Bug connu corrigé** : pour les events multi-jours, l'API renvoie un groupe
par jour → on agrège par canonical (max sur remaining/max, min sur displayOrder).

### URL canoniques
Le domaine `rideapp.pro` qu'on voit dans les payloads ne résout pas DNS.
Toujours **normaliser vers `shop.rideapp.fr`** pour les images
(scraper rideapp.py le fait déjà).

### Convention `_common.parse_french_date()`
- Gère "Mercredi 12 mai 2026", "1er novembre 2026", "11 sept. 2026", "21/03/2026"
- Pour les ranges "du 08 au 09 mai 2026" → matche **la date de fin** (premier
  triplet jour-mois-année complet). Pour SuperLaps qui utilise ce format, on
  reconstruit la date de début depuis `.day-journees` + `.month-journees`.

### `euros_to_cents` vs `wc_price_to_cents`
- `euros_to_cents(value)` : un entier sans virgule = euros (multiplier par 100).
  Utilisé pour DDE 34, SuperLaps, AK Racing (textes HTML)
- `wc_price_to_cents(price, minor_unit)` : applique `10**(2-minor_unit)`.
  Utilisé exclusivement pour les API WC Store

### Pas de colonne `circuit_slug` en DB
Décision : on stocke `circuit` (display) en colonne, et `circuit_slug` (canonical)
dans `raw_data` JSON. Ça évite une migration DB et garde la flexibilité.
`render.py` lit `raw_data["circuit_slug"]` pour grouper.

### Multi-page HTML statique avec path_prefix
Chaque template définit `{% set path_prefix = "../../" %}` selon sa profondeur
pour générer les bons liens relatifs vers les autres pages. Plus simple que
des URLs absolues.

### DDE 34 timeout
Le serveur Joomla peut être lent (parfois >30s). Le scraper utilise un
`SCRAPER_TIMEOUT = 60.0` spécifique au lieu du `HTTP_TIMEOUT = 30.0` global.

## 8. Sources de données (status)

| Module | Status | Format | Particularité |
|---|---|---|---|
| `pmmc.py` | ✅ | WC Store API | mono-circuit (Alès), `is_in_stock` par variation |
| `dbsport.py` | ✅ | WC Store API | multi-circuit, filtre par préfixe nom + catégorie |
| `dde34.py` | ✅ | HTML + microdata | mono-circuit (Alès), schema.org availability |
| `superlaps.py` | ✅ | HTML | 2 endpoints (Vikings + Acid Tracks) |
| `teamsla.py` | ✅ | WC Store API + variations | multi-circuit, `low_stock_remaining` parfois |
| `spoonracing.py` | ✅ | WC Store API | mono-circuit, `currency_minor_unit=0` (gotcha) |
| `akracing.py` | ✅ | HTML Odoo + microdata | multi-circuit, filtre via location |
| `rideapp.py` | ✅ | API JSON publique | **multi-orga + multi-circuit + places exactes** |
| `erdete.py` | ✅ | HTML Squarespace | 1 event/an, **CHF** |
| `accespiste.py` | ✅ | HTML Drupal | multi-circuit, `block-img-epuise` = complet |
| `h2smoto.py` | ✅ | WC Store API | multi-circuit, 4 formules par event |
| `activbike.py` | ✅ | HTML custom | Lédenon only, `inactive` class = passé |

**Sources auditées et skippées (raison documentée)** :
- **calendrier-piste.fr** : CGU interdisent reproduction
- **LPP Team** : pas de Lédenon dans le shop (mentions en pages statiques)
- **PPO Track Days** : events 2023 obsolètes
- **FFMC 26-07** : 1 article/an, low value
- **Swiss Norton** : 2 events/an, low value
- **Engage-sports/FFM** : seulement 2-3 "roulage-circuit" parmi motocross/trial,
  plateforme white-label par sous-domaine, pas d'API publique

## 9. Conventions de code

- **Pas de classe ScraperBase** : chaque scraper expose une fonction
  `fetch() -> list[Event]`. Le contrat est minimaliste, l'orchestrator
  utilise `importlib.import_module()` pour les charger.
- **`continue-on-error`** sur le step crawl du workflow : un scraper down
  ne doit pas bloquer le déploiement.
- **`event.raw_data`** : sac à dos JSON pour stocker tout ce qui peut être
  utile au render sans alourdir le schéma.
- **Tests** : unittest stdlib (pas pytest), focus sur les helpers `_common.py`
  (parsing dates FR, normalize_circuit, normalize_level, prix). Pas de mock
  HTTP : on teste juste les fonctions pures.
- **Commits** : message descriptif court + body explicatif. Préfixe `Phase N
  Lot M —` pour les itérations majeures.
- **Console Windows cp1252** : les `print()` avec accents ou emojis échouent
  parfois en bash console — c'est juste l'affichage, la DB et les fichiers
  sont en UTF-8.

## 10. Commandes essentielles

```powershell
# Setup initial
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Crawl + render + push (workflow normal)
py crawl.py

# Render only (depuis DB existante, sans re-crawler)
py render.py

# Tests
py -m unittest discover -s tests -v

# Serveur local pour preview
py -m http.server -d dist 8000
# → http://localhost:8000
```

## 11. Workflow GitHub Actions (`.github/workflows/crawl.yml`)

Triggers : `cron: "0 * * * *"` (chaque heure UTC) + `workflow_dispatch` +
`push` sur main.

Permissions : `contents: write` (pour commit DB) + `pages: write` +
`id-token: write` (pour deploy Pages).

Steps : checkout → setup-python 3.13 → install deps → run tests → run crawl
(continue-on-error) → render → commit DB si changée (`[skip ci]`) → upload
artifact → deploy Pages.

## 12. Troubleshooting connu

| Symptôme | Cause | Fix |
|---|---|---|
| `git pull --rebase` conflict sur `piste.db` | Le bot github-actions a commit pendant qu'on bossait local | `git checkout --ours piste.db && git add piste.db && git rebase --continue` |
| `git push` Personal Access Token invalid | GCM cache un vieux token | `printf "protocol=https\nhost=github.com\n\n" \| git credential reject` puis re-push |
| `print()` UnicodeEncodeError cp1252 | Console Windows ne décode pas U+2713 etc. | Ignorer — fichiers/DB en UTF-8 OK. Si gênant, replace dans le print par ASCII |
| AK Racing scraper retourne 0 events | Filtre legacy `if "ales" not in slug` oublié dans `_iter_event_links` | Vérifier qu'on retire bien tous les anciens filtres mono-circuit après refactor |
| Card avec 11 badges niveau | Event RideApp multi-jours non agrégé | `_fetch_groups` doit dédupliquer par canonical (max sur remaining) |

## 13. Roadmap / TODO

### Court terme (si valeur user claire)
- Alertes places dispo (email, push) — demande backend ou service tiers
- Hard cleanup `events.last_seen_at < today - N jours` pour pas faire grossir la DB

### Moyen terme
- Investiguer les autres sous-domaines `*.engage-sports.com` (Ultimate Cup,
  etc.) si pertinents
- Améliorer ActivBike pour autres circuits (mapping `id_circuit_activbike →
  slug` à étendre)
- Page "Recherche" globale qui search dans tous les events (full-text)

### Long terme / nice to have
- Photos plus pro pour Clastres + Fontenay-le-Comte
- Page "Favoris" en localStorage côté client
- Vue "Carte" avec geo des circuits

## 14. Mémoire utilisateur (claude.ai)

Les fichiers de mémoire utilisateur sont dans
`C:\Users\sonyn\.claude\projects\C--Users-sonyn-Desktop-App-Piste-Moto\memory\` :

- `MEMORY.md` : index
- `user_profile.md` : profil pilote moto
- `project_piste_aggregator.md` : phases du projet (Phase 1 → Phase 3 + Lots)
- `reference_organizers_audit.md` : audit des sources

Ces mémoires sont injectées automatiquement au début des conversations.

## 15. Comment reprendre dans une nouvelle conversation Claude

1. Lis ce `CONTEXT.md` en premier
2. Vérifie l'état actuel : `py crawl.py` (ou consulte le dernier run sur
   GitHub Actions)
3. La mémoire utilisateur sera déjà chargée (cf. section 14)
4. Pour toute modification : suivre les conventions de la section 9
5. **Ne jamais** réintroduire un filtre "Alès only" dans un scraper —
   l'archi est multi-circuit depuis Phase 1 du refactor
6. **Toujours** mettre à jour `data/circuits.json` quand on découvre un
   nouveau circuit dans une source (sinon les events sont skippés silencieusement
   par `normalize_circuit_name() → None`)
