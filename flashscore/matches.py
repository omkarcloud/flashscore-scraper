"""Match endpoints: the day/live lists and every tab of a match detail.

`match` is one param that takes a bare Flashscore id or any flashscore.com
match link (refs.resolve_match). The sport id embedded in the detail feed
names (`dc_<sport>_<id>`, `df_*_<sport>_<id>`) is ignored by the upstream —
verified against sport ids 1, 2, 3 and 99 on the same match — so no sport
lookup is needed and a bare match id is a complete reference. `_SPORT_SLOT`
below is the placeholder those names still require.

The core feed `dc_<id>` publishes `DX`, the comma list of tabs that match
actually has (MR report, ST statistics, LI line-ups, PMS player statistics,
LC commentary, HH head-to-head, SCR missing players, OD odds, LT/TA league
table, TTS top scorers, MH point-by-point, DR draw, NF news, TV broadcasts).
Every detail endpoint returns that list as `available_tabs` so a caller can
tell "this match has no line-ups" from "the request failed".
"""
from . import parsers as P
from . import refs
from .fetch import (FlashscoreNotFound, gather, get_feed, get_graphql,
                    get_graphql_optional, get_page, run_parallel)

_SPORT_SLOT = 1          # ignored by the upstream; any integer works

# `DX` token -> the public tab name a caller sees.
_TABS = {
    "MR": "report", "ST": "statistics", "PS": "player_statistics",
    "PMS": "player_statistics", "LI": "lineups", "PLI": "predicted_lineups",
    "LC": "commentary", "MC": "commentary", "HH": "h2h", "OD": "odds",
    "TTS": "top_scorers", "SCR": "missing_players", "LT": "standings",
    "TA": "standings", "NF": "news", "MH": "point_by_point", "DR": "draw",
    "TV": "broadcasts", "DTF": "draw", "SP": "summary", "HITO": "summary",
}


def _mid(match):
    return refs.resolve_match(match)


def _feed(name, *, optional=True):
    return get_feed(name, optional=optional)


# ---- lists --------------------------------------------------------------------------

def _timezone_offset(timezone_name):
    """The feeds take a whole-hour offset, not an IANA name. Callers give a
    name (Europe/Berlin) or an offset (+2, -4); both end as an integer."""
    if timezone_name in (None, ""):
        return 0
    text = str(timezone_name).strip()
    try:
        return max(-12, min(12, int(float(text))))
    except ValueError:
        pass
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime
        offset = datetime.now(ZoneInfo(text)).utcoffset()
        return max(-12, min(12, int(round(offset.total_seconds() / 3600)))) if offset else 0
    except Exception:
        raise ValueError(f"unknown timezone '{timezone_name}' — use an IANA name "
                         "(Europe/Berlin) or a whole-hour offset (-4)")


def _day_offset(day, date):
    """The list feed exposes a sliding ±7-day window as an integer offset;
    anything outside it answers with an empty body, so it is rejected here
    with a message instead of returning nothing."""
    if date:
        from datetime import date as _date, datetime
        try:
            target = datetime.strptime(str(date), "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD")
        delta = (target - _date.today()).days
        if not -7 <= delta <= 7:
            raise ValueError("Flashscore's schedule feed only covers today ±7 days; "
                             "for older matches use /flashscore/tournaments/results "
                             "or /flashscore/teams/results")
        return delta
    return int(day or 0)


def get_matches(sport, day=0, date=None, timezone=None, status=None, page=None):
    """Every match of one sport on one day, grouped by competition.

    Golf, cycling, racing and the winter sports have no head-to-head events —
    one row there is a competitor's place in a field — so those come back as
    `leaderboard` entries under each event instead of `matches`, and the
    response says which shape it used."""
    sport_id = refs.resolve_sport(sport)
    offset = _day_offset(day, date)
    tz = _timezone_offset(timezone)
    text = get_feed(f"f_{sport_id}_{offset}_{tz}_en_1", optional=True)
    rows = P.match_list(text, sport_id=sport_id, leaderboard=True)
    if status:
        rows = [r for r in rows if r.get("status") == status]
    is_leaderboard = bool(rows) and "competitor" in rows[0]
    groups = P.group_by_competition(rows)
    if is_leaderboard:
        headers = {}
        for record in P.parse_records(text):
            if "ZA" in record:
                reference = P.clean_id(record.get("ZC")) or P.clean(record.get("ZA"))
                headers[reference] = record
        for group in groups:
            group["leaderboard"] = group.pop("matches")
            group["entry_count"] = len(group["leaderboard"])
            competition = group.get("competition") or {}
            header = headers.get(competition.get("stage_id") or competition.get("name"))
            meta = P.event_meta(header)
            if meta:
                group["event"] = meta
    return {
        "sport": refs.sport_ref(sport_id),
        "format": "leaderboard" if is_leaderboard else "matches",
        "day_offset": offset,
        "timezone_offset": tz,
        "match_count": sum(len(g.get("matches") or g.get("leaderboard") or []) for g in groups),
        "competition_count": len(groups),
        "competitions": groups,
    }


def get_live_matches(sport, timezone=None):
    """The same feed filtered to matches in progress."""
    result = get_matches(sport, day=0, timezone=timezone, status="live")
    result.pop("day_offset", None)
    return result


