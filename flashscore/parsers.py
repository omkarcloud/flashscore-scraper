"""Flashscore normalizers: the site's two wire formats and the HTML pages ->
one clean snake_case shape per entity.

WIRE FORMAT 1 — flat records ("~" splits records, "¬" splits pairs, "÷"
splits key and value). Used by the match lists, every match-detail tab, the
standings tables and head-to-head:

    SA÷1¬~ZA÷SPAIN: LaLiga¬ZEE÷QVmLl54o¬ZB÷176¬…¬~AA÷GGiPsLP1¬AD÷1789673400¬…

A record carrying `ZA` is a competition header and applies to every match
record (`AA`) that follows it, until the next header.

WIRE FORMAT 2 — a nested token stream, same separators but structured by
`TS÷<TYPE>` (open a section) / `TE÷<TYPE>` (close it), with `PT÷<name>`
followed by `PV÷<value>` as the properties inside. Used by transfers,
rankings and the news feeds:

    TS÷GR¬TI÷ALL¬TS÷TA¬TS÷RTT¬PT÷DATE¬PV÷1788732000¬PT÷TD¬PV÷out¬…¬TE÷RTT

`parse_records` and `parse_tree` handle the two; everything else in this
module turns their raw keys into the public shape.

Output conventions (shared with the other scrapers here): `link` for
canonical page URLs (always absolute), `image`/`logo`/`flag` for pictures,
`*_count` for counters, `is_*`/`has_*` for booleans, ISO-8601 UTC for
timestamps, numbers as numbers, null for missing. Entity refs share one
vocabulary: a team is {id, name, link, logo}, a player is
{id, name, link, image}, a competition is {id, name, link, logo}, a country
is {id, name}.

Deliberately dropped as noise: the `A1`/`SA`-style checksums the client uses
for change detection, the `ZX` sort-key blobs, the `PIU`/`IPI`/`IPU` image
lookup preambles (their contents are folded into the rows that reference
them), the per-bookmaker affiliate link tracking (`BU` query strings), the
six image size variants per player (one canonical size is kept), and the
GraphQL `__typename`/`subscriptionSubjects`/`shouldUpdate` plumbing.
"""
import json
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from . import refs

SITE = "https://www.flashscore.com"
IMG_BASE = "https://static.flashscore.com/res/image/data/"

_SPLIT_RE = re.compile(r"[¬~]")
_WS_RE = re.compile(r"\s+")


# ---- wire format 1: flat records -------------------------------------------------

def parse_records(text):
    """"~"-separated records of "¬"-separated KEY÷VALUE pairs -> [dict].

    Repeated keys inside one record keep the FIRST value (the upstream only
    repeats a key when it re-states an unchanged field)."""
    out = []
    for chunk in (text or "").split("~"):
        record = {}
        for pair in chunk.split("¬"):
            key, sep, value = pair.partition("÷")
            if sep and key not in record:
                record[key] = value
        if record:
            out.append(record)
    return out


# ---- wire format 2: nested TS/TE token stream ------------------------------------

def parse_tree(text):
    """TS/TE token stream -> a nested {type, props, children} tree.

    `props` maps a name to its LAST value; `prop_list` keeps every value for
    the rare repeated property. Unbalanced TE tokens are ignored rather than
    raising — the upstream closes sections it never opened on some sports."""
    root = {"type": "root", "props": {}, "prop_list": {}, "children": []}
    stack = [root]
    pending = None
    for token in _SPLIT_RE.split(text or ""):
        key, sep, value = token.partition("÷")
        if not sep:
            continue
        node = stack[-1]
        if key == "TS":
            child = {"type": value, "props": {}, "prop_list": {}, "children": []}
            node["children"].append(child)
            stack.append(child)
            pending = None
        elif key == "TE":
            if len(stack) > 1:
                stack.pop()
            pending = None
        elif key == "PT":
            pending = value
        elif key == "PV":
            if pending is not None:
                node["props"][pending] = value
                node["prop_list"].setdefault(pending, []).append(value)
                pending = None
        else:
            node["props"][key] = value
            node["prop_list"].setdefault(key, []).append(value)
    return root


def iter_nodes(node, wanted):
    """Depth-first walk yielding every node whose type is in `wanted`."""
    if node["type"] in wanted:
        yield node
    for child in node["children"]:
        yield from iter_nodes(child, wanted)


