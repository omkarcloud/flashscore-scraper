"""Rankings and the site-wide news feed.

Rankings (`ran_`) use the nested TS/TE wire format: a `ME` block lists every
ranking table of that sport as a tab ("ATP|/tennis/rankings/atp/|1"), and each
`GR` block holds one table's rows. The tab list is what makes a bare sport
name enough to discover the ranking ids, which are otherwise unguessable.
"""
import re

from . import parsers as P
from . import refs
from .fetch import FlashscoreNotFound, get_feed, get_graphql_optional, get_page
from .matches import article_from_payload

# The ranking tab that anchors each sport's ranking page — the feed answers
# for any ranking id of the sport, and its `ME` block then lists the rest.
_ANCHOR_RANKINGS = {
    2: "dSJr14Y8",     # tennis (ATP singles) — the page's own default
}


# Every ranking page prints the 8-character id its `ran_` feed takes.
_RANKING_ID_RE = re.compile(r"rankingId\s*=\s*[\"']([A-Za-z0-9]{8})[\"']")
_ranking_ids = {}


def _ranking_tabs(node):
    """`ME` tab entries: "ATP|/tennis/rankings/atp/|1" -> name, link, selected."""
    tabs = []
    for tab in P.iter_nodes(node, {"TAB"}):
        raw = P.clean(tab["props"].get("VA"))
        if not raw:
            continue
        parts = raw.split("|")
        tabs.append({
            "id": P.clean(tab["props"].get("ID")),
            "name": P.clean(parts[0]) if parts else None,
            "link": P.link(parts[1]) if len(parts) > 1 else None,
            "is_selected": (parts[2] == "1") if len(parts) > 2 else False,
        })
    return tabs


def _ranking_id(ref):
    """The `ran_` id for a resolved ranking ref. The menu only publishes a
    numeric tab id and an id-less page link per table, so those two forms
    are mapped to the page and the id is read off it (memoised: a page is
    ~1 MB and the id never changes)."""
    if ref.get("id"):
        return ref["id"]
    path = ref.get("path")
    if not path:
        for anchor in _ANCHOR_RANKINGS.values():
            menu = P.first_node(P.parse_tree(get_feed(f"ran_{anchor}_1", optional=True)), {"ME"})
            for tab in (_ranking_tabs(menu) if menu is not None else []):
                if tab["id"] == ref.get("tab_id") and tab["link"]:
                    path = tab["link"][len(P.SITE):]
                    break
            if path:
                break
        if not path:
            raise FlashscoreNotFound(f"ranking tab {ref.get('tab_id')} not found — "
                                     "see /flashscore/rankings")
    cached = _ranking_ids.get(path)
    if cached:
        return cached
    found = _RANKING_ID_RE.search(get_page(path, expect="/rankings/") or "")
    if not found:
        raise FlashscoreNotFound(f"no ranking table at {path}")
    if len(_ranking_ids) >= 256:
        _ranking_ids.clear()
    _ranking_ids[path] = found.group(1)
    return found.group(1)


def get_rankings(sport):
    """Every ranking table this sport publishes, with the ids
    /flashscore/rankings/data takes."""
    sport_id = refs.resolve_sport(sport)
    anchor = _ANCHOR_RANKINGS.get(sport_id)
    if not anchor:
        raise ValueError(f"{refs.sport_name(sport_id) or sport} publishes no rankings "
                         "on Flashscore — tennis is the sport that does")
    text = get_feed(f"ran_{anchor}_1", optional=True)
    tree = P.parse_tree(text)
    menu = P.first_node(tree, {"ME"})
    tabs = _ranking_tabs(menu) if menu is not None else []
    return {"sport": refs.sport_ref(sport_id), "count": len(tabs), "rankings": tabs}


def get_ranking(ranking, page=1):
    """One ranking table: rank, movement, player, country, points and the
    number of tournaments played."""
    ranking_id = _ranking_id(refs.resolve_ranking(ranking))
    text = get_feed(f"ran_{ranking_id}_{max(1, int(page))}", optional=True)
    tree = P.parse_tree(text)
    header = P.first_node(tree, {"HD"})
    rows = []
    for row in P.iter_nodes(tree, {"RW"}):
        props = row["props"]
        player_id = P.clean(props.get("PI"))
        if not player_id and not props.get("PN"):
            continue
        rank = P.to_int(props.get("RA"))
        previous = P.to_int(props.get("RAP"))
        rows.append({
            "rank": rank,
            "previous_rank": previous,
            "rank_change": (previous - rank) if (rank is not None and previous is not None) else None,
            "player": P.player_ref(player_id, props.get("PN"), link_path=_player_link(props.get("PU"))),
            "country": P.country_ref(props.get("CI"), props.get("CN")),
            "points": P.to_int(props.get("PO")),
            "tournaments_played": P.to_int(props.get("TP")),
        })
    # No total is published anywhere, so this reports a page and whether a
    # further one exists rather than inventing a page count.
    page = max(1, int(page))
    return {
        "id": ranking_id,
        "name": P.clean(header["props"].get("TE")) if header is not None else None,
        "updated_on": P.parse_date(header["props"].get("DA")) if header is not None else None,
        "page": page,
        "has_more_pages": len(rows) >= 100,
        "count": len(rows),
        "rankings": rows,
    }


def _player_link(path):
    """Ranking rows print /player/<slug>/<id> without the trailing slash."""
    path = P.clean(path)
    if not path:
        return None
    return path if path.endswith("/") else path + "/"


def get_news(sport):
    """`nl_<sport>_59` — the sport's news front page, in its own sections."""
    sport_id = refs.resolve_sport(sport)
    text = get_feed(f"nl_{sport_id}_59", optional=True)
    # This one feed is plain JSON rather than the "¬÷" protocol.
    payload = {}
    if text.lstrip().startswith("{"):
        import json
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {}
    sections = []
    for section in ((payload.get("data") or {}).get("sections") or []):
        articles = []
        for article in section.get("articles") or []:
            images = article.get("images") or []
            article_id = P.clean(article.get("id"))
            slug = P.clean(article.get("url"))
            articles.append({
                "id": article_id,
                "title": P.clean(article.get("title")),
                "link": P.link(f"/news/{slug}/{article_id}/") if slug and article_id else None,
                "published_at": P.timestamp(article.get("published")),
                "edited_at": P.timestamp(article.get("editedAt")),
                "image": P.clean((images[0] or {}).get("url")) if images else None,
                "image_credit": P.clean((images[0] or {}).get("credit")) if images else None,
            })
        if not articles:
            continue
        sections.append({"name": P.clean(section.get("name")),
                         "count": len(articles), "articles": articles})
    return {"sport": refs.sport_ref(sport_id),
            "section_count": len(sections),
            "article_count": sum(s["count"] for s in sections),
            "sections": sections}


def get_article(article):
    """One news article by id or by its flashscore.com link."""
    article_id = refs.resolve_article(article)
    payload = get_graphql_optional("nah", {"articleId": article_id})
    parsed = article_from_payload(payload)
    if not parsed:
        raise FlashscoreNotFound(f"article {article_id} not found")
    return parsed
