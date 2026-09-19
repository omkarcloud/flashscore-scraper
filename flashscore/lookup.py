"""Id resolution for the refs that need more than the caller can type.

Three feeds need ids that appear nowhere in a public URL, so they are read
off the entity's own page and memoised:

  * a TEAM's sport id — `pr_/pf_<sport>_<country>_<team>_…` returns an empty
    body for the wrong sport (the country segment, by contrast, is ignored).
  * a COMPETITION's (tournament_id, stage_id) pair per season, plus the
    NUMERIC season id that `tr_/tf_<sport>_<country>_<template>_<season>_…`
    takes — that number is printed only inside the page's
    `cjs.initialFeeds[...]` block.
  * a competition's template id, which the pagination feeds key on and which
    only appears as `ZEE` inside the feed data itself.

Both resolvers return a small dict and are cached in-process (the pages are
0.7-1.4 MB; the ids behind them change at most once a season). The contexts
also carry the page itself and its embedded page-one results/fixtures, which
DO move, so they expire after CONTEXT_TTL; only the bare slugs live on. The
cache is bounded and holds no user input.
"""
import threading
import time

from . import parsers as P
from .fetch import FlashscoreNotFound, get_page

_CACHE_LIMIT = 512
# Shorter than every response-cache tier that reads a context (FIXTURES /
# STANDINGS at 30 min), so a response-cache miss never rebuilds from a stale
# page one.
CONTEXT_TTL = 300
_teams = {}
_tournaments = {}
_lock = threading.Lock()


def _recall(store, key, ttl=None):
    entry = store.get(key)
    if entry is None:
        return None
    stamp, value = entry
    if ttl is not None and time.monotonic() - stamp > ttl:
        return None
    return value


def _remember(store, key, value, ttl=None):
    now = time.monotonic()
    with _lock:
        if ttl is not None:
            for stale in [k for k, (stamp, _) in store.items() if now - stamp > ttl]:
                store.pop(stale, None)
        if len(store) >= _CACHE_LIMIT:
            store.clear()
        store[key] = (now, value)
    return value


# ---- teams ------------------------------------------------------------------------

_slugs = {}
_TEAM_SLUG_RE = None


def team_slug(ref):
    """The URL slug a team page needs. Flashscore 404s a wrong slug, and no
    feed maps an id to one directly — but the team's own transfer feed
    (`tetr_`, ~13 KB, keyed on the id alone) prints the canonical
    /team/<slug>/<id>/ path in every row, so it doubles as the resolver."""
    global _TEAM_SLUG_RE
    team_id = ref["id"] if isinstance(ref, dict) else ref
    slug = ref.get("slug") if isinstance(ref, dict) else None
    if slug:
        return slug
    cached = _recall(_slugs, team_id)
    if cached is not None:
        return cached
    if _TEAM_SLUG_RE is None:
        import re
        _TEAM_SLUG_RE = re.compile(r"/team/([a-z0-9-]+)/([A-Za-z0-9]{6,10})/")
    from .fetch import get_feed
    text = get_feed(f"tetr_{team_id}_1_1", optional=True)
    for found_slug, found_id in _TEAM_SLUG_RE.findall(text or ""):
        if found_id == team_id:
            return _remember(_slugs, team_id, found_slug)
    raise FlashscoreNotFound(f"team {team_id} not found")


def team_page(ref, *, tab=""):
    """One team page as HTML, addressed by its canonical /team/<slug>/<id>/."""
    team_id = ref["id"] if isinstance(ref, dict) else ref
    path = f"/team/{team_slug(ref)}/{team_id}/{tab}".rstrip("/") + "/"
    return get_page(path, expect=team_id)