def first_node(node, wanted):
    for found in iter_nodes(node, wanted):
        return found
    return None


# ---- scalars ---------------------------------------------------------------------

def clean(value):
    """Collapse whitespace; "", "-", "?" and dashes become None."""
    if value is None:
        return None
    text = _WS_RE.sub(" ", str(value).replace("\xa0", " ")).strip()
    if text in ("", "-", "–", "—", "?", "N/A", "n/a"):
        return None
    return text


def clean_id(value):
    """Like clean() but "0" — the upstream's placeholder for "no entity" on
    tennis/cup rows that have no parent tournament — also becomes None."""
    text = clean(value)
    return None if text in (None, "0") else text


def to_int(value):
    """"25" -> 25; "83 186" / "83,186" -> 83186; None otherwise."""
    text = clean(value)
    if text is None:
        return None
    match = re.search(r"-?\d[\d\s,.]*", text)
    if not match:
        return None
    digits = re.sub(r"[\s,]", "", match.group(0))
    if digits.count(".") == 1 and len(digits.split(".")[1]) == 3:
        digits = digits.replace(".", "")      # 83.186 is a thousands group here
    digits = digits.split(".")[0]
    try:
        return int(digits)
    except ValueError:
        return None


def to_float(value):
    """"6.5" / "6,5" / "38%" -> float."""
    text = clean(value)
    if text is None:
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def to_bool(value):
    """The feed's truthiness: "1"/"y"/"true" -> True, "0"/""/absent -> False."""
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "y", "yes", "true")


def timestamp(value):
    """A unix timestamp (seconds) -> ISO-8601 UTC, or None for 0/absent."""
    seconds = to_int(value)
    if not seconds:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def date_of(value):
    """Same input as timestamp() -> the YYYY-MM-DD part only."""
    iso = timestamp(value)
    return iso[:10] if iso else None


def parse_date(text):
    """"19.06.2002" / "30.06.2029" / "2002-06-19" -> ISO date."""
    text = clean(text)
    if not text:
        return None
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return match.group(0)
    match = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    if match:
        day, month, year = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return None


def money(text):
    """Flashscore prints compact euros: "€85.1m", "€3.0m", "€500k", "" ->
    {amount, currency} in whole units, or None."""
    text = clean(text)
    if not text:
        return None
    match = re.search(r"([€£$])\s*(-?[\d.,]+)\s*(bn|m|k)?", text, re.I)
    if not match:
        return None
    symbol, number, scale = match.groups()
    try:
        amount = float(number.replace(",", ""))
    except ValueError:
        return None
    amount *= {"bn": 1_000_000_000, "m": 1_000_000, "k": 1_000}.get((scale or "").lower(), 1)
    currency = {"€": "EUR", "£": "GBP", "$": "USD"}.get(symbol, "EUR")
    return {"amount": int(round(amount)), "currency": currency}


# ---- links / images ---------------------------------------------------------------

def link(path):
    """A site-relative path -> an absolute flashscore.com URL.

    Entity paths are normalised to the canonical trailing-slash form the site
    itself uses, so the same team is always the same string whichever feed or
    page it came from."""
    path = clean(path)
    if not path:
        return None
    if not path.startswith(("http://", "https://")):
        path = SITE + ("" if path.startswith("/") else "/") + path
    if "?" in path or "#" in path or path.rsplit("/", 1)[-1].count("."):
        return path              # a query, a fragment or a file — leave it alone
    return path if path.endswith("/") else path + "/"


def image(path):
    """An image token from a feed ("A7kHoxZA-ttfpEDUq.png") -> absolute URL.

    Some rows carry a gender word ("men") or a flag token ("flag-176")
    instead of a real image; those are not URLs and become None."""
    path = clean(path)
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path
    if not path.endswith((".png", ".jpg", ".jpeg", ".webp", ".svg")):
        return None
    if path.startswith("/"):
        return SITE + path
    return IMG_BASE + path


def match_link(match_id):
    return f"{SITE}/match/{match_id}/" if match_id else None


