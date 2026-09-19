"""Discovery endpoints — the catalogue layer the competing Flashscore APIs
leave out: what sports exist, what countries and competitions each has, and
free-text search over teams, players and competitions.

Search runs against s.livesport.services, the same autocomplete the site's
own search box uses: plain JSON, no auth, and it returns the ids and slugs
every other endpoint here takes.
"""
from . import lookup
from . import parsers as P
from . import refs
from .fetch import get_feed, get_page, get_search

# A country page prints `country_id: 176` rather than a window.environment blob.
_COUNTRY_ID_RE = __import__("re").compile(r"country_id['\"]?\s*[:=]\s*['\"]?(\d+)")


def get_sports():
    """Every sport, with today's scheduled and live counts.

    The id/slug table is static (probed live: each /<slug>/ page prints its
    own sportId); `mc_` supplies the counters."""
    text = get_feed("mc_5.5", optional=True)
    counts = {}
    for record in P.parse_records(text):
        sport_id = P.to_int(record.get("SA"))
        if sport_id is None:
            continue
        counts[sport_id] = {"event_count": P.to_int(record.get("EC")),
                            "live_count": P.to_int(record.get("LC"))}
    sports = []
    for sport_id in sorted(refs.SPORTS):
        entry = refs.sport_ref(sport_id)
        entry["link"] = P.link(f"/{refs.SPORTS[sport_id]}/")
        entry.update(counts.get(sport_id) or {"event_count": None, "live_count": None})
        sports.append(entry)
    return {"count": len(sports), "sports": sports}


def get_countries(sport):
    """Every country that has competitions in this sport, from the sport
    page's own country menu."""
    sport_id = refs.resolve_sport(sport)
    slug = refs.sport_slug(sport_id)
    html = get_page(f"/{slug}/", expect=slug)
    soup = P.soup(html)
    seen = {}
    for anchor in soup.select(f"a[href^='/{slug}/']"):
        href = anchor.get("href") or ""
        parts = [p for p in href.split("/") if p]
        if len(parts) != 2:               # /<sport>/<country>/ only
            continue
        country_slug = parts[1]
        name = P.text_of(anchor)
        if not name or country_slug in seen:
            continue
        seen[country_slug] = {"name": name, "slug": country_slug,
                              "link": P.link(f"/{slug}/{country_slug}/")}
    countries = sorted(seen.values(), key=lambda c: c["name"])
    return {"sport": refs.sport_ref(sport_id), "count": len(countries),
            "countries": countries}


def get_tournaments(sport, country):
    """Every competition of one country in one sport, with the ids the
    tournament endpoints take.

    The country page's day feed (`c_`) names each competition with its
    tournament, stage and template ids, so this is a catalogue AND a
    resolver."""
    sport_id = refs.resolve_sport(sport)
    slug = refs.sport_slug(sport_id)
    country_slug = _country_slug(country)
    html = get_page(f"/{slug}/{country_slug}/", expect=country_slug)
    soup = P.soup(html)
    from_menu = {}
    for anchor in soup.select(f"a[href^='/{slug}/{country_slug}/']"):
        href = anchor.get("href") or ""
        parts = [p for p in href.split("/") if p]
        if len(parts) != 3:
            continue
        name = P.text_of(anchor)
        if name and parts[2] not in from_menu:
            from_menu[parts[2]] = {"name": name, "slug": parts[2],
                                   "link": P.link(f"/{slug}/{country_slug}/{parts[2]}/")}
    # A country page ships no `window.environment`; its numeric country id is
    # printed as a bare `country_id` assignment instead.
    country_id = None
    match = _COUNTRY_ID_RE.search(html or "")
    if match:
        country_id = P.to_int(match.group(1))
    if country_id is None:
        environment = P.page_environment(html)
        stages = (environment.get("stages_group") or {}).get("stages") or []
        if stages:
            country_id = P.to_int(stages[0].get("countryId"))
    if country_id:
        text = get_feed(f"c_{sport_id}_{country_id}_5_en_y_1", optional=True)
        for record in P.parse_records(text):
            if "ZA" not in record:
                continue
            competition = P.competition_ref(record)
            path = [p for p in str(record.get("ZL") or "").split("/") if p]
            key = path[2] if len(path) >= 3 else None
            if key and key in from_menu:
                from_menu[key].update({
                    "id": competition.get("id"), "stage_id": competition.get("stage_id"),
                    "template_id": competition.get("template_id"),
                    "reference": f"{competition['id']}:{competition['stage_id']}"
                    if competition.get("id") and competition.get("stage_id") else None,
                    "logo": competition.get("logo"),
                })
            elif key:
                competition["slug"] = key
                competition["reference"] = (f"{competition['id']}:{competition['stage_id']}"
                                            if competition.get("id") and competition.get("stage_id")
                                            else None)
                from_menu[key] = competition
    tournaments = sorted(from_menu.values(), key=lambda t: (t.get("name") or ""))
    return {"sport": refs.sport_ref(sport_id), "country": country_slug,
            "country_id": country_id, "count": len(tournaments),
            "tournaments": tournaments}