def team_context(ref):
    """{sport_id, name, country, logo, feeds} for a team, from its page.

    `feeds` carries the embedded page-one results/fixtures blocks so a caller
    that only wants page one never issues a second request."""
    team_id = ref["id"] if isinstance(ref, dict) else ref
    cached = _recall(_teams, team_id, CONTEXT_TTL)
    if cached is not None:
        return cached
    html = team_page(ref)
    environment = P.page_environment(html)
    soup = P.soup(html)
    heading = soup.select_one("div.container__heading")
    name = P.text_of(soup.select_one("[class*=heading__name]"))
    logo = soup.select_one("[class*=heading__logo] img") or soup.select_one("[class*=heading] img")
    country = None
    if heading is not None:
        crumbs = [P.clean(a.get_text(" ", strip=True)) for a in heading.select("a")]
        crumbs = [c for c in crumbs if c and c != name]
        country = crumbs[-1] if crumbs else None
    context = {
        "id": team_id,
        "sport_id": P.to_int(environment.get("sport_id")),
        "name": name,
        "country": country,
        "logo": logo.get("src") if logo is not None else None,
        "venue": _venue(soup),
        "feeds": P.initial_feeds(html),
        "html": html,
    }
    if not context["sport_id"]:
        raise FlashscoreNotFound(f"team {team_id} not found")
    context["slug"] = team_slug(ref)
    return _remember(_teams, team_id, context, CONTEXT_TTL)


def player_page(ref, *, tab=""):
    """One player page as HTML, addressed by /player/<slug>/<id>/.

    Unlike teams, nothing upstream maps a player id to its slug (probed
    2026-09-18: no player-keyed feed carries one, and the search service
    only matches names), so a bare id cannot reach this page. Every player
    reference this API emits carries a `link`, so callers always have the
    full form to hand back."""
    player_id = ref["id"] if isinstance(ref, dict) else ref
    slug = ref.get("slug") if isinstance(ref, dict) else None
    if not slug:
        raise ValueError(
            "player must be a full flashscore.com player link for this endpoint "
            f"(e.g. https://www.flashscore.com/player/mendes-nuno/{player_id}/) — "
            "Flashscore rejects a player page whose URL slug is missing or wrong, "
            "and publishes no id-to-slug lookup. Every player in this API's "
            "responses carries its `link`.")
    path = f"/player/{slug}/{player_id}/{tab}".rstrip("/") + "/"
    return get_page(path, expect=player_id)


def _venue(soup):
    """"Stadium: Estadio Santiago Bernabéu (Madrid) Capacity: 83 186" ->
    {name, city, capacity}. Absent for most non-football teams."""
    for block in soup.select("[class*=heading__info]"):
        text = P.text_of(block) or ""
        if "Stadium" not in text and "Capacity" not in text:
            continue
        name = city = None
        stadium_part = text.split("Capacity")[0]
        stadium_part = stadium_part.split(":", 1)[-1].strip() if ":" in stadium_part else stadium_part
        if "(" in stadium_part:
            name, _, rest = stadium_part.partition("(")
            city = rest.rstrip(") ").strip() or None
            name = name.strip() or None
        else:
            name = P.clean(stadium_part)
        capacity = None
        if "Capacity" in text:
            capacity = P.to_int(text.split("Capacity", 1)[1])
        if name or capacity:
            return {"name": name, "city": city, "capacity": capacity}
    return None


def team_sport(team_id):
    return team_context(team_id)["sport_id"]


# ---- competitions -------------------------------------------------------------------

def tournament_page(path, *, tab=""):
    """One competition page as HTML. `path` is the league path from refs
    (/football/spain/laliga/); `tab` adds results/fixtures/archive."""
    full = path.rstrip("/") + "/" + (tab.strip("/") + "/" if tab else "")
    marker = path.rstrip("/").split("/")[-1]
    return get_page(full, expect=marker)