def get_live_updates(sport):
    """The tiny diff feeds the site polls every few seconds: only the rows
    whose score, stage or serve changed since the last publish. Intended for
    callers that already hold a list and want the deltas cheaply."""
    sport_id = refs.resolve_sport(sport)
    changes, extra = run_parallel([
        lambda: get_feed(f"r_{sport_id}_1", optional=True),
        lambda: get_feed(f"u_{sport_id}_1", optional=True),
    ])
    updates = {}
    for text in (changes, extra):
        for record in P.parse_records(text):
            match_id = P.clean(record.get("AA"))
            if not match_id:
                continue
            entry = updates.setdefault(match_id, {"id": match_id,
                                                  "link": P.match_link(match_id)})
            stage_id = P.to_int(record.get("AC"))
            home, away = P.to_int(record.get("AG")), P.to_int(record.get("AH"))
            if stage_id is not None:
                entry["stage"] = refs.stage_name(stage_id)
                entry["stage_id"] = stage_id
            if record.get("AB") is not None:
                entry["status"] = refs.status_of(P.to_int(record.get("AB")))
            if home is not None:
                entry["home_score"] = home
            if away is not None:
                entry["away_score"] = away
            if record.get("WA") or record.get("WB"):
                entry["current_game"] = {
                    "home_points": P.clean(record.get("WA")),
                    "away_points": P.clean(record.get("WB")),
                    "serving": {1: "home", 2: "away"}.get(P.to_int(record.get("WC"))),
                }
            # A diff row restates only what changed, so a period appears
            # half-filled; keep just the complete ones.
            periods = [p for p in P._period_scores(record, sport_id)
                       if p.get("home") is not None and p.get("away") is not None]
            if periods:
                entry["periods"] = periods
    return {"sport": refs.sport_ref(sport_id), "count": len(updates),
            "updates": list(updates.values())}


# ---- match core ---------------------------------------------------------------------

def _core(match_id):
    """`dc_` — status, kick-off, current score and the available-tab list.
    A 404 here is the definitive "no such match".

    An id that exists but has an EMPTY core belongs to a leaderboard sport
    (golf, cycling, racing, the winter sports), where a "match" is really one
    competitor's place in a field and none of these tabs exist — say so
    rather than reporting the id as missing."""
    try:
        text = get_feed(f"dc_{_SPORT_SLOT}_{match_id}", optional=False)
    except FlashscoreNotFound:
        raise FlashscoreNotFound(f"match {match_id} not found")
    if not text:
        raise FlashscoreNotFound(
            f"{match_id} has no match detail — golf, cycling, racing and the winter "
            "sports have no head-to-head events, so their standings come from "
            "/flashscore/matches (which returns a leaderboard for those sports)")
    records = P.parse_records(text)
    return records[0] if records else {}


def _available_tabs(core):
    tokens = [t.strip() for t in (core.get("DX") or "").split(",") if t.strip()]
    seen = []
    for token in tokens:
        name = _TABS.get(token)
        if name and name not in seen:
            seen.append(name)
    return seen


def _core_summary(match_id, core):
    """The shared header every detail endpoint repeats, so each response
    stands on its own."""
    bucket = P.to_int(core.get("DA"))
    stage_id = P.to_int(core.get("DB"))
    sport_id = P.to_int(core.get("DV"))
    return {
        "id": match_id,
        "link": P.match_link(match_id),
        "sport": refs.sport_ref(sport_id),
        "status": refs.status_of(bucket),
        "stage": refs.stage_name(stage_id) or refs.status_of(bucket),
        "stage_id": stage_id,
        "start_time": P.timestamp(core.get("DC")),
        "date": P.date_of(core.get("DC")),
        "updated_at": P.timestamp(core.get("DD")),
        "home_score": P.to_int(core.get("DG")),
        "away_score": P.to_int(core.get("DH")),
        "winner": {"H": "home", "A": "away"}.get(P.clean(core.get("DJ"))),
        "available_tabs": _available_tabs(core),
    }


def get_details(match):
    """The full match card: teams, competition, venue, status, scores, and
    the tab list. The page is the only surface carrying the participants and
    the competition together, so this is one HTML fetch plus nothing."""
    match_id = _mid(match)
    try:
        html = get_page(f"/match/{match_id}/", expect=match_id)
    except FlashscoreNotFound:
        # A leaderboard-sport id has no match page either; _core() explains
        # that case properly, and re-raises for an id that truly is unknown.
        _core(match_id)
        raise
    environment = P.page_environment(html)
    if not environment:
        _core(match_id)
        raise FlashscoreNotFound(f"match {match_id} not found")
    core = {}
    for entry in environment.get("common_feed") or []:
        if isinstance(entry, dict):
            core.update({k: v for k, v in entry.items()})
    core = {k: ("" if v is None else str(v)) for k, v in core.items()}
    summary = _core_summary(match_id, core)
    sport_id = P.to_int(environment.get("sport_id")) or (summary.get("sport") or {}).get("id")
    summary["sport"] = refs.sport_ref(sport_id) or summary.get("sport")
    header = environment.get("header") or {}
    tournament = header.get("tournament") or {}
    participants = environment.get("participantsData") or {}
    info = environment.get("event_info") or {}
    country = P.country_ref(header.get("country_id"), header.get("country_name"))
    name = P.clean(tournament.get("tournament")) or None
    round_name = None
    if name and " - " in name:
        name, _, round_name = name.partition(" - ")
        name, round_name = P.clean(name), P.clean(round_name)
    summary.update({
        "competition": {
            "name": name,
            "link": P.link(tournament.get("link")),
            "country": country,
        },
        "round": round_name,
        "home": _page_participant(participants.get("home"), country),
        "away": _page_participant(participants.get("away"), country),
        "is_national": bool(participants.get("is_national")),
        "has_statistics": bool(info.get("hasStats")),
        "has_player_statistics": bool(info.get("hasPlayerStats")),
        "has_missing_players": bool(info.get("hasMissingPlayers")),
        "audio_stream_link": P.clean(core.get("QJ")) or P.clean(core.get("QQ")),
    })
    return summary


