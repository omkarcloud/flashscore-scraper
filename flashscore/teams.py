"""Team endpoints. `team` takes a bare Flashscore team id or a
flashscore.com team link — a bare id is enough for everything here, because
the team's transfer feed doubles as the id-to-slug resolver its page needs
(lookup.team_slug).

Results and fixtures page one ride along inside the team page
(`cjs.initialFeeds`), so the common case is one request; later pages come
from `pr_`/`pf_`, which are 0-based and DO care about the sport id (the
country segment they also carry is ignored).
"""
from . import lookup
from . import parsers as P
from . import refs
from .fetch import get_feed, run_parallel
from .matches import standings_payload, top_scorers_payload


def _ref(team):
    return refs.resolve_team(team)


def _identity(context):
    return {
        "id": context.get("id"),
        "name": context.get("name"),
        "link": P.link(f"/team/{context.get('slug')}/{context.get('id')}/")
        if context.get("slug") else None,
        "sport": refs.sport_ref(context.get("sport_id")),
        "country": context.get("country"),
        "logo": context.get("logo"),
    }


def get_details(team):
    """The team card: name, country, crest, venue and the competitions it is
    currently entered in."""
    context = lookup.team_context(_ref(team))
    environment = P.page_environment(context["html"])
    stages = (environment.get("stages_group") or {}).get("stages") or []
    result = _identity(context)
    result.update({
        "venue": context.get("venue"),
        "competitions": [{"stage_id": P.clean_id(s.get("id")), "name": P.clean(s.get("name")),
                          "type": P.clean(s.get("statsType")),
                          "country_id": P.to_int(s.get("countryId"))} for s in stages],
        "seasons": [{"name": P.clean(s.get("name")),
                     "tournament_id": s.get("tournament_id"), "stage_id": s.get("stage_id"),
                     "reference": f"{s['tournament_id']}:{s['stage_id']}"
                     if s.get("tournament_id") and s.get("stage_id") else None}
                    for s in lookup._seasons(environment)],
    })
    return result


def _paged_matches(team, kind, page):
    context = lookup.team_context(_ref(team))
    embedded = (context.get("feeds") or {}).get(kind) or {}
    total = embedded.get("total_count")
    if page <= 1:
        text = embedded.get("text") or ""
    else:
        prefix = "pr" if kind == "results" else "pf"
        text = get_feed(f"{prefix}_{context['sport_id']}_0_{context['id']}_{page - 1}_5_en_1",
                        optional=True)
    matches = P.match_list(text, sport_id=context.get("sport_id"))
    first_page_size = len(P.match_list(embedded.get("text") or "")) or len(matches) or 1
    result = _identity(context)
    result["count"] = len(matches)
    result["competitions"] = P.group_by_competition(matches)
    result["pagination"] = P.pagination(page, first_page_size, total)
    return result


def get_results(team, page=1):
    """Finished matches, newest first, grouped by competition."""
    return _paged_matches(team, "results", page)


def get_fixtures(team, page=1):
    """Upcoming matches, grouped by competition."""
    return _paged_matches(team, "fixtures", page)


def get_squad(team):
    """The squad page: every player by position group, with the season
    statistics the site shows.

    The page carries one table per competition filter (LaLiga / Champions
    League / Total for a club in both). The last one is the complete roster,
    so it is the primary list; the others ride along as `by_competition`."""
    context = lookup.team_context(_ref(team))
    html = lookup.team_page(_ref(team), tab="squad")
    soup = P.soup(html)
    filters = [P.clean(f.get_text(" ", strip=True))
               for f in soup.select("div.filter.lineup__filter a, div.lineup__filter a")]
    if not filters:
        block = soup.select_one("div.filter.lineup__filter, div.lineup__filter")
        filters = [P.clean(t) for t in (block.get_text("|", strip=True).split("|") if block else [])]
    filters = [f for f in filters if f]
    tables = soup.select("div.squad-table.profileTable")
    tables_by_name = {}
    for index, table in enumerate(tables):
        name = filters[index] if index < len(filters) else f"table_{index + 1}"
        tables_by_name[name] = _squad_groups(table)
    primary_name = filters[-1] if filters else (list(tables_by_name)[-1] if tables_by_name else None)
    result = _identity(context)
    result["competition_filter"] = primary_name
    result["competition_filters"] = filters or None
    result["groups"] = tables_by_name.get(primary_name) or []
    result["player_count"] = sum(len(g["players"]) for g in result["groups"])
    result["by_competition"] = [{"competition": name, "groups": groups}
                                for name, groups in tables_by_name.items()
                                if name != primary_name] or None
    return result


