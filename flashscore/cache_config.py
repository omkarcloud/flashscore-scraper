"""Cache TTL per /flashscore/* endpoint (cache.py, keyed on the validated
params — marshmallow fills the defaults, so `?page=1` and no `page` share a
row).

Tiers follow how fast Flashscore itself moves each surface: live scores
change every few seconds, a finished match never changes again, and the
sport/country catalogues change a few times a season. Endpoints that serve
BOTH live and finished matches from one route (every /matches/* detail tab)
take the short tier — a stale live score is the one error a scores API
cannot afford, and the upstream is cheap.
"""
from datetime import timedelta

# --- live surfaces -----------------------------------------------------------
# The day list mixes scheduled, live and finished; the site itself repolls
# every ~15 s.
MATCH_LIST_CACHE = timedelta(seconds=30)
# Live-only lists and the diff feeds: as short as the cache is worth having.
LIVE_CACHE = timedelta(seconds=15)

# --- match detail ------------------------------------------------------------
# Core card, incidents, statistics, line-ups, commentary: a finished match is
# immutable but a live one moves, and one route serves both.
MATCH_DETAIL_CACHE = timedelta(minutes=1)
# Player statistics and odds comparison are heavier upstream calls (the PMS
# payload is ~1 MB) and move a little more slowly than the score.
MATCH_HEAVY_CACHE = timedelta(minutes=3)
# Head-to-head, missing players, the match report and broadcasts are settled
# well before kick-off.
MATCH_STATIC_CACHE = timedelta(hours=6)

# --- competitions ------------------------------------------------------------
# Tables move on match days.
STANDINGS_CACHE = timedelta(minutes=30)
# Results and fixtures pages: a result lands minutes after the final whistle.
FIXTURES_CACHE = timedelta(minutes=30)
# The competition card, its season list and archive change once a season.
TOURNAMENT_CACHE = timedelta(hours=12)
SEASONS_CACHE = timedelta(days=7)
# Which standings tabs a competition publishes is a per-season fact.
STANDINGS_TYPES_CACHE = timedelta(days=1)
# Knockout brackets change only as ties are played.
DRAW_CACHE = timedelta(hours=3)

# --- teams / players ---------------------------------------------------------
TEAM_CACHE = timedelta(hours=12)
SQUAD_CACHE = timedelta(hours=12)
TRANSFERS_CACHE = timedelta(hours=12)
PLAYER_CACHE = timedelta(hours=12)

# --- catalogues / search -----------------------------------------------------
# The sport list carries today's live counters, so it cannot be cached long.
SPORTS_CACHE = timedelta(minutes=5)
# Country and competition menus change a handful of times a season.
CATALOGUE_CACHE = timedelta(days=1)
# Search results shift only when entities are created or renamed.
SEARCH_CACHE = timedelta(hours=12)
# Link resolution is a pure id lookup.
RESOLVE_CACHE = timedelta(days=1)

# --- news / rankings ---------------------------------------------------------
NEWS_CACHE = timedelta(minutes=30)
ARTICLE_CACHE = timedelta(hours=12)
# Tennis rankings are republished weekly (Mondays).
RANKINGS_CACHE = timedelta(hours=12)