def team_link(team_id, slug=None):
    """Flashscore serves a team page ONLY at /team/<slug>/<id>/ and 404s any
    other spelling, so a link is emitted only when the slug is known — a
    guessed one would be a dead link (horses and some individual-sport
    competitors have no team page at all)."""
    if not team_id or not slug:
        return None
    return f"{SITE}/team/{slug}/{team_id}/"


def player_link(player_id, slug=None):
    """Same rule as team_link: no slug, no link."""
    if not player_id or not slug:
        return None
    return f"{SITE}/player/{slug}/{player_id}/"


# ---- entity refs -------------------------------------------------------------------

def team_ref(team_id, name=None, *, slug=None, logo=None, short_name=None, link_path=None):
    """{id, name, short_name, link, logo} — the one team shape used everywhere."""
    team_id = clean(team_id)
    name = clean(name)
    if not team_id and not name:
        return None
    return {
        "id": team_id,
        "name": name,
        "short_name": clean(short_name),
        "link": link(link_path) if link_path else team_link(team_id, slug),
        "logo": image(logo),
    }


def player_ref(player_id, name=None, *, slug=None, image_path=None, link_path=None):
    player_id = clean(player_id)
    name = clean(name)
    if not player_id and not name:
        return None
    return {
        "id": player_id,
        "name": name,
        "link": link(link_path) if link_path else player_link(player_id, slug),
        "image": image(image_path),
    }


def country_ref(country_id, name=None):
    country_id = to_int(country_id)
    name = clean(name)
    if country_id is None and not name:
        return None
    return {"id": country_id, "name": name}


def competition_ref(header):
    """A competition header record (the `Z*` keys) -> the competition shape.

    `ZA` is "COUNTRY: Name" for club competitions and "ATP - SINGLES: Name"
    for tennis, so the display name is split off its category prefix."""
    if not header:
        return None
    display = clean(header.get("ZA")) or ""
    category = clean(header.get("ZAF")) or clean(header.get("ZY"))
    name = display
    if ":" in display:
        prefix, _, rest = display.partition(":")
        if category and prefix.strip().lower() == category.strip().lower():
            name = rest.strip()
        elif not category:
            category, name = prefix.strip(), rest.strip()
        else:
            name = rest.strip()
    return {
        "id": clean_id(header.get("ZE")),
        "stage_id": clean_id(header.get("ZC")),
        "template_id": clean_id(header.get("ZEE")),
        "name": clean(name),
        "display_name": clean(display),
        "category": clean(category),
        "link": link(header.get("ZL")),
        "logo": image(header.get("OAJ")),
        "country": country_ref(header.get("ZB"), header.get("ZY")),
    }


# ---- match rows --------------------------------------------------------------------
# Period score keys: BA/BB is period 1, BC/BD period 2, BE/BF period 3, and so
# on in pairs. Football is the exception the site makes on purpose — it sends
# only BC/BD and they carry the HALF-TIME score, not the second half.
_PERIOD_KEYS = [("BA", "BB"), ("BC", "BD"), ("BE", "BF"), ("BG", "BH"),
                ("BI", "BJ"), ("BK", "BL"), ("BM", "BN")]


def _period_scores(row, sport_id):
    """[{name, home, away}] for the per-period columns a sport publishes."""
    periods = []
    for index, (home_key, away_key) in enumerate(_PERIOD_KEYS, start=1):
        home, away = to_int(row.get(home_key)), to_int(row.get(away_key))
        if home is None and away is None:
            continue
        periods.append({"name": _period_name(sport_id, index), "home": home, "away": away})
    return periods


def _period_name(sport_id, index):
    if sport_id == 1:
        # Football only ever fills slot 2, and it means half time.
        return "Half Time" if index == 2 else f"Period {index}"
    if sport_id in (2, 12, 17, 25, 21):        # racket / net sports
        return f"Set {index}"
    if sport_id in (3, 5, 18):                  # basketball, gridiron, AFL
        return f"Quarter {index}"
    if sport_id in (4, 9, 10, 19, 8):           # hockey / rugby codes
        return f"Period {index}"
    return f"Period {index}"


def is_leaderboard_row(row, sport_id=None):
    """True when a row is one competitor's standing in a field event rather
    than a head-to-head match. Golf, cycling, horse racing and the winter
    sports publish those: no second participant, and a position in `NI`.
    Detected from the row itself so an out-of-season sport still works."""
    if row.get("PY") or row.get("AF"):
        return False
    return bool(row.get("NI")) or (sport_id in refs.NO_DUEL_SPORTS)