def _squad_groups(table):
    """One squad table -> [{name, players}] in the page's own order.

    The statistics columns carry no per-cell headings on the page (only
    icons), so they are named from the site's fixed column order: matches
    played, minutes, goals, assists, yellow cards, red cards."""
    groups = []
    current = None
    for node in table.find_all(["div"], recursive=True):
        classes = node.get("class") or []
        if "lineupTable__title" in classes:
            current = {"name": P.text_of(node), "players": []}
            groups.append(current)
            continue
        if "lineupTable__row" not in classes:
            continue
        if current is None:
            current = {"name": None, "players": []}
            groups.append(current)
        current["players"].append(_squad_player(node))
    return [g for g in groups if g["players"]]


_SQUAD_CELLS = {
    "jersey": "shirt_number", "age": "age", "matchesPlayed": "matches_played",
    "minutesPlayed": "minutes_played", "goal": "goals", "assist": "assists",
    "yellowCard": "yellow_cards", "redCard": "red_cards",
}


def _squad_player(row):
    values = {}
    for cell in row.select("div.lineupTable__cell"):
        classes = [c for c in (cell.get("class") or [])
                   if c.startswith("lineupTable__cell--") and not c.endswith("--gray")]
        for cls in classes:
            key = cls.replace("lineupTable__cell--", "")
            if key in _SQUAD_CELLS and key not in values:
                values[key] = P.to_int(cell.get_text(" ", strip=True))
    anchor = row.select_one("a.lineupTable__cell--name")
    flag = row.select_one("[class*=lineupTable__cell--flag]")
    href = anchor.get("href") if anchor is not None else None
    player_id = None
    slug = None
    if href:
        parts = [p for p in href.split("/") if p]
        if len(parts) >= 3:
            slug, player_id = parts[-2], parts[-1]
    out = {
        "player": P.player_ref(player_id, P.text_of(anchor), slug=slug, link_path=href),
        "country": P.clean(flag.get("title")) if flag is not None else None,
    }
    for key, name in _SQUAD_CELLS.items():
        out[name] = values.get(key)
    return out


def get_transfers(team, direction="all", page=1):
    """`tetr_` — arrivals and departures with the date, fee and both clubs."""
    ref = _ref(team)
    tab = refs.TRANSFER_TABS.get(str(direction or "all").lower())
    if tab is None:
        raise ValueError("direction must be one of all, in, out")
    context, text = run_parallel([
        lambda: lookup.team_context(ref),
        lambda: get_feed(f"tetr_{ref['id']}_{tab}_{max(1, int(page))}", optional=True),
    ])
    tree = P.parse_tree(text)
    transfers = []
    for node in P.iter_nodes(tree, {"RTT"}):
        clubs = [n for n in node["children"] if n["type"] == "TEA"]
        player_node = next((n for n in node["children"] if n["type"] == "PLA"), None)
        sides = {}
        for club in clubs:
            slot = P.to_int(club["props"].get("TEAT"))
            sides[slot] = P.team_ref(_id_from_path(club["props"].get("TURL")),
                                     club["props"].get("VA"),
                                     link_path=club["props"].get("TURL"),
                                     logo=club["props"].get("PI"))
        player = None
        if player_node is not None:
            props = player_node["props"]
            player = P.player_ref(props.get("PID"), props.get("VA"),
                                  link_path=_player_path(props.get("PURL"), props.get("PID")))
            country = P.clean(props.get("CRNA"))
        else:
            country = None
        transfers.append({
            "date": P.date_of(node["props"].get("DATE")),
            "direction": P.clean(node["props"].get("TD")),
            "type": P.clean(node["props"].get("TT")),
            "fee": P.money(node["props"].get("TJ")),
            "player": player,
            "player_country": country,
            "from_team": sides.get(1),
            "to_team": sides.get(2),
        })
    result = _identity(context)
    result["direction"] = str(direction or "all").lower()
    result["count"] = len(transfers)
    result["transfers"] = transfers
    return result


