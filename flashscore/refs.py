"""Flashscore reference parsing: ONE param per input that auto-detects its
forms (tripadvisor QueryOrIdField convention — never a sibling `url`/`id`
pair). Every entity ref accepts a bare Flashscore id OR a pasted
flashscore.com link (the slug in a link is decorative; only the id matters,
and the site itself redirects a slug-less URL to the canonical one):

  match        GCxZ2uHc | https://www.flashscore.com/match/football/GCxZ2uHc/
                        | https://www.flashscore.com/match/real-madrid-W8mj7MDD/
                          sevilla-h8oAv4Ts/?mid=GCxZ2uHc
  team         W8mj7MDD | https://www.flashscore.com/team/real-madrid/W8mj7MDD/
  player       ne2xCTJj | https://www.flashscore.com/player/mendes-nuno/ne2xCTJj/
  tournament   /football/spain/laliga/          (a league path or full link)
                        | QeI1Oeyi:dWdJXP6U     (tournament id : stage id)
  ranking      dSJr14Y8 | https://www.flashscore.com/tennis/rankings/atp/
  article      p41OraKF | https://www.flashscore.com/news/.../p41OraKF/

Also holds the tables the routes share: the sport id <-> slug map (probed
live 2026-09-18), the standings tab ids, the event-stage translations, and
the odds betting types.
"""
import re
from urllib.parse import parse_qs, urlparse

# Every Flashscore id is EXACTLY 8 characters of [A-Za-z0-9] (checked against
# 352 ids harvested from the live feeds — match, team, player, tournament,
# stage and template ids alike). The extra "contains an upper-case letter or
# a digit" rule is what separates an id from a URL slug: ids like GCxZ2uHc,
# W8mj7MDD and hxt57t2q all satisfy it, while 8-letter lower-case slugs such
# as "football" or "handball" do not, so a competition link is never mistaken
# for an entity id.
_ID_RE = re.compile(r"^(?=.*[A-Z0-9])[A-Za-z0-9]{8}$")

# ---- sports ---------------------------------------------------------------------
# id -> url slug. Probed live (every /<slug>/ page prints its own sportId):
# ids 20, 27, 32, 33 and 40 appear in the counts feed but have no public
# sport page, so they are deliberately absent.
SPORTS = {
    1: "football", 2: "tennis", 3: "basketball", 4: "hockey",
    5: "american-football", 6: "baseball", 7: "handball", 8: "rugby-union",
    9: "floorball", 10: "bandy", 11: "futsal", 12: "volleyball",
    13: "cricket", 14: "darts", 15: "snooker", 16: "boxing",
    17: "beach-volleyball", 18: "aussie-rules", 19: "rugby-league",
    21: "badminton", 22: "water-polo", 23: "golf", 24: "field-hockey",
    25: "table-tennis", 26: "beach-soccer", 28: "mma", 29: "netball",
    30: "pesapallo", 31: "motorsport", 34: "cycling", 35: "horse-racing",
    36: "esports", 37: "winter-sports", 38: "ski-jumping",
    39: "alpine-skiing", 41: "biathlon", 42: "kabaddi",
}
SPORT_NAMES = {
    1: "Football", 2: "Tennis", 3: "Basketball", 4: "Hockey",
    5: "American Football", 6: "Baseball", 7: "Handball", 8: "Rugby Union",
    9: "Floorball", 10: "Bandy", 11: "Futsal", 12: "Volleyball",
    13: "Cricket", 14: "Darts", 15: "Snooker", 16: "Boxing",
    17: "Beach Volleyball", 18: "Aussie Rules", 19: "Rugby League",
    21: "Badminton", 22: "Water Polo", 23: "Golf", 24: "Field Hockey",
    25: "Table Tennis", 26: "Beach Soccer", 28: "MMA", 29: "Netball",
    30: "Pesäpallo", 31: "Motorsport", 34: "Cycling", 35: "Horse Racing",
    36: "eSports", 37: "Winter Sports", 38: "Ski Jumping",
    39: "Alpine Skiing", 41: "Biathlon", 42: "Kabaddi",
}
_SLUG_TO_SPORT = {slug: sid for sid, slug in SPORTS.items()}
# Aliases people actually type.
_SPORT_ALIASES = {
    "soccer": 1, "nfl": 5, "nba": 3, "ice-hockey": 4, "icehockey": 4,
    "nhl": 4, "mlb": 6, "ufc": 28, "f1": 31, "formula-1": 31,
    "table tennis": 25, "american football": 5, "rugby": 8,
}