def _page_participant(entries, fallback_country=None):
    """`participantsData.home` is a list — doubles/relay events carry two.

    The page gives each participant a country ID but leaves the name empty for
    club sides, so the competition's own country supplies it when the IDs agree."""
    if not entries:
        return None
    people = []
    for entry in entries:
        country = P.country_ref(entry.get("country_id"), entry.get("country"))
        if country and not country.get("name") and fallback_country \
                and country.get("id") == fallback_country.get("id"):
            country = dict(country, name=fallback_country.get("name"))
        people.append({
            "id": P.clean(entry.get("id")),
            "name": P.clean(entry.get("name")),
            "short_name": P.clean(entry.get("three_char_name")),
            "link": P.link(entry.get("detail_link")),
            "logo": P.clean(entry.get("image_path")),
            "country": country,
            "is_team": bool(entry.get("is_team")),
        })
    first = dict(people[0])
    if len(people) > 1:
        first["name"] = " / ".join(p["name"] for p in people if p.get("name")) or first["name"]
        first["participants"] = people
    return first


# ---- detail tabs ----------------------------------------------------------------------

def _with_core(match_id, extra):
    core = _core(match_id)
    result = _core_summary(match_id, core)
    result.update(extra)
    return result


def get_summary(match):
    """`df_sui_` — the incident list (goals, cards, substitutions, VAR) with
    the running score at the start of each period."""
    match_id = _mid(match)
    core, text = run_parallel([lambda: _core(match_id),
                               lambda: _feed(f"df_sui_{_SPORT_SLOT}_{match_id}")])
    periods = []
    current = None
    for record in P.parse_records(text):
        if "AC" in record and "III" not in record:
            current = {"period": P.clean(record.get("AC")),
                       "home_score_at_start": P.to_int(record.get("IG")),
                       "away_score_at_start": P.to_int(record.get("IH")),
                       "incidents": []}
            periods.append(current)
            continue
        if "III" not in record and "IK" not in record:
            continue
        incident = {
            "id": P.clean(record.get("III")),
            "team": {1: "home", 2: "away"}.get(P.to_int(record.get("IA"))),
            "minute": P.clean(record.get("IB")),
            "minute_number": P.to_int(record.get("IB")),
            "type": P.clean(record.get("IK")),
            "reason": P.clean(record.get("IL")),
            "description": P.clean(record.get("ICT")),
            "player": P.player_ref(record.get("IM"), record.get("IF"),
                                   link_path=record.get("IU")),
            "assist_player": P.player_ref(record.get("IN"), record.get("IO"),
                                          link_path=record.get("IP")),
            "home_score": P.to_int(record.get("IG")),
            "away_score": P.to_int(record.get("IH")),
        }
        if current is None:
            current = {"period": None, "home_score_at_start": None,
                       "away_score_at_start": None, "incidents": []}
            periods.append(current)
        current["incidents"].append(incident)
    result = _core_summary(match_id, core)
    result["period_count"] = len(periods)
    result["incident_count"] = sum(len(p["incidents"]) for p in periods)
    result["periods"] = periods
    return result


def get_statistics(match, period=None):
    """`df_st_` — every published statistic, grouped by period and category.

    Values arrive as the site prints them ("38%", "81% (281/346)", "0.55"),
    so each row keeps the display string AND a numeric reading of it."""
    match_id = _mid(match)
    core, text = run_parallel([lambda: _core(match_id),
                               lambda: _feed(f"df_st_{_SPORT_SLOT}_{match_id}")])
    periods = []
    current_period = None
    current_group = None
    for record in P.parse_records(text):
        if "SE" in record:
            current_period = {"period": P.clean(record.get("SE")), "groups": []}
            periods.append(current_period)
            current_group = None
            continue
        if "SF" in record:
            current_group = {"category": P.clean(record.get("SF")), "statistics": []}
            if current_period is None:
                current_period = {"period": None, "groups": []}
                periods.append(current_period)
            current_period["groups"].append(current_group)
            continue
        if "SG" not in record:
            continue
        if current_group is None:
            current_group = {"category": None, "statistics": []}
            if current_period is None:
                current_period = {"period": None, "groups": []}
                periods.append(current_period)
            current_period["groups"].append(current_group)
        home_text, away_text = P.clean(record.get("SH")), P.clean(record.get("SI"))
        current_group["statistics"].append({
            "id": P.clean(record.get("SD")),
            "name": P.clean(record.get("SG")),
            "home": home_text, "away": away_text,
            "home_value": P.to_float(home_text), "away_value": P.to_float(away_text),
        })
    if period:
        wanted = str(period).strip().lower()
        periods = [p for p in periods if (p.get("period") or "").lower() == wanted] or []
    result = _core_summary(match_id, core)
    result["period_count"] = len(periods)
    result["periods"] = periods
    return result


