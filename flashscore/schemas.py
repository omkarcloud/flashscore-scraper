"""Marshmallow request schemas for every /flashscore/* route.

Generic fields live in the shared top-level schema_fields.py; this module
adds the Flashscore resolvers and the per-route schemas. Every schema's
load() output is the kwargs dict its endpoint function takes.

ONE param per input (tripadvisor QueryOrIdField convention, never a sibling
`url`/`id` pair):

    match       a match id OR any flashscore.com match link
    team        a team id OR a team link
    player      a player link (or a bare id where the endpoint only needs one)
    tournament  a competition link OR a "<tournament_id>:<stage_id>" pair
    sport       an id, a slug or a name (1 | football | Football | soccer)
    ranking     a ranking id OR a rankings page link
    article     an article id OR a news link
"""
from marshmallow import ValidationError, validate, validates_schema

from schema_fields import (BaseSchema, ChoiceField, LimitField, PageField,
                           QueryField, RefField, StrippedString)
from flashscore import refs


# ---- id-or-link fields -------------------------------------------------------------------

class MatchRefField(RefField):
    resolver = staticmethod(refs.resolve_match)


class TeamRefField(RefField):
    resolver = staticmethod(refs.resolve_team)


class PlayerRefField(RefField):
    resolver = staticmethod(refs.resolve_player)


class TournamentRefField(RefField):
    resolver = staticmethod(refs.resolve_tournament)


class RankingRefField(RefField):
    resolver = staticmethod(refs.resolve_ranking)


class ArticleRefField(RefField):
    resolver = staticmethod(refs.resolve_article)


class SportField(StrippedString):
    """Sport as an id, slug or name -> the numeric id."""

    def __init__(self, required=True, **kwargs):
        kwargs.setdefault("required", required)
        if not required:
            kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        try:
            return refs.resolve_sport(value)
        except ValueError as e:
            raise ValidationError(str(e))


class TimezoneField(StrippedString):
    """An IANA name (Europe/Berlin) or a whole-hour offset (-4)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)


def _standings_type():
    return ChoiceField(sorted(refs.STANDINGS_TYPES), load_default="overall")


def _over_under_line():
    return ChoiceField(sorted(refs.OVER_UNDER_LINES), load_default="all")


# ---- discovery -----------------------------------------------------------------------------

class EmptySchema(BaseSchema):
    pass


class SportSchema(BaseSchema):
    sport = SportField()


class CountryTournamentsSchema(BaseSchema):
    sport = SportField()
    country = StrippedString(required=True, validate=validate.Length(min=1, max=80))


class SearchSchema(BaseSchema):
    query = QueryField()
    type = ChoiceField(["team", "player", "tournament"])
    sport = SportField(required=False)
    limit = LimitField(default=20, max_size=50)


class ResolveSchema(BaseSchema):
    link = StrippedString(required=True, validate=validate.Length(min=3, max=400))


# ---- match lists ----------------------------------------------------------------------------

class MatchListSchema(BaseSchema):
    sport = SportField()
    day = ChoiceField([str(d) for d in range(-7, 8)], load_default="0",
                      metadata={"description": "day offset from today, -7 to 7"})
    date = StrippedString(required=False, load_default=None,
                          validate=validate.Regexp(r"^\d{4}-\d{2}-\d{2}$",
                                                   error="Must be YYYY-MM-DD."))
    timezone = TimezoneField()
    status = ChoiceField(["scheduled", "live", "finished"])

    @validates_schema
    def _one_of_day_or_date(self, data, **kwargs):
        if data.get("date") and data.get("day") not in (None, "0"):
            raise ValidationError("pass either day or date, not both.", "date")


class LiveMatchesSchema(BaseSchema):
    sport = SportField()
    timezone = TimezoneField()


class LiveUpdatesSchema(BaseSchema):
    sport = SportField()


# ---- match detail -----------------------------------------------------------------------------

class MatchSchema(BaseSchema):
    match = MatchRefField()


class MatchStatisticsSchema(MatchSchema):
    period = StrippedString(required=False, load_default=None,
                            metadata={"description": 'e.g. "Match", "1st Half", "Set 1"'})


class MatchPointByPointSchema(MatchSchema):
    set = StrippedString(required=False, load_default=None,
                         metadata={"description": 'set number or name, e.g. 2 or "Set 2"'})


class MatchStandingsSchema(MatchSchema):
    type = _standings_type()
    line = _over_under_line()


class MatchOddsSchema(MatchSchema):
    # ChoiceField lower-cases plain lists, so the upstream's SCREAMING_CASE
    # tokens are declared as an explicit public -> upstream map.
    bet_type = ChoiceField({t.lower().replace("_", "-"): t for t in refs.BETTING_TYPES})
    bet_scope = ChoiceField({s.lower().replace("_", "-"): s for s in refs.BETTING_SCOPES})
    geo = StrippedString(required=False, load_default="US",
                         validate=validate.Length(min=2, max=2),
                         metadata={"description": "ISO country whose bookmakers to price"})


class MatchBroadcastsSchema(MatchSchema):
    geo = StrippedString(required=False, load_default=None,
                         validate=validate.Length(min=2, max=2))


# ---- tournaments -------------------------------------------------------------------------------

class TournamentSchema(BaseSchema):
    tournament = TournamentRefField()


class TournamentPageSchema(TournamentSchema):
    page = PageField(max_page=200)


class TournamentStandingsSchema(TournamentSchema):
    type = _standings_type()
    line = _over_under_line()


# ---- teams ---------------------------------------------------------------------------------------

class TeamSchema(BaseSchema):
    team = TeamRefField()


class TeamPageSchema(TeamSchema):
    page = PageField(max_page=200)


class TeamTransfersSchema(TeamSchema):
    direction = ChoiceField(sorted(refs.TRANSFER_TABS), load_default="all")
    page = PageField(max_page=50)


class TeamStandingsSchema(TeamSchema):
    type = _standings_type()
    line = _over_under_line()


# ---- players ----------------------------------------------------------------------------------------

class PlayerSchema(BaseSchema):
    player = PlayerRefField()


# ---- rankings / news ------------------------------------------------------------------------------------

class RankingsSchema(BaseSchema):
    sport = SportField()


class RankingSchema(BaseSchema):
    ranking = RankingRefField()
    page = PageField(max_page=50)


class NewsSchema(BaseSchema):
    sport = SportField()


class ArticleSchema(BaseSchema):
    article = ArticleRefField()
