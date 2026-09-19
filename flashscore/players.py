"""Player endpoints.

The player page is entirely server-rendered — profile, career tables,
transfers and injury history all arrive in the HTML — so one fetch answers
every endpoint here and the rest is parsing.

`player` takes a flashscore.com player link or a bare id. A bare id works
for the news endpoint (its feed is keyed on the id alone) but NOT for the
page-backed ones: Flashscore serves a player page only at its exact
/player/<slug>/<id>/ URL and publishes no id-to-slug lookup (probed
2026-09-18 across every player-keyed feed, the search service and the
GraphQL tags). Every player reference this API emits carries its `link`, so
callers always have the usable form to pass back.
"""
from . import lookup
from . import parsers as P
from . import refs
from .fetch import get_feed
from .teams import news_payload

# The career tables have no per-column headings in the DOM (the site uses
# icons), and every sport lays them out the same way.
_CAREER_COLUMNS = ["rating", "matches_played", "goals", "assists",
                   "yellow_cards", "red_cards"]


def _ref(player):
    return refs.resolve_player(player)


def _page(ref):
    return lookup.player_page(ref)


def _profile(soup, ref):
    """The header block: name, position, club, age, birthday, market value
    and contract expiry, which the page prints as "Label: value" pairs.

    Country comes from the breadcrumb (/football/portugal/), not from the
    visible text — the club name sits in the same run of anchors and would
    otherwise win."""
    heading = soup.select_one("div.player-profile-heading") or soup
    name = P.text_of(soup.select_one("[class*=playerHeader__nameWrapper]")) \
        or P.text_of(soup.select_one("[class*=heading__name]"))
    team_anchor = heading.select_one("a.playerInfoItem__link[href^='/team/']") \
        or heading.select_one("a[href^='/team/']")
    team_link = team_anchor.get("href") if team_anchor is not None else None
    team_name = None
    if team_anchor is not None:
        team_name = P.clean(team_anchor.get("title")) or \
            P.clean((P.text_of(team_anchor) or "").strip("()"))
    position = None
    team_block = soup.select_one("[class*=playerTeam]")
    team_text = P.text_of(team_block) or ""
    if team_text:
        position = P.clean(team_text.split("(")[0]) if "(" in team_text else P.clean(team_text)
    country = None
    for anchor in heading.select("a[class*=breadcrumbItemLabel]"):
        href = anchor.get("href") or ""
        parts = [p for p in href.split("/") if p]
        if len(parts) == 2:                      # /football/portugal/
            country = P.text_of(anchor)
    labels = _labelled_values(heading)
    age_text = labels.get("Age") or ""
    return {
        "id": ref["id"],
        "name": name or (P.text_of(soup.select_one("h1")) if soup else None),
        "link": P.link(f"/player/{ref['slug']}/{ref['id']}/") if ref.get("slug") else None,
        "position": position,
        "country": country,
        "team": P.team_ref(_id_from_path(team_link), team_name, link_path=team_link)
        if (team_link or team_name) else None,
        "age": P.to_int(age_text.split("(")[0] if age_text else None),
        "date_of_birth": P.parse_date(age_text),
        "market_value": P.money(labels.get("Market value")),
        "contract_expires": P.parse_date(labels.get("Contract expires")),
    }


def _labelled_values(block):
    """"Age : 24 (19.06.2002) Market value : €85.1m" -> {label: value}."""
    text = P.text_of(block) or ""
    out = {}
    for label in ("Age", "Market value", "Contract expires", "Height", "Weight"):
        marker = label + " :"
        index = text.find(marker)
        if index < 0:
            marker = label + ":"
            index = text.find(marker)
        if index < 0:
            continue
        rest = text[index + len(marker):].strip()
        for other in ("Age", "Market value", "Contract expires", "Height", "Weight"):
            if other == label:
                continue
            cut = rest.find(other + " ")
            if cut > 0:
                rest = rest[:cut]
        out[label] = P.clean(rest)
    return out