def get_lineups(match):
    """`df_li_` — formations, starters, bench, coaches, ratings and the
    in-match incidents attached to each player."""
    match_id = _mid(match)
    core, text = run_parallel([lambda: _core(match_id),
                               lambda: _feed(f"df_li_{_SPORT_SLOT}_{match_id}")])
    sides = {"home": {"formation": None, "rating": None, "groups": []},
             "away": {"formation": None, "rating": None, "groups": []}}
    group_name = None
    for record in P.parse_records(text):
        if "LB" in record and "LP" not in record:
            group_name = P.clean(record.get("LB"))
            continue
        if "LP" not in record:
            continue
        side = "home" if P.to_int(record.get("LK")) == 1 else "away"
        entry = sides[side]
        if record.get("LD"):
            entry["formation"] = P.clean(record.get("LD"))
        if record.get("LRH"):
            entry["rating"] = P.to_float(record.get("LRH"))
        player = {
            "player": P.player_ref(record.get("LP"), record.get("LI"),
                                   image_path=record.get("LPI"), link_path=record.get("NU")),
            "shirt_number": P.to_int(record.get("LJ")),
            "surname": P.clean(record.get("LN")),
            "country": P.country_ref(record.get("LO"), record.get("LQ")),
            "rating": P.to_float(record.get("LPR")),
            "role": P.clean(record.get("LS")),
            "is_captain": P.clean(record.get("LR")) == "(C)",
            "is_goalkeeper": P.clean(record.get("LR")) == "(G)",
            "incident": _lineup_incident(record),
        }
        target = None
        for group in entry["groups"]:
            if group["name"] == group_name:
                target = group
                break
        if target is None:
            target = {"name": group_name, "players": []}
            entry["groups"].append(target)
        target["players"].append(player)
    result = _core_summary(match_id, core)
    result["home"] = sides["home"]
    result["away"] = sides["away"]
    return result


def _lineup_incident(record):
    """A player's in-match marker: a card with its text, or the substitution
    partner (`LIN` is the other player's name, `LIU` their link)."""
    label = P.clean(record.get("LIT"))
    name = P.clean(record.get("LIN"))
    if not label and not name:
        return None
    return {"minute": label, "detail": name, "link": P.link(record.get("LIU"))}


def get_player_statistics(match):
    """Per-player statistics. The feed tab is empty for football, so this
    reads the two persisted queries the site uses: `epmsse` (the column
    definitions and the squads) and `epmsd` (every value)."""
    match_id = _mid(match)
    core, meta, values = run_parallel([
        lambda: _core(match_id),
        lambda: get_graphql_optional("epmsse", {"eventId": match_id, "projectId": 2}),
        lambda: get_graphql_optional("epmsd", {"eventId": match_id, "providerId": 7}),
    ])
    result = _core_summary(match_id, core)
    holder = (meta or {}).get("findEventPMSById") or {}
    stats_meta = holder.get("stats") or {}
    types = {t.get("id"): t for t in stats_meta.get("types") or []}
    teams = {}
    for team in holder.get("teams") or []:
        teams[team.get("id")] = {"id": P.clean(team.get("id")), "name": P.clean(team.get("name")),
                                 "side": (team.get("side") or "").lower() or None}
    entries = {}
    values_holder = (values or {}).get("findEventPMSById") or {}
    for entry in (values_holder.get("stats") or {}).get("entries") or []:
        entries.setdefault(entry.get("playerId"), []).append(entry)
    ratings = {r.get("participantId"): r for r in values_holder.get("ratings") or []}
    players = []
    for player in holder.get("players") or []:
        participant = player.get("participant") or {}
        player_id = P.clean(participant.get("id"))
        position = player.get("position") or {}
        rating = ratings.get(player_id) or {}
        stats = []
        for entry in entries.get(player_id, []):
            definition = types.get(entry.get("typeId")) or {}
            stats.append({
                "id": P.clean(entry.get("typeId")),
                "name": P.clean(definition.get("label")) or P.clean(entry.get("typeId")),
                "value": P.clean(entry.get("value")),
                "numeric_value": P.to_float(entry.get("rawValue")),
            })
        team = teams.get(player.get("teamId")) or {}
        players.append({
            "player": P.player_ref(player_id, participant.get("name"),
                                   slug=P.clean(participant.get("url"))),
            "team": team or None,
            "side": team.get("side"),
            "position": P.clean(position.get("name")),
            "is_goalkeeper": bool(position.get("isGoalkeeper")),
            "is_starter": bool(player.get("inBaseLineup")),
            "rating": P.to_float(rating.get("value")),
            "is_best_rated": bool(rating.get("isBestRating")),
            "statistic_count": len(stats),
            "statistics": stats,
        })
    result["categories"] = [{"id": P.clean(g.get("id")), "name": P.clean(g.get("label")),
                             "is_goalkeeper_only": bool(g.get("isGoalkeeperStat"))}
                            for g in stats_meta.get("typeGroups") or []]
    result["player_count"] = len(players)
    result["players"] = players
    return result