# ---- event stage (status) --------------------------------------------------------
# `AC` on a list row / `DB` on a match core row. Every sport ships its own
# window.environment.eventStageTranslations; this is the union collected from
# football, tennis, basketball, cricket and darts match pages (2026-09-18).
# Stage 1 is "&nbsp;" upstream (a scheduled match has no stage caption).
STAGES = {
    1: "Scheduled", 2: "Live", 3: "Finished", 4: "Postponed", 5: "Cancelled",
    6: "Extra Time", 7: "Penalties", 8: "Finished / retired", 9: "Walkover",
    10: "After Extra Time", 11: "After Penalties", 12: "1st Half", 13: "2nd Half",
    17: "Set 1", 18: "Set 2", 19: "Set 3", 20: "Set 4", 21: "Set 5",
    22: "1st Quarter", 23: "2nd Quarter", 24: "3rd Quarter", 25: "4th Quarter",
    26: "1st Inns", 27: "2nd Inns", 36: "Interrupted", 37: "Abandoned",
    38: "Half Time", 42: "Awaiting updates", 43: "Delayed", 45: "To finish",
    46: "Break Time", 47: "Set 1 - Tiebreak", 48: "Set 2 - Tiebreak",
    49: "Set 3 - Tiebreak", 50: "Set 4 - Tiebreak", 51: "Set 5 - Tiebreak",
    54: "Awarded", 55: "Set 6", 56: "Set 7", 57: "After day 1", 58: "After day 2",
    59: "After day 3", 60: "After day 4", 61: "After day 5", 324: "Set 8",
    325: "Set 9", 326: "Set 10", 327: "Set 11", 328: "Set 12", 329: "Set 13",
    333: "Lunch", 334: "Tea", 335: "Medical timeout",
}
# `AB` / `DA`: the coarse bucket every sport shares.
STATUS_BUCKETS = {1: "scheduled", 2: "live", 3: "finished"}

# ---- standings ------------------------------------------------------------------
# `type` (+ `line` for over-under) -> the tab id in `to_`/`df_to_` feed names.
# Verified live on LaLiga 2026/27; `tx_<tournament>_<stage>` lists which of
# these a given competition actually publishes.
STANDINGS_TYPES = {
    "overall": "1",
    "home": "2",
    "away": "3",
    "form": "5:0",
    "form-home": "8:0",
    "form-away": "9:0",
    "over-under": "6:{line}",
    "over-under-home": "17:{line}",
    "over-under-away": "18:{line}",
    "ht-ft": "13",
    "ht-ft-home": "14",
    "ht-ft-away": "15",
    "top-scorers": "10",
}
# over/under line -> the numeric slot in the tab id (8 = every line at once).
OVER_UNDER_LINES = {
    "all": "8", "0.5": "1", "1.5": "2", "2.5": "3",
    "3.5": "4", "4.5": "5", "5.5": "6",
}

# ---- odds -----------------------------------------------------------------------
BETTING_TYPES = ["HOME_DRAW_AWAY", "HOME_AWAY", "OVER_UNDER",
                 "BOTH_TEAMS_TO_SCORE", "DOUBLE_CHANCE", "ASIAN_HANDICAP",
                 "CORRECT_SCORE", "HALF_TIME_FULL_TIME"]
BETTING_SCOPES = ["FULL_TIME", "FIRST_HALF", "SECOND_HALF"]

# ---- search ---------------------------------------------------------------------
# The autocomplete's own type ids, read back off live responses (a request
# for an id outside 1-4 is rejected with "UnprocessableEntity"):
#   1 TournamentTemplate, 2 Team, 3 Player, 4 PlayerInTeam
# "Player" is the standalone athlete a tennis/golf page is built around;
# "PlayerInTeam" is a squad member of a club — both surface as `player`.
SEARCH_TYPES = {
    "tournament": 1,
    "team": 2,
    "player": 3,
    "player-in-team": 4,
}
SEARCH_TYPE_NAMES = {1: "tournament", 2: "team", 3: "player", 4: "player_in_team"}