def _id_from_path(path):
    parts = [p for p in str(path or "").split("/") if p]
    for part in reversed(parts):
        if 6 <= len(part) <= 10 and part.isalnum():
            return part
    return None


_CAREER_SECTIONS = ["League", "Domestic Cups", "International Cups", "National Team"]


def _career_rows(soup):
    """The career tables, one `div.careerTab` block per competition kind.

    The block labels live in a single `subFilter` element ("League Domestic
    Cups International Cups National Team") rather than on the blocks, so
    they are matched back by position, falling back to the site's own fixed
    order when the label strip is missing."""
    labels = _career_labels(soup)
    sections = []
    for index, block in enumerate(soup.select("div.careerTab")):
        rows = []
        for row in block.select("div.careerTab__row"):
            classes = row.get("class") or []
            if "careerTab__row--main" in classes or "careerTab__row--total" in classes:
                continue
            entry = _career_entry(row)
            if entry["season"] or entry["team"]:
                rows.append(entry)
        if not rows:
            continue
        name = labels[index] if index < len(labels) else (
            _CAREER_SECTIONS[index] if index < len(_CAREER_SECTIONS) else None)
        total = block.select_one("div.careerTab__row--total")
        sections.append({
            "name": name,
            "count": len(rows),
            "totals": _career_totals(total),
            "seasons": rows,
        })
    return sections


def _career_labels(soup):
    """"League Domestic Cups International Cups National Team" -> the four
    labels, split on the known section names so a missing one shifts nothing."""
    block = soup.select_one("div.subFilter.careerTab__subFilter") \
        or soup.select_one("[class*=careerTab__subFilter]")
    parts = [P.text_of(node) for node in (block.find_all(["a", "button"]) if block else [])]
    parts = [p for p in parts if p]
    if parts:
        return parts
    text = P.text_of(block) or ""
    found = []
    for name in _CAREER_SECTIONS:
        if name.lower() in text.lower():
            found.append(name)
    return found


def _career_entry(row):
    """One season row. `careerTab__competitionHref` also appears INSIDE the
    participant cell, so both cells are matched on their exact class."""
    season = P.text_of(row.find(["span", "div"], class_="careerTab__season"))
    participant = row.find("div", class_="careerTab__participant")
    competition = row.find("div", class_="careerTab__competition")
    team_anchor = participant.find("a") if participant is not None else None
    competition_anchor = competition.find("a") if competition is not None else None
    stats = [P.text_of(cell) for cell in row.find_all("div", class_="careerTab__stat")]
    entry = {
        "season": season,
        "team": P.team_ref(_id_from_path(team_anchor.get("href")) if team_anchor else None,
                           P.text_of(team_anchor) or (team_anchor.get("title") if team_anchor else None),
                           link_path=team_anchor.get("href") if team_anchor else None),
        "competition": {
            "name": P.text_of(competition_anchor) or (competition_anchor.get("title")
                                                      if competition_anchor is not None else None),
            "link": P.link(competition_anchor.get("href")) if competition_anchor is not None else None,
        } if competition_anchor is not None else None,
    }
    for index, column in enumerate(_CAREER_COLUMNS):
        value = stats[index] if index < len(stats) else None
        entry[column] = P.to_float(value) if column == "rating" else P.to_int(value)
    return entry


def _career_totals(row):
    """The "Total" footer row. It omits the rating column, and each cell
    names its own column index (`careerTab__stat--2`), so the mapping is
    taken from that suffix rather than from position."""
    if row is None:
        return None
    totals = {column: None for column in _CAREER_COLUMNS}
    for cell in row.find_all("div", class_="careerTab__stat"):
        position = None
        for cls in cell.get("class") or []:
            if cls.startswith("careerTab__stat--") and cls.split("--")[-1].isdigit():
                position = int(cls.split("--")[-1])
        if position is None or not 1 <= position <= len(_CAREER_COLUMNS):
            continue
        column = _CAREER_COLUMNS[position - 1]
        value = P.text_of(cell)
        totals[column] = P.to_float(value) if column == "rating" else P.to_int(value)
    return totals if any(v is not None for v in totals.values()) else None


