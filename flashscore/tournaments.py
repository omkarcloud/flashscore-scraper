"""Competition endpoints.

`tournament` is one param taking either a competition link
("/football/spain/laliga/", a past season "/football/spain/laliga-2024-2025/",
or the full URL) or the "<tournament_id>:<stage_id>" pair those links resolve
to. The link form is richer: results and fixtures are paginated by feeds keyed
on a TEMPLATE id and a NUMERIC season id that appear only inside the page, so
those two endpoints require it and say so when given a bare pair.

Page one of results and fixtures is embedded in the competition page itself
(`cjs.initialFeeds`), so it costs no extra request; later pages come from
`tr_`/`tf_`, whose page numbers are 0-based (page 1 here is offset 0) and
whose sport and country segments the upstream ignores.
"""
from . import lookup
from . import parsers as P
from . import refs
from .fetch import get_feed, get_graphql_optional, run_parallel
from .matches import (article_from_payload, standings_payload,
                      top_scorers_payload)


def _context(tournament):
    return lookup.tournament_context(tournament)


def _identity(context):
    """The competition block every response leads with."""
    return {
        "id": context.get("tournament_id"),
        "stage_id": context.get("stage_id"),
        "template_id": context.get("template_id"),
        "name": context.get("name"),
        "link": P.link(context.get("path")),
        "season": context.get("season_name"),
        "sport": refs.sport_ref(context.get("sport_id")),
    }


def get_details(tournament):
    """The competition card: current season, stage list, dates and winner.

    `lph` is the persisted query the site uses for the season switcher; it
    carries the stage timeline and every archived season with its ids."""
    context = lookup.require_pair(_context(tournament), "tournament details")
    payload = get_graphql_optional("lph", {"tournamentId": context["tournament_id"],
                                           "tournamentStageId": context["stage_id"],
                                           "projectId": 2})
    holder = (payload or {}).get("getTournamentSeasons") or {}
    requested = holder.get("requested") or {}
    stages = (requested.get("tournamentStages") or {}).get("requested") or {}
    stage = stages.get("tournamentStage") or {}
    names = stages.get("leagueNames") or {}
    images = requested.get("images") or []
    result = _identity(context)
    result.update({
        "id": P.clean_id(requested.get("tournamentId")) or result["id"],
        "template_id": P.clean_id(requested.get("tournamentTemplateId")) or result["template_id"],
        "name": result["name"] or P.clean(names.get("tournament")),
        "country": P.clean(names.get("country")),
        "stage_name": P.clean(names.get("stage")),
        "season": P.clean(f"{requested.get('start')}/{requested.get('end')}"
                          if requested.get("start") and requested.get("end")
                          and requested["start"] != requested["end"]
                          else requested.get("start")) or result["season"],
        "season_start_year": P.to_int(requested.get("start")),
        "season_end_year": P.to_int(requested.get("end")),
        "is_current_season": bool(requested.get("isCurrent")),
        "starts_at": P.timestamp(stage.get("stageStartEstimated")),
        "ends_at": P.timestamp(stage.get("stageEndEstimated")),
        "is_final_stage": bool(stage.get("isFinal")),
        "logo": P.image((images[0] or {}).get("path")) if images else None,
        "available_tabs": [t for t in stage.get("tournamentStageTabIds") or []],
        "winners": [P.team_ref(w.get("id"), w.get("name"), slug=P.clean(w.get("url")),
                               logo=((w.get("images") or [{}])[0] or {}).get("path"))
                    for w in requested.get("winners") or []],
        "stages": context.get("stages") or [],
    })
    return result