def get_h2h(match):
    """`df_hh_` — the previous meetings plus each side's recent form, as the
    site groups them ("Last matches: Sevilla", "Head-to-head")."""
    match_id = _mid(match)
    core, text = run_parallel([lambda: _core(match_id),
                               lambda: _feed(f"df_hh_{_SPORT_SLOT}_{match_id}")])
    sections = []
    current = None
    for record in P.parse_records(text):
        if "KA" in record:
            continue                       # the surface filter ("Overall", "All surfaces")
        if "KB" in record:
            current = {"name": P.clean(record.get("KB")), "matches": []}
            sections.append(current)
            continue
        if "KP" not in record:
            continue
        if current is None:
            current = {"name": None, "matches": []}
            sections.append(current)
        home_goals, away_goals = P.to_int(record.get("KU")), P.to_int(record.get("KT"))
        current["matches"].append({
            "id": P.clean(record.get("KP")),
            "link": P.match_link(P.clean(record.get("KP"))),
            "date": P.date_of(record.get("KC")),
            "start_time": P.timestamp(record.get("KC")),
            "competition": {"name": P.clean(record.get("KF")),
                            "short_name": P.clean(record.get("KI")),
                            "country": P.country_ref(record.get("KG"), record.get("KH"))},
            "home": P.team_ref(record.get("UQ"), (P.clean(record.get("KJ")) or "").lstrip("*"),
                               slug=P.clean(record.get("UE")), logo=record.get("EC")),
            "away": P.team_ref(record.get("UO"), (P.clean(record.get("KK")) or "").lstrip("*"),
                               slug=P.clean(record.get("UG")), logo=record.get("ED")),
            "home_score": home_goals,
            "away_score": away_goals,
            "score": P.clean(record.get("KL")),
            "result": {"w": "win", "l": "loss", "d": "draw"}.get(P.clean(record.get("WIS"))),
            "side": P.clean(record.get("KS")),
        })
    result = _core_summary(match_id, core)
    result["section_count"] = len(sections)
    result["sections"] = sections
    return result


def get_commentary(match):
    """`df_lc_` — the running text commentary, newest first as published."""
    match_id = _mid(match)
    core, text = run_parallel([lambda: _core(match_id),
                               lambda: _feed(f"df_lc_{_SPORT_SLOT}_{match_id}")])
    comments = []
    for record in P.parse_records(text):
        if "MD" not in record:
            continue
        comments.append({
            "minute": P.clean(record.get("MB")),
            "clock": P.clean(record.get("MK")),
            "icon": P.clean(record.get("MC")),
            "text": P.clean(record.get("MD")),
            "is_key_moment": P.to_bool(record.get("MF")),
            "home_score": P.to_int(record.get("ML")),
            "away_score": P.to_int(record.get("MM")),
        })
    result = _core_summary(match_id, core)
    result["count"] = len(comments)
    result["comments"] = comments
    return result


def get_report(match):
    """`df_mr_` — the editorial match report, when one was written."""
    match_id = _mid(match)
    core, text = run_parallel([lambda: _core(match_id),
                               lambda: _feed(f"df_mr_{_SPORT_SLOT}_{match_id}")])
    tree = P.parse_tree(text)
    node = P.first_node(tree, {"CO"})
    props = {}
    for found in P.iter_nodes(tree, {"CO"}):
        props.update(found["props"])
    body = P.clean(props.get("CO"))
    result = _core_summary(match_id, core)
    result["report"] = None if not (node and (props.get("TL") or body)) else {
        "title": P.clean(props.get("TL")),
        "link": P.link(props.get("LI")),
        "image": P.clean(props.get("CIU")) or P.clean(props.get("IU")),
        "image_credit": P.clean(props.get("IPN")),
        "published_at": P.timestamp(props.get("PU")),
        "source": P.clean(props.get("NA")),
        "body": _strip_markup(body),
    }
    return result


_MARKUP_RE = None


def _strip_markup(text):
    """Report bodies use the site's own "[p][b]…[/b][/p]" markup."""
    global _MARKUP_RE
    if text is None:
        return None
    import re
    if _MARKUP_RE is None:
        _MARKUP_RE = re.compile(r"\[/?[a-z][^\]]{0,20}\]")
    return P.clean(_MARKUP_RE.sub(" ", text))