def leaderboard_row(row, header=None, *, sport_id=None):
    """One competitor's line of a golf / cycling / racing leaderboard."""
    entry_id = clean(row.get("AA"))
    out = {
        "id": entry_id,
        "position": to_int(row.get("NI")),
        "competitor": _match_side(row, "home"),
        "status": refs.status_of(to_int(row.get("AB"))),
        "stage": refs.stage_name(to_int(row.get("AC"))),
        "score": clean(row.get("AG")),            # e.g. "-7" to par
        "score_value": to_float(row.get("AG")),
        "total": to_int(row.get("BI")),           # strokes / time value
        "start_time": timestamp(row.get("AD")),
        "end_time": timestamp(row.get("AP")) or timestamp(row.get("AO")),
        "date": date_of(row.get("AD")),
    }
    if header:
        out["competition"] = competition_ref(header)
    return out


def event_meta(header):
    """The extra facts a no-duel competition header carries: the window it
    runs over, the course par and the prize fund (`ZN` packs them as
    "start|end|par|prize")."""
    if not header:
        return None
    packed = [p for p in str(header.get("ZN") or "").split("|")]
    meta = {
        "starts_at": timestamp(packed[0]) if len(packed) > 0 else None,
        "ends_at": timestamp(packed[1]) if len(packed) > 1 else None,
        "par": to_int(packed[2]) if len(packed) > 2 else None,
        "prize_fund": clean(header.get("ZP")) or (clean(packed[3]) if len(packed) > 3 else None),
    }
    return meta if any(v is not None for v in meta.values()) else None


def match_row(row, header=None, *, sport_id=None):
    """One `AA` record from a list / results / fixtures feed -> a match dict.

    `header` is the competition record that preceded it in the same feed."""
    match_id = clean(row.get("AA"))
    bucket = to_int(row.get("AB"))
    stage_id = to_int(row.get("AC"))
    sport_id = sport_id if sport_id is not None else to_int(row.get("DV"))
    winner = to_int(row.get("AS"))
    home_score, away_score = to_int(row.get("AG")), to_int(row.get("AH"))
    periods = _period_scores(row, sport_id)
    out = {
        "id": match_id,
        "link": match_link(match_id),
        "status": refs.status_of(bucket),
        "stage": refs.stage_name(stage_id) or refs.status_of(bucket),
        "stage_id": stage_id,
        "start_time": timestamp(row.get("AD")),
        "date": date_of(row.get("AD")),
        "end_time": timestamp(row.get("AO")),
        "round": clean(row.get("ER")),
        "note": clean(row.get("AM")),
        "home": _match_side(row, "home"),
        "away": _match_side(row, "away"),
        "home_score": home_score,
        "away_score": away_score,
        "winner": {1: "home", 2: "away", 0: "draw"}.get(winner),
        "periods": periods or None,
        "has_statistics": to_bool(row.get("AX")),
        "has_live_coverage": to_bool(row.get("AN")),
    }
    if header:
        out["competition"] = competition_ref(header)
    # Tennis serves the current game's points in WA/WB and the server in WC.
    if row.get("WA") or row.get("WB"):
        out["current_game"] = {
            "home_points": clean(row.get("WA")),
            "away_points": clean(row.get("WB")),
            "serving": {1: "home", 2: "away"}.get(to_int(row.get("WC"))),
        }
    return out


def _match_side(row, side):
    """The home/away participant of a list row. Tennis rows add the player's
    country (CA/CB + FU/FV) where team sports leave it empty."""
    if side == "home":
        keys = {"id": "PX", "name": "AE", "full": "FH", "slug": "WU", "logo": "OA",
                "short": "WM", "country_id": "CA", "country": "FU"}
    else:
        keys = {"id": "PY", "name": "AF", "full": "FK", "slug": "WV", "logo": "OB",
                "short": "WN", "country_id": "CB", "country": "FV"}
    ref = team_ref(row.get(keys["id"]), row.get(keys["name"]),
                   slug=clean(row.get(keys["slug"])), logo=row.get(keys["logo"]),
                   short_name=row.get(keys["short"]))
    if ref is None:
        return None
    full_name = clean(row.get(keys["full"]))
    if full_name and full_name != ref["name"]:
        ref["full_name"] = full_name
    country = country_ref(row.get(keys["country_id"]), row.get(keys["country"]))
    if country:
        ref["country"] = country
    return ref