def get_seasons(tournament):
    """Every season of this competition with the id pair each one needs."""
    context = lookup.require_pair(_context(tournament), "tournament seasons")
    payload = get_graphql_optional("lph", {"tournamentId": context["tournament_id"],
                                           "tournamentStageId": context["stage_id"],
                                           "projectId": 2})
    holder = (payload or {}).get("getTournamentSeasons") or {}
    seasons = []
    entries = [holder.get("requested") or {}] + list(holder.get("other") or [])
    for entry in entries:
        if not entry.get("tournamentId"):
            continue
        stages = entry.get("tournamentStages") or {}
        stage_entries = ([stages.get("requested")] if stages.get("requested") else []) \
            + list(stages.get("other") or [])
        stage_id = None
        for stage in stage_entries:
            if stage and stage.get("id"):
                stage_id = stage["id"]
                break
        start, end = entry.get("start"), entry.get("end")
        seasons.append({
            "name": f"{start}/{end}" if start and end and start != end else P.clean(start),
            "tournament_id": P.clean_id(entry.get("tournamentId")),
            "stage_id": P.clean_id(stage_id),
            "reference": f"{entry.get('tournamentId')}:{stage_id}" if stage_id else None,
            "start_year": P.to_int(start),
            "end_year": P.to_int(end),
            "is_current": bool(entry.get("isCurrent")),
            "winners": [P.team_ref(w.get("id"), w.get("name"), slug=P.clean(w.get("url")),
                                   logo=((w.get("images") or [{}])[0] or {}).get("path"))
                        for w in entry.get("winners") or []],
        })
    result = _identity(context)
    result["count"] = len(seasons)
    result["seasons"] = seasons
    return result


def _paged_matches(context, kind, page):
    """Page one comes free with the page; later pages hit `tr_`/`tf_`."""
    lookup.require_pagination_ids(context)
    embedded = (context.get("feeds") or {}).get(kind) or {}
    total = embedded.get("total_count")
    if page <= 1:
        text = embedded.get("text") or ""
    else:
        prefix = "tr" if kind == "results" else "tf"
        text = get_feed(
            f"{prefix}_{context['sport_id']}_{context.get('country_id') or 0}_"
            f"{context['template_id']}_{context['season_id']}_{page - 1}_5_en_1",
            optional=True)
    matches = P.match_list(text, sport_id=context.get("sport_id"))
    for match in matches:
        match.pop("competition", None)
    per_page = len(matches) if page <= 1 else max(len(matches), 1)
    first_page_size = len(P.match_list(embedded.get("text") or "")) or per_page
    result = _identity(context)
    result["count"] = len(matches)
    result["matches"] = matches
    result["pagination"] = P.pagination(page, first_page_size or per_page, total)
    return result


def get_results(tournament, page=1):
    """Finished matches of this season, newest first."""
    return _paged_matches(_context(tournament), "results", page)


def get_fixtures(tournament, page=1):
    """Upcoming matches of this season."""
    return _paged_matches(_context(tournament), "fixtures", page)


def get_standings(tournament, type="overall", line="all"):
    """Any of the tables this competition publishes — the league table, its
    home/away splits, the form table, over/under totals, or half-time
    /full-time. `/flashscore/tournaments/standings-types` lists which of them
    a given competition actually has."""
    context = lookup.require_pair(_context(tournament), "standings")
    tab = refs.standings_tab(type, line)
    text = get_feed(f"to_{context['tournament_id']}_{context['stage_id']}_{tab}",
                    optional=True)
    result = _identity(context)
    result.update(standings_payload(text, type, line))
    return result


def get_standings_types(tournament):
    """`tx_` — the tab ids this competition publishes, mapped back to the
    `type`/`line` values the standings endpoint takes."""
    context = lookup.require_pair(_context(tournament), "standings types")
    text = get_feed(f"tx_{context['tournament_id']}_{context['stage_id']}", optional=True)
    records = P.parse_records(text)
    tabs = []
    for record in records:
        if record.get("TB"):
            tabs = [t.strip() for t in record["TB"].split(",") if t.strip() and t.strip() != "-1"]
            break
    reverse = {}
    for name, template in refs.STANDINGS_TYPES.items():
        if "{line}" in template:
            for label, slot in refs.OVER_UNDER_LINES.items():
                reverse[template.format(line=slot)] = (name, label)
        else:
            reverse[template] = (name, None)
    available = []
    for tab in tabs:
        name, label = reverse.get(tab, (None, None))
        if name:
            available.append({"type": name, "line": label, "tab": tab})
    result = _identity(context)
    result["count"] = len(available)
    result["standings_types"] = available
    return result