def get_missing_players(match):
    """`df_scr_` — injuries and suspensions with the reason and how likely
    the player is to feature."""
    match_id = _mid(match)
    core, text = run_parallel([lambda: _core(match_id),
                               lambda: _feed(f"df_scr_{_SPORT_SLOT}_{match_id}")])
    sides = {"home": [], "away": []}
    for record in P.parse_records(text):
        if "SPI" not in record:
            continue
        side = "home" if P.to_int(record.get("SPT")) == 1 else "away"
        sides[side].append({
            "player": P.player_ref(record.get("SPI"), record.get("SPN"),
                                   link_path=record.get("SPR")),
            "country": P.country_ref(record.get("SPF"), record.get("SPG")),
            "reason": P.clean(record.get("SPE")),
            "likelihood": P.clean(record.get("SPD")),
        })
    result = _core_summary(match_id, core)
    result["home"] = sides["home"]
    result["away"] = sides["away"]
    result["count"] = len(sides["home"]) + len(sides["away"])
    return result


def get_point_by_point(match, set=None):
    """`df_mh_` — the game-by-game point sequence of a racket-sport match,
    plus `df_mhs_` for the game in progress."""
    match_id = _mid(match)
    core, text, live = run_parallel([
        lambda: _core(match_id),
        lambda: _feed(f"df_mh_{_SPORT_SLOT}_{match_id}"),
        lambda: _feed(f"df_mhs_{_SPORT_SLOT}_{match_id}"),
    ])
    sets = []
    current = None
    for record in P.parse_records(text):
        if "HA" in record:
            current = {"name": P.clean(record.get("HA")), "games": []}
            sets.append(current)
            continue
        if "HL" not in record and "HK" not in record:
            continue
        if current is None:
            current = {"name": None, "games": []}
            sets.append(current)
        current["games"].append({
            "home_games": P.to_int(record.get("HC")),
            "away_games": P.to_int(record.get("HE")),
            "server": {1: "home", 2: "away"}.get(P.to_int(record.get("HG"))),
            "winner": {1: "home", 2: "away"}.get(P.to_int(record.get("HK"))),
            "is_break": P.to_bool(record.get("HH")),
            "points": [p.strip() for p in (P.clean(record.get("HL")) or "").split(",") if p.strip()],
        })
    if set:
        wanted = str(set).strip().lower()
        sets = [s for s in sets
                if wanted in (s.get("name") or "").lower()
                or wanted == (s.get("name") or "").lower().replace("set ", "")]
    result = _core_summary(match_id, core)
    result["set_count"] = len(sets)
    result["sets"] = sets
    result["current_game"] = _current_game(live)
    return result


def _current_game(text):
    tree = P.parse_tree(text)
    points = []
    for node in P.iter_nodes(tree, {"SC"}):
        points.append({"side": {1: "home", 2: "away"}.get(P.to_int(node["props"].get("PT_1")))
                       or {1: "home", 2: "away"}.get(P.to_int(node["props"].get("PT"))),
                       "value": P.clean(node["props"].get("VA"))})
    header = P.first_node(tree, {"HD"})
    if not points and header is None:
        return None
    return {"name": P.clean(header["props"].get("VA")) if header else None,
            "points": [p for p in points if p.get("value") is not None] or None}


def get_standings(match, type="overall", line="all"):
    """The competition table as the match page shows it — `df_to_` keyed on
    the match, so a caller never has to resolve the competition first."""
    match_id = _mid(match)
    tab = refs.standings_tab(type, line)
    core, text = run_parallel([lambda: _core(match_id),
                               lambda: _feed(f"df_to_{_SPORT_SLOT}_{match_id}_{tab}")])
    result = _core_summary(match_id, core)
    result.update(standings_payload(text, type, line))
    return result


def get_top_scorers(match):
    """`df_tt_` — the competition's scoring chart, keyed on the match."""
    match_id = _mid(match)
    core, text = run_parallel([lambda: _core(match_id),
                               lambda: _feed(f"df_tt_{_SPORT_SLOT}_{match_id}")])
    result = _core_summary(match_id, core)
    result.update(top_scorers_payload(text))
    return result


def get_odds(match, bet_type=None, bet_scope=None, geo="US"):
    """`oce` — the full bookmaker comparison for one match.

    Which bookmakers appear depends on `geo` (the upstream's own geo-IP
    parameter), not on where this service runs, so a caller in any market
    can ask for that market's prices."""
    match_id = _mid(match)
    core, payload, sides = run_parallel([
        lambda: _core(match_id),
        lambda: get_graphql_optional("oce", {"eventId": match_id, "projectId": 2,
                                             "geoIpCode": (geo or "US").upper(),
                                             "geoIpSubdivisionCode": ""}, odds=True),
        lambda: _participant_sides(match_id),
    ])
    holder = (payload or {}).get("findOddsByEventId") or {}
    bookmakers = {}
    for entry in ((holder.get("settings") or {}).get("bookmakers") or []):
        book = entry.get("bookmaker") or {}
        bookmakers[book.get("id")] = {"id": book.get("id"), "name": P.clean(book.get("name")),
                                      "order": entry.get("numOrder")}
    markets = []
    for row in holder.get("odds") or []:
        if bet_type and (row.get("bettingType") or "").upper() != bet_type.upper():
            continue
        if bet_scope and (row.get("bettingScope") or "").upper() != bet_scope.upper():
            continue
        selections = []
        for odd in row.get("odds") or []:
            # An odds row identifies its pick by event-participant id; the
            # one with no participant is the draw.
            participant = P.clean(odd.get("eventParticipantId"))
            selections.append({
                "selection": sides.get(participant) if participant
                else (P.clean(odd.get("selection")) or P.clean(odd.get("winner")) or "draw"),
                "value": P.to_float(odd.get("value")),
                "opening_value": P.to_float(odd.get("opening")),
                "handicap": P.clean(odd.get("handicap")),
                "score": P.clean(odd.get("score")),
                "is_active": bool(odd.get("active")),
            })
        markets.append({
            "bookmaker": bookmakers.get(row.get("bookmakerId"))
            or {"id": row.get("bookmakerId"), "name": None},
            "bet_type": P.clean(row.get("bettingType")),
            "bet_scope": P.clean(row.get("bettingScope")),
            "has_live_betting": bool(row.get("hasLiveBettingOffers")),
            "selections": selections,
        })
    result = _core_summary(match_id, core)
    result["geo"] = (geo or "US").upper()
    result["bookmaker_count"] = len(bookmakers)
    result["market_count"] = len(markets)
    result["markets"] = markets
    return result