# ---- transfers / news -----------------------------------------------------------
TRANSFER_TABS = {"all": "1", "in": "2", "out": "3"}

# ---- leaderboard ("no-duel") sports -----------------------------------------------
# Golf, cycling, horse racing, motorsport and the winter sports have no
# head-to-head events: a day-list row is ONE competitor's leaderboard entry
# (position `NI`, score `AG`, strokes/time `BI`), there is no second
# participant, and `dc_<id>` returns an empty body, so none of the match
# detail tabs exist for them. Verified live 2026-09-18 for golf, cycling and
# horse racing; the rest were out of season and are listed from the same
# no-duel family the site groups them in.
NO_DUEL_SPORTS = {23, 31, 34, 35, 37, 38, 39, 41}


# ---- helpers --------------------------------------------------------------------

def _is_flashscore_url(value):
    if not value.startswith(("http://", "https://", "//", "www.", "/")):
        return False
    if value.startswith("/"):
        return True
    url = value if "://" in value else "https://" + value.lstrip("/")
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return "flashscore" in host or "livesport" in host


def _path_of(value):
    if value.startswith("/"):
        return value, {}
    url = value if "://" in value else "https://" + value.lstrip("/")
    parsed = urlparse(url)
    return parsed.path, parse_qs(parsed.query or "")


def _id_after(parts, segment):
    """The first id-shaped segment following `segment`. Match links put the
    sport slug in between (/match/football/<id>/), so the search skips ahead
    rather than taking the next segment blindly."""
    if segment in parts:
        idx = parts.index(segment)
        for candidate in parts[idx + 1:idx + 4]:
            if _ID_RE.match(candidate):
                return candidate
    return None


def resolve_match(value):
    """Match id from a bare id or any flashscore.com match link.

    The canonical link carries the id in `?mid=`; the short form has it as a
    path segment (/match/football/<id>/ or /match/<id>/). Both appear in the
    wild because the site rewrites one into the other."""
    value = str(value or "").strip()
    if not value:
        raise ValueError("match is required")
    if _ID_RE.match(value):
        return value
    if _is_flashscore_url(value):
        path, query = _path_of(value)
        mid = (query.get("mid") or [None])[0]
        if mid and _ID_RE.match(mid):
            return mid
        parts = [p for p in path.split("/") if p]
        found = _id_after(parts, "match")
        if found:
            return found
        # /match/<slug-with-id>/<slug-with-id>/ — the trailing ids belong to
        # the two teams, so only the ?mid= form is usable there.
        raise ValueError("could not find a match id in that flashscore.com link "
                         "(expected /match/<id>/ or ?mid=<id>)")
    raise ValueError("match must be a Flashscore match id (e.g. GCxZ2uHc) "
                     "or a flashscore.com match link")


def _entity_resolver(kind, segment, example):
    """Resolves to {"id", "slug"}: the site's own pages are addressed as
    /<segment>/<slug>/<id>/ and REJECT a wrong slug with a 404, so a link
    carries one bit a bare id does not. Endpoints that only hit the feeds
    (which key on the id alone) ignore `slug`; endpoints that must load a
    page ask lookup.py to fill it in."""
    def resolve(value):
        # Idempotent: the marshmallow field resolves the param once, and the
        # endpoint function then calls this again on what it was handed.
        if isinstance(value, dict):
            if value.get("id"):
                return {"id": value["id"], "slug": value.get("slug")}
            raise ValueError(f"{kind} is required")
        value = str(value or "").strip()
        if not value:
            raise ValueError(f"{kind} is required")
        if _ID_RE.match(value):
            return {"id": value, "slug": None}
        if _is_flashscore_url(value):
            path, _ = _path_of(value)
            parts = [p for p in path.split("/") if p]
            if segment not in parts and not any(_ID_RE.match(p) for p in parts):
                raise ValueError(f"that flashscore.com link is not a {kind} page "
                                 f"(expected a /{segment}/<slug>/<id>/ path)")
            found = _id_after(parts, segment)
            if found:
                index = parts.index(found)
                slug = parts[index - 1] if index >= 1 and parts[index - 1] != segment else None
                return {"id": found, "slug": slug}
            # Some links carry extra tab segments after the id.
            for position in range(len(parts) - 1, -1, -1):
                if _ID_RE.match(parts[position]):
                    slug = parts[position - 1] if position >= 1 else None
                    return {"id": parts[position],
                            "slug": slug if slug != segment else None}
            raise ValueError(f"could not find a {kind} id in that flashscore.com link "
                             f"(expected a /{segment}/<slug>/<id>/ path)")
        raise ValueError(f"{kind} must be a Flashscore {kind} id (e.g. {example}) "
                         f"or a flashscore.com {kind} link")
    return resolve