def match_list(text, *, sport_id=None, leaderboard=False):
    """A whole list feed -> [match], each carrying its competition header.

    With `leaderboard=True` the rows of a no-duel sport are emitted as
    leaderboard entries instead (see `is_leaderboard_row`)."""
    header = None
    rows = []
    for record in parse_records(text):
        if "ZA" in record:
            header = record
        elif "AA" in record:
            if leaderboard and is_leaderboard_row(record, sport_id):
                rows.append(leaderboard_row(record, header, sport_id=sport_id))
            else:
                rows.append(match_row(record, header, sport_id=sport_id))
    return rows


def group_by_competition(matches):
    """[match] -> [{competition, matches}] preserving the feed's own order.

    The per-match `competition` key is dropped once it is on the group."""
    groups = []
    index = {}
    for item in matches:
        competition = item.pop("competition", None) or {}
        key = competition.get("stage_id") or competition.get("id") or competition.get("name")
        if key not in index:
            index[key] = {"competition": competition or None, "matches": []}
            groups.append(index[key])
        index[key]["matches"].append(item)
    return groups


# ---- HTML helpers --------------------------------------------------------------------

def soup(html):
    return BeautifulSoup(html or "", "lxml")


def text_of(element):
    return clean(element.get_text(" ", strip=True)) if element is not None else None


def js_object(html, marker):
    """The JS object literal assigned right after `marker` in a page, as a
    dict. Brace-matched rather than regex-matched: these blobs embed JSON
    strings containing braces, so any lazy regex truncates them."""
    if not html:
        return {}
    start = html.find(marker)
    if start < 0:
        return {}
    open_brace = html.find("{", start)
    if open_brace < 0:
        return {}
    depth = 0
    in_string = False
    escaped = False
    for index in range(open_brace, len(html)):
        char = html[index]
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[open_brace:index + 1])
                except ValueError:
                    return {}
    return {}


def page_environment(html):
    """The `window.environment = {...}` blob every page ships."""
    return js_object(html, "window.environment")


_FEED_BLOCK_RE = re.compile(
    r"cjs\.initialFeeds\[['\"](?P<name>[\w-]+)['\"]\]\s*=\s*\{(?P<body>.*?)\n\s*\}",
    re.S)
_FEED_DATA_RE = re.compile(r"data:\s*`(?P<data>.*?)`", re.S)


def initial_feeds(html):
    """The `cjs.initialFeeds[...] = {data: `…`, allEventsCount: N, seasonId: N}`
    blocks a league / team page embeds -> {name: {text, total_count, season_id}}.

    This is page one of results and fixtures for free, plus the NUMERIC season
    id the `tr_`/`tf_` pagination feeds need (it appears nowhere else)."""
    feeds = {}
    for block in _FEED_BLOCK_RE.finditer(html or ""):
        body = block.group("body")
        data = _FEED_DATA_RE.search(body)
        feeds[block.group("name")] = {
            "text": data.group("data") if data else "",
            "total_count": to_int((re.search(r"allEventsCount:\s*(\d+)", body) or [None, None])[1]
                                  if re.search(r"allEventsCount:\s*(\d+)", body) else None),
            "season_id": to_int((re.search(r"seasonId:\s*(\d+)", body) or [None, None])[1]
                                if re.search(r"seasonId:\s*(\d+)", body) else None),
        }
    return feeds


def archive_seasons(html):
    """The `var league_archive_data = {"seasons":[…]}` blob a competition's
    archive page ships: every past season's name, page URL and winner."""
    return js_object(html, "league_archive_data").get("seasons") or []


def pagination(page, per_page, total_count):
    """The block route_glue.paginate() lifts into the flat gateway shape."""
    total_pages = 0
    if total_count and per_page:
        total_pages = (int(total_count) + int(per_page) - 1) // int(per_page)
    return {"page": page, "items_per_page": per_page,
            "total_pages": total_pages, "total_count": total_count}