def get_details(player):
    """The player card plus career, transfers and injury history — the page
    carries all of it, so this is one fetch."""
    ref = _ref(player)
    soup = P.soup(_page(ref))
    result = _profile(soup, ref)
    sections = _career_rows(soup)
    transfers = _transfer_rows(soup)
    injuries = _injury_rows(soup)
    result["career"] = sections
    result["career_section_count"] = len(sections)
    result["transfer_count"] = len(transfers)
    result["transfers"] = transfers
    result["injury_count"] = len(injuries)
    result["injury_history"] = injuries
    return result


def get_career(player):
    """Just the career tables, season by season, by competition kind."""
    ref = _ref(player)
    soup = P.soup(_page(ref))
    sections = _career_rows(soup)
    result = _profile(soup, ref)
    result["count"] = sum(len(s["seasons"]) for s in sections)
    result["career"] = sections
    return result


def _transfer_rows(soup):
    """`transferTab__row` — date, both clubs, the move type and its fee."""
    transfers = []
    for row in soup.select("div.transferTab__row"):
        if "transferTab__row--main" in (row.get("class") or []):
            continue
        anchors = row.select("a[href^='/team/']")
        fee_text = P.text_of(row.find("div", class_="transferTab__feePrize"))
        transfers.append({
            "date": P.parse_date(P.text_of(row.find("div", class_="transferTab__date"))),
            "from_team": P.team_ref(_id_from_path(anchors[0].get("href")), P.text_of(anchors[0]),
                                    link_path=anchors[0].get("href")) if len(anchors) > 0 else None,
            "to_team": P.team_ref(_id_from_path(anchors[1].get("href")), P.text_of(anchors[1]),
                                  link_path=anchors[1].get("href")) if len(anchors) > 1 else None,
            "type": P.text_of(row.find("div", class_="transferTab__type")),
            "fee": P.money(fee_text),
        })
    return transfers


def _injury_rows(soup):
    """`injuryTable__row` — the from/until window and the injury named."""
    injuries = []
    for row in soup.select("div.injuryTable__row"):
        if "injuryTable__row--main" in (row.get("class") or []):
            continue
        # The cells are <span>s, not <div>s, unlike every other table here.
        dates = [P.text_of(cell)
                 for cell in row.find_all(["span", "div"], class_="injuryTable__date")]
        injuries.append({
            "from_date": P.parse_date(dates[0]) if len(dates) > 0 else None,
            "until_date": P.parse_date(dates[1]) if len(dates) > 1 else None,
            "type": P.text_of(row.find(["span", "div"], class_="injuryTable__typeInfo"))
            or P.text_of(row.find(["span", "div"], class_="injuryTable__type")),
        })
    return injuries


def get_transfers(player):
    """The transfer table on the player page: date, clubs, type and fee."""
    ref = _ref(player)
    soup = P.soup(_page(ref))
    transfers = _transfer_rows(soup)
    result = _profile(soup, ref)
    result["count"] = len(transfers)
    result["transfers"] = transfers
    return result


def get_injuries(player):
    """The player's injury history, newest first."""
    ref = _ref(player)
    soup = P.soup(_page(ref))
    injuries = _injury_rows(soup)
    result = _profile(soup, ref)
    result["count"] = len(injuries)
    result["injury_history"] = injuries
    return result


def get_news(player):
    """`pnf_` — the player's headline feed. Works from a bare id."""
    ref = _ref(player)
    text = get_feed(f"pnf_{ref['id']}", optional=True)
    result = {"id": ref["id"],
              "link": P.link(f"/player/{ref['slug']}/{ref['id']}/") if ref.get("slug") else None}
    result.update(news_payload(text))
    return result
