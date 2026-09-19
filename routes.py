"""The 50 Flashscore endpoints. Every path is served with and without the
`/flashscore` prefix, so code generated against the hosted API on RapidAPI
(paths like /matches/details) runs unchanged against this server.

Parameters are validated by the same marshmallow schemas the module ships
(flashscore/schemas.py), so a bad request fails here exactly as it does on the
hosted API — same message, same status. ONE param per input: `match`, `team`,
`player`, `tournament`, `ranking` and `article` each take a bare Flashscore ID
or a pasted flashscore.com link, and `sport` takes an ID, a slug or a name.
"""
import json
from urllib.parse import urlencode

from bottle import request, response, route

from flashscore import discovery, matches, misc, players, schemas, teams, tournaments
from schema_fields import load_query
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError

PREFIX = "/flashscore"


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The request query as plain unicode strings (bottle 0.12's .get() hands
    back latin-1 decoded bytes, so a UTF-8 "Amélie" would arrive as
    "AmÃ©lie")."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _page_link(path, params, page):
    if not page:
        return None
    query = {k: v for k, v in params.items() if v not in (None, "", False)}
    query["page"] = page
    host = request.headers.get("Host") or f"localhost:{request.environ.get('SERVER_PORT', 8000)}"
    scheme = request.headers.get("X-Forwarded-Proto") or request.urlparts.scheme or "http"
    return f"{scheme}://{host}{path}?{urlencode(query, doseq=True)}"


def paginate(result, path, raw_params):
    """Lift the scraper's `pagination` block into the flat shape the hosted API
    returns, so a response from this server and one from the hosted API have
    the same keys."""
    pagination = result.pop("pagination", None) or {}
    result.pop("count", None)          # per-page count; `count` is the total
    page = _as_int(pagination.get("page")) or _as_int(raw_params.get("page")) or 1
    total_pages = max(_as_int(pagination.get("total_pages")), 0)
    out = {
        "count": pagination.get("total_count"),
        "per_page": pagination.get("items_per_page"),
        "current_page": page,
        "total_pages": total_pages,
        "next": _page_link(path, raw_params, page + 1 if page < total_pages else None),
        "previous": _page_link(path, raw_params, page - 1 if page > 1 else None),
    }
    out.update(result)
    return out


def call(schema_cls, impl, path, paginated=False):
    """Validate the query, run the endpoint, map failures to HTTP:
    bad params -> 400, missing entity -> 404, transport/blocks -> 502."""
    raw = query_dict()
    data, error = load_query(schema_cls, raw)
    if error:
        return json_response(error, 400)
    try:
        result = impl(**data)
    except ValueError as e:                     # bad id / unusable combination
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"flashscore rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Blocked as e:
        return json_response({"error": f"flashscore blocked the request, retry later: {e}"}, 502)
    except UpstreamError as e:
        return json_response({"error": f"flashscore {path.strip('/')} failed: {e}"}, 502)
    except Exception as e:
        return json_response({"error": f"flashscore {path.strip('/')} failed: "
                                       f"{type(e).__name__}: {e}"}, 500)
    if paginated:
        result = paginate(result, path, raw)
    return json_response(result)


def mount(path, schema_cls, impl, paginated=False):
    """Serve one endpoint at /path and /flashscore/path."""
    def handler():
        return call(schema_cls, impl, path, paginated)
    handler.__name__ = "flashscore_" + path.strip("/").replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route(PREFIX + path, method="GET")(handler)