resolve_team = _entity_resolver("team", "team", "W8mj7MDD")
resolve_player = _entity_resolver("player", "player", "ne2xCTJj")


def resolve_ranking(value):
    """A ranking table in any of the three forms callers hold:

      * the 8-character ranking id the `ran_` feed takes (dSJr14Y8)
      * a rankings page link (/tennis/rankings/wta/) — the form
        /flashscore/rankings returns; it carries no id, so misc.py reads the
        id off that page (`_cjs.rankingId`)
      * the numeric tab id /flashscore/rankings returns (35853), mapped to
        its page through the rankings menu

    -> {"id", "path", "tab_id"}, exactly one of them set."""
    if isinstance(value, dict):
        # Idempotent: the marshmallow field resolved it already.
        if value.get("id") or value.get("path") or value.get("tab_id"):
            return {"id": value.get("id"), "path": value.get("path"), "tab_id": value.get("tab_id")}
        raise ValueError("ranking is required")
    value = str(value or "").strip()
    if not value:
        raise ValueError("ranking is required")
    if _ID_RE.match(value) and not value.isdigit():
        return {"id": value, "path": None, "tab_id": None}
    if value.isdigit() and len(value) <= 9:
        return {"id": None, "path": None, "tab_id": value}
    if _is_flashscore_url(value):
        path, _ = _path_of(value)
        parts = [p for p in path.split("/") if p]
        for part in reversed(parts):
            if _ID_RE.match(part) and not part.isdigit():
                return {"id": part, "path": None, "tab_id": None}
        if "rankings" in parts and parts[0] in _SLUG_TO_SPORT:
            keep = parts[:parts.index("rankings") + 2]
            return {"id": None, "path": "/" + "/".join(keep) + "/", "tab_id": None}
    raise ValueError("ranking must be a Flashscore ranking id (e.g. dSJr14Y8), a rankings "
                     "page link (e.g. https://www.flashscore.com/tennis/rankings/atp/) or "
                     "a tab id from /flashscore/rankings")


def resolve_article(value):
    """News article id from a bare id or a /news/... link."""
    value = str(value or "").strip()
    if not value:
        raise ValueError("article is required")
    if _ID_RE.match(value):
        return value
    if _is_flashscore_url(value):
        path, _ = _path_of(value)
        parts = [p for p in path.split("/") if p]
        for part in reversed(parts):
            if _ID_RE.match(part):
                return part
    raise ValueError("article must be a Flashscore article id or a flashscore.com news link")


