"""Offline tests for the Flashscore parsers — no network.

Every fixture in flashscore/fixtures/ is a real upstream payload captured on
2026-09-18 (the text feeds verbatim; the HTML pages trimmed to the blocks the
parsers read). The assertions pin the field mapping that was decoded from
live data, so a silent upstream rename shows up here rather than as nulls in
a customer's response.

    python -m pytest flashscore/test_parsers.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flashscore import parsers as P          # noqa: E402
from flashscore import players, refs, teams  # noqa: E402
from flashscore.matches import (standings_payload,  # noqa: E402
                                top_scorers_payload)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


# ---- wire formats ------------------------------------------------------------

def test_parse_records_splits_on_the_three_separators():
    records = P.parse_records("AA÷x1¬AB÷3¬~AA÷x2¬AB÷1¬")
    assert records == [{"AA": "x1", "AB": "3"}, {"AA": "x2", "AB": "1"}]


def test_parse_records_keeps_the_first_value_of_a_repeated_key():
    assert P.parse_records("AA÷first¬AA÷second¬") == [{"AA": "first"}]


def test_parse_tree_nests_ts_te_sections():
    tree = P.parse_tree("TS÷GR¬TI÷ALL¬TS÷RTT¬PT÷TD¬PV÷out¬TE÷RTT¬TE÷GR¬")
    group = tree["children"][0]
    assert group["type"] == "GR" and group["props"]["TI"] == "ALL"
    assert group["children"][0]["type"] == "RTT"
    assert group["children"][0]["props"]["TD"] == "out"


def test_parse_tree_survives_an_unbalanced_close():
    tree = P.parse_tree("TE÷GR¬TS÷TA¬PT÷X¬PV÷1¬")
    assert [node["type"] for node in tree["children"]] == ["TA"]


# ---- scalars ------------------------------------------------------------------

def test_clean_treats_placeholder_dashes_as_missing():
    assert P.clean("  Real   Madrid ") == "Real Madrid"
    assert P.clean("-") is None and P.clean("?") is None and P.clean("") is None


def test_clean_id_drops_the_zero_placeholder():
    assert P.clean_id("0") is None
    assert P.clean_id("QeI1Oeyi") == "QeI1Oeyi"


def test_to_int_handles_grouped_digits():
    assert P.to_int("83 186") == 83186
    assert P.to_int("83.186") == 83186
    assert P.to_int("6") == 6 and P.to_int("-") is None


def test_money_reads_the_compact_euro_notation():
    assert P.money("€85.1m") == {"amount": 85100000, "currency": "EUR"}
    assert P.money("€3.0m")["amount"] == 3000000
    assert P.money("€500k")["amount"] == 500000
    assert P.money("") is None


def test_timestamp_is_iso_utc_and_zero_is_missing():
    assert P.timestamp("1789673400") == "2026-09-17T19:30:00Z"
    assert P.date_of("1789673400") == "2026-09-17"
    assert P.timestamp("0") is None


def test_parse_date_accepts_both_site_formats():
    assert P.parse_date("19.06.2002") == "2002-06-19"
    assert P.parse_date("2002-06-19") == "2002-06-19"


def test_image_rejects_non_image_tokens():
    assert P.image("A7kHoxZA-ttfpEDUq.png").endswith("/A7kHoxZA-ttfpEDUq.png")
    assert P.image("men") is None          # a gender word, not a picture
    assert P.image("flag-176") is None


# ---- match rows ----------------------------------------------------------------

def test_football_match_row_maps_scores_periods_and_teams():
    matches = P.match_list(load("tournament_results.txt"), sport_id=1)
    first = matches[0]
    assert first["id"] == "GGiPsLP1"
    assert first["status"] == "finished" and first["stage"] == "Finished"
    assert (first["home_score"], first["away_score"]) == (1, 3)
    assert first["winner"] == "away"
    assert first["home"]["name"] == "Malaga" and first["away"]["name"] == "Villarreal"
    assert first["round"] == "Round 6"
    # Football fills only the second period slot, and it is the half-time score.
    assert first["periods"] == [{"name": "Half Time", "home": 0, "away": 1}]


def test_match_rows_carry_their_competition_header():
    matches = P.match_list(load("tournament_results.txt"), sport_id=1)
    competition = matches[0]["competition"]
    assert competition["name"] == "LaLiga"
    assert competition["id"] == "QeI1Oeyi" and competition["stage_id"] == "dWdJXP6U"
    assert competition["country"] == {"id": 176, "name": "Spain"}


def test_tennis_rows_use_set_periods_and_carry_player_countries():
    matches = P.match_list(load("tennis_day_list.txt"), sport_id=2)
    with_sets = [m for m in matches if m["periods"]]
    assert with_sets, "expected at least one tennis match with set scores"
    assert with_sets[0]["periods"][0]["name"] == "Set 1"
    with_country = [m for m in matches if (m["home"] or {}).get("country")]
    assert with_country, "tennis rows should carry the player's country"


def test_group_by_competition_preserves_feed_order():
    matches = P.match_list(load("tennis_day_list.txt"), sport_id=2)
    groups = P.group_by_competition(matches)
    assert groups and "competition" in groups[0] and groups[0]["matches"]
    assert sum(len(g["matches"]) for g in groups) == len(matches)
    assert "competition" not in groups[0]["matches"][0]


# ---- standings ------------------------------------------------------------------

def test_league_table_columns():
    payload = standings_payload(load("standings_overall.txt"), "overall", "all")
    assert payload["count"] == 20
    leader = payload["standings"][0]
    assert leader["rank"] == 1 and leader["team"]["name"] == "Barcelona"
    assert leader["points"] == 18 and leader["matches_played"] == 6
    assert (leader["wins"], leader["draws"], leader["losses"]) == (6, 0, 0)
    assert leader["goals_for"] == 28 and leader["goals_against"] == 6
    assert leader["goal_difference"] == 22 and leader["points_per_match"] == 3.0
    assert leader["zone"] == "q1"
    assert leader["recent_matches"], "form strip should ride along"


def test_over_under_feed_is_split_by_line_and_filtered():
    text = load("standings_over_under.txt")
    every = standings_payload(text, "over-under", "all")
    assert every["available_lines"] and "2.5" in every["available_lines"]
    assert every["count"] > 20, "the feed returns every line at once"
    one = standings_payload(text, "over-under", "2.5")
    assert one["count"] == 20 and one["line"] == "2.5"
    assert all(row.get("line") == "2.5" for row in one["standings"])
    assert one["standings"][0]["over"] is not None


def test_half_time_full_time_matrix():
    payload = standings_payload(load("standings_ht_ft.txt"), "ht-ft", "all")
    matrix = payload["standings"][0]["half_time_full_time"]
    assert set(matrix) == {"win_win", "win_draw", "win_loss", "draw_win", "draw_draw",
                           "draw_loss", "loss_win", "loss_draw", "loss_loss"}


def test_top_scorers_rows():
    payload = top_scorers_payload(load("top_scorers.txt"))
    top = payload["standings"] if "standings" in payload else payload["top_scorers"]
    assert payload["count"] > 50
    assert top[0]["rank"] == 1 and top[0]["player"]["name"]
    assert top[0]["team"]["name"] and top[0]["goals"] is not None


# ---- HTML -------------------------------------------------------------------------

def test_page_environment_is_brace_matched():
    environment = P.page_environment(load("league_page.html"))
    assert environment["sport_id"] == 1
    assert environment["season_list"][0]["name"] == "2026/2027"


def test_initial_feeds_expose_page_one_and_the_numeric_season_id():
    feeds = P.initial_feeds(load("league_page.html"))
    assert "results" in feeds
    assert feeds["results"]["season_id"] == 190
    assert feeds["results"]["total_count"] == 59
    assert P.match_list(feeds["results"]["text"], sport_id=1)


def test_archive_seasons_blob():
    seasons = P.archive_seasons(load("league_archive.html"))
    assert len(seasons) > 50
    assert seasons[0]["name"].startswith("LaLiga")


def test_squad_groups_and_player_rows():
    soup = P.soup(load("team_squad.html"))
    tables = soup.select("div.squad-table.profileTable")
    assert len(tables) == 3, "one table per competition filter"
    groups = teams._squad_groups(tables[-1])
    names = [group["name"] for group in groups]
    assert "Goalkeepers" in names and "Coach" in names
    keeper = groups[0]["players"][0]
    assert keeper["player"]["name"] == "Courtois Thibaut"
    assert keeper["player"]["id"] == "MVExOq3n"
    assert keeper["shirt_number"] == 1 and keeper["age"] == 34
    assert keeper["country"] == "Belgium"
    assert keeper["matches_played"] is not None


def test_player_profile_reads_country_from_the_breadcrumb_not_the_club():
    soup = P.soup(load("player_profile.html"))
    profile = players._profile(soup, {"id": "ne2xCTJj", "slug": "mendes-nuno"})
    assert profile["name"] == "Nuno Mendes"
    assert profile["country"] == "Portugal"        # not "(PSG)"
    assert profile["position"] == "Defender"
    assert profile["team"]["id"] == "CjhkPw0k"
    assert profile["age"] == 24 and profile["date_of_birth"] == "2002-06-19"
    assert profile["market_value"] == {"amount": 85100000, "currency": "EUR"}
    assert profile["contract_expires"] == "2029-06-30"


def test_player_career_sections_competition_and_totals():
    soup = P.soup(load("player_profile.html"))
    sections = players._career_rows(soup)
    assert [s["name"] for s in sections][:2] == ["League", "Domestic Cups"]
    season = sections[0]["seasons"][0]
    # The competition cell must not pick up the team (both carry a
    # `careerTab__competitionHref` class inside).
    assert season["competition"]["name"] == "Ligue 1"
    assert season["team"]["name"] == "PSG"
    assert season["rating"] == 6.5 and season["matches_played"] == 1
    totals = sections[0]["totals"]
    assert totals["matches_played"] == 142 and totals["goals"] == 9


def test_player_transfers_and_injuries():
    soup = P.soup(load("player_profile.html"))
    transfers = players._transfer_rows(soup)
    assert transfers and transfers[0]["date"] == "2022-07-01"
    assert transfers[0]["from_team"]["name"] == "Sporting CP"
    assert transfers[0]["to_team"]["name"] == "PSG"
    assert transfers[0]["fee"] == {"amount": 38000000, "currency": "EUR"}
    injuries = players._injury_rows(soup)
    assert injuries and injuries[0]["from_date"] == "2026-08-13"
    assert injuries[0]["type"]


# ---- refs --------------------------------------------------------------------------

def test_match_ref_accepts_every_link_form():
    assert refs.resolve_match("GCxZ2uHc") == "GCxZ2uHc"
    assert refs.resolve_match("https://www.flashscore.com/match/GCxZ2uHc/") == "GCxZ2uHc"
    assert refs.resolve_match(
        "https://www.flashscore.com/match/football/GCxZ2uHc/") == "GCxZ2uHc"
    assert refs.resolve_match(
        "https://www.flashscore.com/match/real-madrid-W8mj7MDD/"
        "sevilla-h8oAv4Ts/?mid=GCxZ2uHc") == "GCxZ2uHc"


def test_a_competition_link_is_never_read_as_an_entity_id():
    for bad in ("https://www.flashscore.com/football/spain/laliga/", "football", "handball"):
        for resolver in (refs.resolve_match, refs.resolve_team, refs.resolve_player):
            try:
                resolver(bad)
            except ValueError:
                continue
            raise AssertionError(f"{resolver.__name__} wrongly accepted {bad!r}")


def test_entity_refs_keep_the_slug_from_a_link():
    assert refs.resolve_team(
        "https://www.flashscore.com/team/real-madrid/W8mj7MDD/") == {
        "id": "W8mj7MDD", "slug": "real-madrid"}
    assert refs.resolve_player("ne2xCTJj") == {"id": "ne2xCTJj", "slug": None}


def test_resolvers_are_idempotent():
    """The marshmallow field resolves once and the endpoint resolves again."""
    for resolver, value in ((refs.resolve_team, "W8mj7MDD"),
                            (refs.resolve_player, "ne2xCTJj"),
                            (refs.resolve_match, "GCxZ2uHc"),
                            (refs.resolve_sport, "football"),
                            (refs.resolve_article, "rwyTTY9T")):
        once = resolver(value)
        assert resolver(once) == once
    pair = refs.resolve_tournament("QeI1Oeyi:dWdJXP6U")
    assert refs.resolve_tournament(pair) == pair
    path = refs.resolve_tournament("/football/spain/laliga/")
    assert refs.resolve_tournament(path) == path


def test_tournament_ref_forms():
    assert refs.resolve_tournament("/football/spain/laliga/")["path"] == \
        "/football/spain/laliga/"
    # trailing tab segments normalise away so /results/ and / agree
    assert refs.resolve_tournament(
        "https://www.flashscore.com/football/spain/laliga/results/")["path"] == \
        "/football/spain/laliga/"
    pair = refs.resolve_tournament("QeI1Oeyi:dWdJXP6U")
    assert pair["tournament_id"] == "QeI1Oeyi" and pair["stage_id"] == "dWdJXP6U"


def test_sport_ref_accepts_id_slug_name_and_alias():
    for value in (1, "1", "football", "Football", "soccer"):
        assert refs.resolve_sport(value) == 1
    assert refs.resolve_sport("american-football") == 5
    assert refs.sport_ref(2) == {"id": 2, "name": "Tennis", "slug": "tennis"}


def test_standings_tab_mapping():
    assert refs.standings_tab("overall") == "1"
    assert refs.standings_tab("form") == "5:0"
    assert refs.standings_tab("over-under", "2.5") == "6:3"
    assert refs.standings_tab("ht-ft") == "13"


def test_stage_names_cover_every_sport_we_serve():
    assert refs.stage_name(3) == "Finished"
    assert refs.stage_name(13) == "2nd Half"
    assert refs.stage_name(18) == "Set 2"          # tennis
    assert refs.stage_name(24) == "3rd Quarter"    # basketball
    assert refs.stage_name(26) == "1st Inns"       # cricket


# ---- feed-backed sections ------------------------------------------------------------

def test_team_transfers_tree():
    tree = P.parse_tree(load("team_transfers.txt"))
    rows = list(P.iter_nodes(tree, {"RTT"}))
    assert rows, "expected transfer rows"
    clubs = [n for n in rows[0]["children"] if n["type"] == "TEA"]
    assert len(clubs) == 2
    assert rows[0]["props"]["TD"] in ("in", "out")


def test_team_news_payload():
    payload = teams.news_payload(load("team_news.txt"))
    assert payload["count"] > 5
    assert payload["articles"][0]["title"]
    assert payload["articles"][0]["link"].startswith("https://www.flashscore.com/")


def test_standings_tab_list_feed():
    records = P.parse_records(load("standings_tabs.txt"))
    tabs = next(r["TB"] for r in records if r.get("TB"))
    assert "1" in tabs.split(",") and "10" in tabs.split(",")