def _country_slug(country):
    """A country name, slug or flashscore link -> the URL slug."""
    value = str(country or "").strip()
    if not value:
        raise ValueError("country is required")
    if value.startswith(("http://", "https://", "/")):
        parts = [p for p in value.split("/")
                 if p and "flashscore" not in p and p not in ("http:", "https:")]
        if len(parts) >= 2:
            return parts[1]          # /<sport>/<country>/
        if parts:
            return parts[-1]
    return value.lower().replace(" ", "-").replace("_", "-")


def search(query, type=None, sport=None, limit=20):
    """Free-text search over teams, players and competitions.

    Returns the id AND the slug for each hit, which is what makes the rest
    of the API reachable from a name alone — player endpoints in particular
    need the slug, and this is the only place it can be looked up."""
    type_ids = list(refs.SEARCH_TYPES.values())
    if type:
        wanted = str(type).strip().lower()
        if wanted not in refs.SEARCH_TYPES:
            raise ValueError(f"type must be one of {', '.join(sorted(refs.SEARCH_TYPES))}")
        type_ids = [refs.SEARCH_TYPES[wanted]]
        if wanted == "player":
            type_ids.append(refs.SEARCH_TYPES["player-in-team"])
    # The service's own `sport-ids` filter returns nothing for a single id
    # (verified: sport-ids=2 answers an empty list where the unfiltered call
    # returns 20 hits), so the sport is applied to the results instead.
    wanted_sport = refs.resolve_sport(sport) if sport else None
    rows = get_search(query, type_ids=type_ids)
    if wanted_sport:
        rows = [r for r in rows if P.to_int((r.get("sport") or {}).get("id")) == wanted_sport]
    results = []
    for row in rows[:max(1, int(limit or 20))]:
        kind = refs.SEARCH_TYPE_NAMES.get((row.get("type") or {}).get("id"))
        entity_id = P.clean(row.get("id"))
        slug = P.clean(row.get("url"))
        sport_id = P.to_int((row.get("sport") or {}).get("id"))
        results.append({
            "id": entity_id,
            "type": "player" if kind == "player_in_team" else kind,
            "name": P.clean(row.get("name")),
            "slug": slug,
            "link": _entity_link(kind, entity_id, slug),
            "sport": refs.sport_ref(sport_id),
            "country": P.country_ref((row.get("defaultCountry") or {}).get("id"),
                                     (row.get("defaultCountry") or {}).get("name")),
            "gender": P.clean((row.get("gender") or {}).get("name")),
            "image": P.image((row.get("images") or [{}])[0].get("path")
                             if row.get("images") else None),
        })
    return {"query": query, "count": len(results), "results": results}


def _entity_link(kind, entity_id, slug):
    if not entity_id:
        return None
    if kind in ("team",):
        # A guessed slug would be a dead link (parsers.team_link).
        return P.team_link(entity_id, slug)
    if kind in ("player", "player_in_team"):
        return P.link(f"/player/{slug}/{entity_id}/") if slug else None
    return None


def resolve(link):
    """Turn any flashscore.com URL into the ids this API takes.

    Handy as a first call for callers holding a page URL: it says what kind
    of entity that is, what id it has, and which endpoints accept it."""
    value = str(link or "").strip()
    if not value:
        raise ValueError("link is required")
    # The URL's own leading segment says what kind of page it is; trying the
    # resolvers in turn would let a player link match the team resolver,
    # since both are /<segment>/<slug>/<id>/.
    segments = [p for p in value.split("?")[0].split("/")
                if p and "flashscore" not in p and p not in ("http:", "https:")]
    kind_by_segment = {"match": "match", "team": "team", "player": "player"}
    kind = kind_by_segment.get(segments[0]) if segments else None
    if kind == "match" or (kind is None and refs._ID_RE.match(value)):
        match_id = refs.resolve_match(value)
        return {"type": "match", "id": match_id, "slug": None, "reference": match_id,
                "link": P.match_link(match_id)}
    if kind in ("team", "player"):
        resolved = (refs.resolve_team if kind == "team" else refs.resolve_player)(value)
        if kind == "team" and not resolved.get("slug"):
            resolved = {"id": resolved["id"], "slug": lookup.team_slug(resolved)}
        return {"type": kind, "id": resolved["id"], "slug": resolved.get("slug"),
                "reference": resolved["id"],
                "link": _entity_link(kind, resolved["id"], resolved.get("slug"))}
    try:
        tournament = refs.resolve_tournament(value)
    except ValueError:
        raise ValueError("that does not look like a flashscore.com match, team, player "
                         "or competition link")
    context = lookup.tournament_context(tournament)
    reference = (f"{context['tournament_id']}:{context['stage_id']}"
                 if context.get("tournament_id") and context.get("stage_id") else None)
    return {
        "type": "tournament",
        "id": context.get("tournament_id"),
        "stage_id": context.get("stage_id"),
        "template_id": context.get("template_id"),
        "season_id": context.get("season_id"),
        "reference": reference,
        "name": context.get("name"),
        "sport": refs.sport_ref(context.get("sport_id")),
        "link": P.link(context.get("path")),
    }