def tournament_context(ref):
    """Everything the competition routes need, from one page fetch:

        {path, sport_id, country_id, tournament_id, stage_id, template_id,
         season_id, name, seasons: [{name, tournament_id, stage_id}],
         stages: [{id, name}], feeds: {...}}

    A ref carrying an explicit <tournament_id>:<stage_id> pair skips the page
    for the endpoints that need nothing more (standings, draw, top scorers);
    those call `ids_only` instead."""
    path = ref.get("path")
    if not path:
        return ids_only(ref)
    cached = _recall(_tournaments, path, CONTEXT_TTL)
    if cached is not None:
        return cached
    html = tournament_page(path)
    environment = P.page_environment(html)
    feeds = P.initial_feeds(html)
    stages = (environment.get("stages_group") or {}).get("stages") or []
    seasons = _seasons(environment)
    selected = P.to_int(environment.get("selected_season_id")) or 0
    current = seasons[selected] if 0 <= selected < len(seasons) else (seasons[0] if seasons else {})
    season_id = None
    template_id = None
    for name in ("results", "fixtures"):
        block = feeds.get(name) or {}
        season_id = season_id or block.get("season_id")
        if not template_id and block.get("text"):
            header = next((r for r in P.parse_records(block["text"]) if "ZEE" in r), None)
            if header:
                template_id = P.clean_id(header.get("ZEE"))
    soup = P.soup(html)
    context = {
        "path": path,
        "sport_id": P.to_int(environment.get("sport_id")),
        "country_id": P.to_int(stages[0].get("countryId")) if stages else None,
        "tournament_id": current.get("tournament_id"),
        "stage_id": current.get("stage_id") or (stages[0].get("id") if stages else None),
        "template_id": template_id,
        "season_id": season_id,
        "season_name": current.get("name"),
        "name": P.text_of(soup.select_one("[class*=heading__name]")),
        "seasons": seasons,
        "stages": [{"id": P.clean_id(s.get("id")), "name": P.clean(s.get("name")),
                    "type": P.clean(s.get("statsType"))} for s in stages],
        "feeds": feeds,
        "html": html,
    }
    if not context["sport_id"]:
        raise FlashscoreNotFound(f"competition {path} not found")
    return _remember(_tournaments, path, context, CONTEXT_TTL)


def _seasons(environment):
    """`season_list` entries look like
    {"id": 0, "name": "2026/2027", "pathname": "/standings/QeI1Oeyi/dWdJXP6U/"}
    — the pathname is where the (tournament_id, stage_id) pair lives."""
    out = []
    for entry in environment.get("season_list") or []:
        parts = [p for p in str(entry.get("pathname") or "").split("/") if p]
        ids = [p for p in parts if len(p) == 8 and p.isalnum()]
        out.append({
            "name": P.clean(entry.get("name")),
            "tournament_id": ids[0] if len(ids) > 0 else None,
            "stage_id": ids[1] if len(ids) > 1 else None,
        })
    return out


def ids_only(ref):
    """A context holding just the id pair a caller supplied — enough for the
    feeds keyed on (tournament_id, stage_id) and nothing else."""
    return {
        "path": None, "sport_id": None, "country_id": None,
        "tournament_id": ref.get("tournament_id"), "stage_id": ref.get("stage_id"),
        "template_id": None, "season_id": None, "season_name": None,
        "name": None, "seasons": [], "stages": [], "feeds": {}, "html": "",
    }


def require_pair(context, what="this endpoint"):
    """Raise a helpful 400 when a caller passed an id pair to an endpoint that
    needs the page-derived ids (or vice versa)."""
    if not context.get("tournament_id") or not context.get("stage_id"):
        raise ValueError(f"{what} needs a competition link "
                         "(e.g. /football/spain/laliga/) or a <tournament_id>:<stage_id> pair")
    return context


def require_pagination_ids(context):
    if not context.get("template_id") or not context.get("season_id"):
        raise ValueError("this endpoint needs a competition LINK "
                         "(e.g. /football/spain/laliga/) — an id pair does not carry the "
                         "template and season numbers its pages are keyed on")
    return context