def _participant_sides(match_id):
    """{event_participant_id: "home"|"away"} — odds rows name their pick by
    the event-participant id, which is NOT the team id."""
    payload = get_graphql_optional("dsos2", {"eventId": match_id, "projectId": 2})
    holder = (payload or {}).get("findEventById") or {}
    sides = {}
    for participant in holder.get("eventParticipants") or []:
        side = ((participant.get("type") or {}).get("side") or "").lower()
        if participant.get("id") and side:
            sides[participant["id"]] = side
    return sides


def get_news(match):
    """`fsned` lists the article ids attached to a match; `nah` fetches each."""
    match_id = _mid(match)
    core, layout = run_parallel([
        lambda: _core(match_id),
        lambda: get_graphql_optional("fsned", {"projectId": 2, "entityId": match_id,
                                               "layoutTypeId": 2}),
    ])
    holder = (layout or {}).get("findNewsLayoutForEventDetail") or {}
    ids = []
    for section in holder.get("sections") or []:
        for article in section.get("articles") or []:
            article_id = article.get("id")
            if article_id and article_id not in ids:
                ids.append(article_id)
    articles = []
    if ids:
        fetched = gather({aid: (lambda a=aid: get_graphql("nah", {"articleId": a}))
                          for aid in ids[:12]})
        for article_id in ids[:12]:
            article = article_from_payload(fetched.get(article_id))
            if article:
                articles.append(article)
    result = _core_summary(match_id, core)
    result["count"] = len(articles)
    result["articles"] = articles
    return result


def get_broadcasts(match, geo=None):
    """`df_tv_` and `df_dos_` — TV channels and streaming providers, plus the
    audio commentary stream the core feed carries."""
    match_id = _mid(match)
    core, tv_text, stream_text = run_parallel([
        lambda: _core(match_id),
        lambda: _feed(f"df_tv_{_SPORT_SLOT}_{match_id}"),
        lambda: _feed(f"df_dos_{_SPORT_SLOT}_{match_id}_"),
    ])
    channels = []
    for record in P.parse_records(tv_text):
        name = P.clean(record.get("BN")) or P.clean(record.get("TVB"))
        if not name:
            continue
        channels.append({"name": name, "id": P.to_int(record.get("TVI")),
                         "link": P.link(record.get("BU")),
                         "country": P.clean(record.get("TVC"))})
    providers = []
    for record in P.parse_records(stream_text):
        raw = record.get("AL")
        if not raw:
            continue
        try:
            import json as _json
            payload = _json.loads(raw)
        except ValueError:
            continue
        for _, entries in (payload or {}).items():
            for entry in entries or []:
                providers.append({"name": P.clean(entry.get("BN")),
                                  "id": entry.get("BI") or entry.get("TVI"),
                                  "link": P.link(entry.get("BU")),
                                  "logo": P.clean(entry.get("IU"))})
    result = _core_summary(match_id, core)
    result["geo"] = (geo or "").upper() or None
    result["tv_channels"] = channels
    result["streaming_providers"] = providers
    result["audio_stream_link"] = P.clean(core.get("QJ")) or P.clean(core.get("QQ"))
    return result


# ---- shared payload builders (also used by tournaments.py / teams.py) ----------------