# Order matches content/openapi_spec.json — the live-scores core first.
ENDPOINTS = [
    # matches: the live-scores core
    ("/matches", schemas.MatchListSchema, matches.get_matches, False),
    ("/matches/live", schemas.LiveMatchesSchema, matches.get_live_matches, False),
    ("/matches/details", schemas.MatchSchema, matches.get_details, False),
    # match detail tabs
    ("/matches/statistics", schemas.MatchStatisticsSchema, matches.get_statistics, False),
    ("/matches/lineups", schemas.MatchSchema, matches.get_lineups, False),
    ("/matches/summary", schemas.MatchSchema, matches.get_summary, False),
    ("/matches/player-statistics", schemas.MatchSchema, matches.get_player_statistics, False),
    ("/matches/h2h", schemas.MatchSchema, matches.get_h2h, False),
    ("/matches/odds", schemas.MatchOddsSchema, matches.get_odds, False),
    ("/matches/commentary", schemas.MatchSchema, matches.get_commentary, False),
    ("/matches/point-by-point", schemas.MatchPointByPointSchema, matches.get_point_by_point, False),
    ("/matches/standings", schemas.MatchStandingsSchema, matches.get_standings, False),
    ("/matches/top-scorers", schemas.MatchSchema, matches.get_top_scorers, False),
    ("/matches/missing-players", schemas.MatchSchema, matches.get_missing_players, False),
    ("/matches/report", schemas.MatchSchema, matches.get_report, False),
    ("/matches/news", schemas.MatchSchema, matches.get_news, False),
    ("/matches/broadcasts", schemas.MatchBroadcastsSchema, matches.get_broadcasts, False),
    ("/matches/live-updates", schemas.LiveUpdatesSchema, matches.get_live_updates, False),
    # search
    ("/search", schemas.SearchSchema, discovery.search, False),
    # competitions
    ("/tournaments/standings", schemas.TournamentStandingsSchema, tournaments.get_standings, False),
    ("/tournaments/results", schemas.TournamentPageSchema, tournaments.get_results, True),
    ("/tournaments/fixtures", schemas.TournamentPageSchema, tournaments.get_fixtures, True),
    ("/tournaments/details", schemas.TournamentSchema, tournaments.get_details, False),
    ("/tournaments/top-scorers", schemas.TournamentSchema, tournaments.get_top_scorers, False),
    ("/tournaments/draw", schemas.TournamentSchema, tournaments.get_draw, False),
    ("/tournaments/seasons", schemas.TournamentSchema, tournaments.get_seasons, False),
    ("/tournaments/archive", schemas.TournamentSchema, tournaments.get_archive, False),
    ("/tournaments/standings-types", schemas.TournamentSchema, tournaments.get_standings_types, False),
    ("/tournaments/news", schemas.TournamentSchema, tournaments.get_news, False),
    # teams
    ("/teams/details", schemas.TeamSchema, teams.get_details, False),
    ("/teams/results", schemas.TeamPageSchema, teams.get_results, True),
    ("/teams/fixtures", schemas.TeamPageSchema, teams.get_fixtures, True),
    ("/teams/squad", schemas.TeamSchema, teams.get_squad, False),
    ("/teams/transfers", schemas.TeamTransfersSchema, teams.get_transfers, False),
    ("/teams/standings", schemas.TeamStandingsSchema, teams.get_standings, False),
    ("/teams/top-scorers", schemas.TeamSchema, teams.get_top_scorers, False),
    ("/teams/news", schemas.TeamSchema, teams.get_news, False),
    # players
    ("/players/details", schemas.PlayerSchema, players.get_details, False),
    ("/players/career", schemas.PlayerSchema, players.get_career, False),
    ("/players/transfers", schemas.PlayerSchema, players.get_transfers, False),
    ("/players/injuries", schemas.PlayerSchema, players.get_injuries, False),
    ("/players/news", schemas.PlayerSchema, players.get_news, False),
    # rankings & news
    ("/rankings", schemas.RankingsSchema, misc.get_rankings, False),
    ("/rankings/data", schemas.RankingSchema, misc.get_ranking, False),
    ("/news", schemas.NewsSchema, misc.get_news, False),
    ("/news/article", schemas.ArticleSchema, misc.get_article, False),
    # catalogue
    ("/sports", schemas.EmptySchema, discovery.get_sports, False),
    ("/countries", schemas.SportSchema, discovery.get_countries, False),
    ("/tournaments", schemas.CountryTournamentsSchema, discovery.get_tournaments, False),
    ("/resolve", schemas.ResolveSchema, discovery.resolve, False),
]

for _path, _schema, _impl, _paginated in ENDPOINTS:
    mount(_path, _schema, _impl, _paginated)


@route("/", method="GET")
@route("/health", method="GET")
@route(PREFIX, method="GET")
@route(PREFIX + "/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, _, _, _ in ENDPOINTS]})