def get_top_scorers(tournament):
    """`tt_` — the competition's scoring chart."""
    context = lookup.require_pair(_context(tournament), "top scorers")
    text = get_feed(f"tt_{context['tournament_id']}_{context['stage_id']}", optional=True)
    result = _identity(context)
    result.update(top_scorers_payload(text))
    return result


def get_draw(tournament):
    """`dr_` — the knockout bracket, for cups and tennis events.

    Participants arrive once in an index map (`PA`: "0_Zverev A.|1_Sonego L.")
    and every tie then refers to them by index."""
    context = lookup.require_pair(_context(tournament), "draw")
    text = get_feed(f"dr_{context['tournament_id']}_{context['stage_id']}", optional=True)
    records = P.parse_records(text)
    names = {}
    for record in records:
        if record.get("PA"):
            for entry in record["PA"].split("|"):
                index, _, name = entry.partition("_")
                if index.strip().isdigit():
                    names[index.strip()] = P.clean(name)
            break
    ties = []
    for record in records:
        if "AA" not in record and "RQ" not in record:
            continue
        home_index = P.clean(record.get("HP"))
        away_index = P.clean(record.get("AP"))
        match_id = P.clean(record.get("AA"))
        ties.append({
            "id": match_id,
            "link": P.match_link(match_id),
            "round": P.clean(record.get("RI")),
            "start_time": P.timestamp(record.get("ES")),
            "date": P.date_of(record.get("ES")),
            "status": refs.status_of(P.to_int(record.get("AC"))),
            "home": {"name": names.get(home_index), "seed": P.clean(record.get("HI"))},
            "away": {"name": names.get(away_index), "seed": P.clean(record.get("AI"))},
            "home_score": P.to_int(record.get("AT")),
            "away_score": P.to_int(record.get("AU")),
            "winner": {"H": "home", "A": "away"}.get(P.clean(record.get("AE"))),
        })
    result = _identity(context)
    result["participant_count"] = len(names)
    result["count"] = len(ties)
    result["draw"] = ties
    return result


def get_news(tournament):
    """`nl` lists the articles tagged with this competition; `nah` expands
    each. The tag id is the template id plus the site's own suffix."""
    context = _context(tournament)
    template = context.get("template_id")
    if not template:
        raise ValueError("competition news needs a competition LINK "
                         "(e.g. /football/spain/laliga/)")
    layout = get_graphql_optional("nl", {"projectId": 2, "tagId": template + "CdnS0XT8"})
    holder = (layout or {}).get("findNewsLayoutByProjectId") or {}
    ids = []
    for section in holder.get("sections") or []:
        for article in section.get("articles") or []:
            if article.get("id") and article["id"] not in ids:
                ids.append(article["id"])
    articles = []
    if ids:
        payloads = run_parallel([
            (lambda a=article_id: get_graphql_optional("nah", {"articleId": a}))
            for article_id in ids[:12]])
        for payload in payloads:
            article = article_from_payload(payload)
            if article:
                articles.append(article)
    result = _identity(context)
    result["count"] = len(articles)
    result["articles"] = articles
    return result


def get_archive(tournament):
    """The archive page's own season list: names, page links and winners.

    Overlaps `seasons` on purpose — this one is what the site shows a reader
    (including the winner's crest), while `seasons` carries the id pairs the
    other endpoints take."""
    context = _context(tournament)
    if not context.get("path"):
        raise ValueError("archive needs a competition LINK (e.g. /football/spain/laliga/)")
    html = lookup.tournament_page(context["path"], tab="archive")
    seasons = []
    for entry in P.archive_seasons(html):
        winners = entry.get("winners")
        winner_rows = list(winners.values()) if isinstance(winners, dict) else (winners or [])
        seasons.append({
            "name": P.clean(entry.get("name")),
            "link": P.link(entry.get("url")),
            "winners": [P.team_ref(_id_from_link(w.get("url")), w.get("name"),
                                   link_path=w.get("url"), logo=w.get("logoId"))
                        for w in winner_rows if isinstance(w, dict)],
        })
    result = _identity(context)
    result["count"] = len(seasons)
    result["seasons"] = seasons
    return result


def _id_from_link(url):
    parts = [p for p in str(url or "").split("/") if p]
    for part in reversed(parts):
        if len(part) in range(6, 11) and part.isalnum():
            return part
    return None