def _id_from_path(path):
    parts = [p for p in str(path or "").split("/") if p]
    for part in reversed(parts):
        if 6 <= len(part) <= 10 and part.isalnum():
            return part
    return None


def _player_path(path, player_id):
    """The transfers feed prints player links under /team/<slug>/<id>/ — a
    long-standing upstream quirk. Rewrite them to the real player page."""
    parts = [p for p in str(path or "").split("/") if p]
    if len(parts) >= 3 and parts[0] == "team" and parts[-1] == (player_id or ""):
        return f"/player/{parts[-2]}/{parts[-1]}/"
    return path


def get_standings(team, type="overall", line="all"):
    """The table of the competition this team is currently in."""
    context = lookup.team_context(_ref(team))
    environment = P.page_environment(context["html"])
    seasons = lookup._seasons(environment)
    stages = (environment.get("stages_group") or {}).get("stages") or []
    tournament_id = seasons[0].get("tournament_id") if seasons else None
    stage_id = (seasons[0].get("stage_id") if seasons else None) or \
        (P.clean_id(stages[0].get("id")) if stages else None)
    if not tournament_id or not stage_id:
        raise ValueError("this team has no league table (cup-only or friendly squads "
                         "have none) — use /flashscore/tournaments/standings instead")
    tab = refs.standings_tab(type, line)
    text = get_feed(f"to_{tournament_id}_{stage_id}_{tab}", optional=True)
    result = _identity(context)
    result["competition"] = {"id": tournament_id, "stage_id": stage_id,
                             "name": P.clean(stages[0].get("name")) if stages else None}
    result.update(standings_payload(text, type, line))
    return result


def get_top_scorers(team):
    """The scoring chart of the competition this team is currently in."""
    context = lookup.team_context(_ref(team))
    environment = P.page_environment(context["html"])
    seasons = lookup._seasons(environment)
    stages = (environment.get("stages_group") or {}).get("stages") or []
    tournament_id = seasons[0].get("tournament_id") if seasons else None
    stage_id = (seasons[0].get("stage_id") if seasons else None) or \
        (P.clean_id(stages[0].get("id")) if stages else None)
    if not tournament_id or not stage_id:
        raise ValueError("this team has no competition scoring chart — "
                         "use /flashscore/tournaments/top-scorers instead")
    text = get_feed(f"tt_{tournament_id}_{stage_id}", optional=True)
    result = _identity(context)
    result["competition"] = {"id": tournament_id, "stage_id": stage_id}
    result.update(top_scorers_payload(text))
    return result


def get_news(team):
    """`pnf_` — the headline feed the team page shows, with its sources."""
    ref = _ref(team)
    context, text = run_parallel([
        lambda: lookup.team_context(ref),
        lambda: get_feed(f"pnf_{ref['id']}", optional=True),
    ])
    result = _identity(context)
    result.update(news_payload(text))
    return result


def news_payload(text):
    """A `pnf_` headline feed -> [{title, link, image, published_at, source}]."""
    tree = P.parse_tree(text)
    articles = []
    for row in P.iter_nodes(tree, {"RW"}):
        props = {}
        for node in P.iter_nodes(row, {"CO"}):
            props.update(node["props"])
        title = P.clean(props.get("TL"))
        if not title:
            continue
        articles.append({
            "title": title,
            "link": P.link(props.get("LI")),
            "image": P.clean(props.get("IU")),
            "published_at": P.timestamp(props.get("PU")),
            "source": P.clean(props.get("NA")),
        })
    return {"count": len(articles), "articles": articles}