def resolve_tournament(value):
    """A competition reference, in whichever of its three forms was passed:

      * a league path or link — "/football/spain/laliga/", a past season
        ("/football/spain/laliga-2024-2025/"), or the full URL. Returned as
        {"path": "/football/spain/laliga/"}; lookup.py turns it into ids.
      * "<tournament_id>:<stage_id>" — the pair the standings/draw feeds take,
        for callers that cached them. Returned as {"tournament_id", "stage_id"}.
      * a bare stage or tournament id is NOT accepted: the feeds need both
        halves and there is no way to infer the other one.
    """
    if isinstance(value, dict):
        # Already resolved by the marshmallow field.
        if value.get("path") or (value.get("tournament_id") and value.get("stage_id")):
            return {"path": value.get("path"), "tournament_id": value.get("tournament_id"),
                    "stage_id": value.get("stage_id")}
        raise ValueError("tournament is required")
    value = str(value or "").strip()
    if not value:
        raise ValueError("tournament is required")
    if ":" in value and not value.startswith(("http", "//")):
        left, _, right = value.partition(":")
        left, right = left.strip(), right.strip()
        if _ID_RE.match(left) and _ID_RE.match(right):
            return {"tournament_id": left, "stage_id": right, "path": None}
        raise ValueError("tournament id pair must look like <tournament_id>:<stage_id> "
                         "(e.g. QeI1Oeyi:dWdJXP6U)")
    if _is_flashscore_url(value):
        path, _ = _path_of(value)
        parts = [p for p in path.split("/") if p]
        # Drop trailing tab segments so /laliga/results/ and /laliga/ agree.
        while parts and parts[-1] in ("results", "fixtures", "standings", "archive",
                                      "draw", "news", "odds", "summary", "table"):
            parts.pop()
        if len(parts) < 2 or parts[0] not in _SLUG_TO_SPORT:
            raise ValueError("tournament link must be a flashscore.com competition page "
                             "(e.g. https://www.flashscore.com/football/spain/laliga/)")
        return {"path": "/" + "/".join(parts) + "/", "tournament_id": None, "stage_id": None}
    raise ValueError("tournament must be a flashscore.com competition link "
                     "(e.g. /football/spain/laliga/) or a <tournament_id>:<stage_id> pair "
                     "(e.g. QeI1Oeyi:dWdJXP6U)")


def resolve_sport(value):
    """Sport as an id (1), a slug (football, american-football) or a name
    (Football, American Football, soccer) -> the numeric id."""
    if value in (None, ""):
        raise ValueError("sport is required")
    if isinstance(value, int) and not isinstance(value, bool):
        if value in SPORTS:
            return value
        raise ValueError(f"unknown sport id {value} — see /flashscore/sports")
    value = str(value).strip()
    if value.isdigit():
        sid = int(value)
        if sid in SPORTS:
            return sid
        raise ValueError(f"unknown sport id {sid} — see /flashscore/sports")
    key = value.lower().replace("_", "-").replace(" ", "-")
    if key in _SLUG_TO_SPORT:
        return _SLUG_TO_SPORT[key]
    if key in _SPORT_ALIASES:
        return _SPORT_ALIASES[key]
    if value.lower() in _SPORT_ALIASES:
        return _SPORT_ALIASES[value.lower()]
    for sid, name in SPORT_NAMES.items():
        if name.lower() == value.lower():
            return sid
    raise ValueError(f"unknown sport '{value}' — use a slug (football), a name (Football) "
                     "or an id (see /flashscore/sports)")


def sport_slug(sport_id):
    return SPORTS.get(sport_id)


def sport_name(sport_id):
    return SPORT_NAMES.get(sport_id)


def sport_ref(sport_id):
    """{id, name, slug} for a sport id, or None."""
    sport_id = _as_int(sport_id)
    if sport_id is None or sport_id not in SPORTS:
        return None
    return {"id": sport_id, "name": SPORT_NAMES.get(sport_id), "slug": SPORTS[sport_id]}


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def standings_tab(type_name, line="all"):
    """('over-under', '2.5') -> '6:3'. Raises ValueError for a bad combination."""
    template = STANDINGS_TYPES.get(type_name)
    if template is None:
        raise ValueError(f"unknown standings type '{type_name}' — one of "
                         f"{', '.join(sorted(STANDINGS_TYPES))}")
    if "{line}" not in template:
        return template
    slot = OVER_UNDER_LINES.get(str(line or "all"))
    if slot is None:
        raise ValueError(f"unknown over/under line '{line}' — one of "
                         f"{', '.join(OVER_UNDER_LINES)}")
    return template.format(line=slot)


def stage_name(stage_id):
    return STAGES.get(_as_int(stage_id))


def status_of(bucket):
    return STATUS_BUCKETS.get(_as_int(bucket))