def standings_payload(text, type_name, line):
    """A `to_`/`df_to_` table -> {type, line, standings, ...}.

    One feed shape serves four very different tables, so a row only carries
    the columns its own table published: points/goals for a league table,
    over/under counts for the totals table, a 3x3 matrix for half-time
    /full-time, and a form strip for the form table.

    The over/under feed ignores the line in its own name and always answers
    with EVERY line, split into `ETI`-marked sections (verified: the 6:3 and
    6:8 tabs return byte-identical bodies), so the line is applied here. Each
    row then carries the `line` it belongs to, and `available_lines` says
    which ones this competition publishes."""
    rows = []
    title = subtitle = None
    current_line = None
    available = []
    for record in P.parse_records(text):
        if "TZ" in record:
            title, subtitle = P.clean(record.get("TZ")), P.clean(record.get("TZS"))
            continue
        if "ETI" in record and "TR" not in record:
            marker = P.clean(record.get("ETI"))
            current_line = None if marker in (None, "0") else marker
            if current_line and current_line not in available:
                available.append(current_line)
            continue
        if "LMS" in record or "LMU" in record:
            if rows:
                rows[-1].setdefault("recent_matches", []).append(_form_match(record))
            continue
        if "TI" not in record and "TN" not in record:
            continue
        goals_for, goals_against = _split_goals(record.get("TG"))
        row = {
            "rank": P.to_int(record.get("TR")),
            "team": P.team_ref(record.get("TI"), record.get("TN"),
                               link_path=record.get("TIU")),
            "matches_played": P.to_int(record.get("TM")),
            "points": P.to_int(record.get("TP")),
            "zone": P.clean(record.get("TU")),
            "zone_colour": P.clean(record.get("TUC")),
        }
        wins, draws, losses = (P.to_int(record.get("TW")), P.to_int(record.get("TDR")),
                               P.to_int(record.get("TL")))
        if wins is not None or draws is not None or losses is not None:
            row.update({"wins": wins, "draws": draws, "losses": losses})
        if goals_for is not None or goals_against is not None:
            row.update({"goals_for": goals_for, "goals_against": goals_against,
                        "goal_difference": P.to_int(record.get("TPF"))})
        if record.get("TPK"):
            row["points_per_match"] = P.to_float(record.get("TPK"))
        if record.get("OUO") or record.get("OUU"):
            row.update({"over": P.to_int(record.get("OUO")), "under": P.to_int(record.get("OUU")),
                        "average_goals": P.to_float(record.get("TGM"))})
        matrix = {key: P.to_int(record.get(feed_key)) for key, feed_key in (
            ("win_win", "HWW"), ("win_draw", "HWD"), ("win_loss", "HWL"),
            ("draw_win", "HDW"), ("draw_draw", "HDD"), ("draw_loss", "HDL"),
            ("loss_win", "HLW"), ("loss_draw", "HLD"), ("loss_loss", "HLL"))}
        if any(v is not None for v in matrix.values()):
            row["half_time_full_time"] = matrix
        if current_line:
            row["line"] = current_line
        rows.append({k: v for k, v in row.items() if v is not None or k in ("team", "rank")})
    wanted = str(line or "all").strip().lower()
    if available and wanted != "all":
        rows = [r for r in rows if r.get("line") == wanted]
    return {"type": type_name,
            "line": None if wanted == "all" else wanted,
            "available_lines": available or None,
            "title": title, "variant": subtitle,
            "count": len(rows), "standings": rows}


def _split_goals(value):
    """"28:6" -> (28, 6)."""
    text = P.clean(value)
    if not text or ":" not in text:
        return None, None
    left, _, right = text.partition(":")
    return P.to_int(left), P.to_int(right)


def _form_match(record):
    """One entry of a standings row's recent-form strip."""
    return {
        "id": P.clean(record.get("LME")),
        "link": P.match_link(P.clean(record.get("LME"))),
        "result": {"w": "win", "l": "loss", "d": "draw"}.get(P.clean(record.get("LMU"))),
        "is_upcoming": P.clean(record.get("LMU")) == "upcoming",
        "start_time": P.timestamp(record.get("LMC")),
        "home": P.team_ref(record.get("LMH"), record.get("LMJ")),
        "away": P.team_ref(record.get("LMA"), record.get("LMK")),
        "home_score": P.to_int(record.get("LMF")),
        "away_score": P.to_int(record.get("LMG")),
    }


def top_scorers_payload(text):
    """A `tt_`/`df_tt_` chart -> ranked scorers with their team and country."""
    rows = []
    for record in P.parse_records(text):
        if "UP" not in record and "UF" not in record:
            continue
        rows.append({
            "rank": P.to_int(record.get("UA")),
            "player": P.player_ref(record.get("UP"), record.get("UF"),
                                   link_path=record.get("UUR")),
            "team": P.team_ref(record.get("UT"), record.get("UU"),
                               short_name=record.get("UUS"), link_path=record.get("UUU")),
            "country": P.country_ref(record.get("UC"), record.get("UCN")),
            "position": P.clean(record.get("UPN")),
            "goals": P.to_int(record.get("UH")),
            "assists": P.to_int(record.get("UK")),
            "points": P.to_int(record.get("UJ")),
        })
    return {"count": len(rows), "top_scorers": rows}


def article_from_payload(payload):
    """A `nah` persisted-query result -> one news article."""
    article = (payload or {}).get("findCatArticleById") or {}
    if not article:
        return None
    images = article.get("images") or []
    largest = images[-1] if images else {}
    article_id = P.clean(article.get("id"))
    slug = P.clean(article.get("slug"))
    return {
        "id": article_id,
        "title": P.clean(article.get("title")),
        # The article page lives at /news/<slug>/<id>/ — both halves required.
        "link": P.link(f"/news/{slug}/{article_id}/") if slug and article_id else None,
        "slug": slug,
        "published_at": P.timestamp(article.get("publishedAt")),
        "edited_at": P.timestamp(article.get("editedAt")),
        "type": P.clean(article.get("type")),
        "image": P.clean(largest.get("url")),
        "image_credit": P.clean(largest.get("credit")),
        "image_caption": P.clean(largest.get("altText")),
    }
