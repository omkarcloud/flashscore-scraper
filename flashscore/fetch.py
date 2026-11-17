"""Flashscore transport: plain curl_cffi with browser-impersonated TLS. No
browser, no cookies, no proxy needed (validated 2026-09-18 from direct
egress and through residential proxy exits).

Four upstream surfaces, all open:

  * FEED  https://global.flashscore.ninja/2/x/feed/<name>
    The site's own data protocol. Needs ONE header, `x-fsign` (401 without
    it); the value is printed in every page as `feed_sign` and has been
    SW9D1eZo for years — config.FLASHSCORE_FEED_SIGN seeds it and a 401
    re-reads it from the homepage, so a rotation self-heals.
    Body is text, not JSON: records split on "~", pairs on "¬", key/value
    on "÷" (parsers.py). An empty body, "0" or "A1÷¬~" all mean "no data".

  * GQL   https://2.ds.lsapp.eu/pq_graphql?_hash=<hash>&...
          https://global.ds.lsapp.eu/odds/pq_graphql?_hash=<hash>&...
    Persisted queries addressed by a short hash, plain GET, JSON, no auth.
    STRICT about parameters: one extra query param and Varnish answers a
    400 HTML page instead of JSON, so every call is built from an explicit
    allow-list (never **kwargs passthrough).

  * SEARCH https://s.livesport.services/api/v2/search/  — plain JSON.

  * HTML  https://www.flashscore.com/...  — server-rendered pages. Used for
    what no feed carries: team/player/league headers, squads, player career
    tables, and the `window.environment` + `cjs.initialFeeds[...]` blobs
    that hold the ids the feeds need (sport id, numeric season id, the
    (tournament_id, stage_id) pair per season).

Two id quirks that shape the routes (both verified 2026-09-18):
  * the sport id inside `dc_<sport>_<match>` / `df_*_<sport>_<match>` is
    IGNORED by the server — any integer returns the same body, so a bare
    match id is a complete reference and no sport lookup is needed.
  * the country id inside `pr_/pf_/tr_/tf_` is likewise ignored, but the
    SPORT id in `pr_/pf_` (team feeds) is real — a wrong sport returns an
    empty body. lookup.py resolves it from the team page.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  FlashscoreUpstreamError  transport failure / 5xx        — retryable
  FlashscoreBlocked        401 / 403 / 429 / HTML for JSON — retryable
  FlashscoreBadRequest     upstream 400 (bad hash params)  — never retried
  FlashscoreNotFound       404 / empty entity              — never retried
"""
import os
import re
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_futures

# Allow direct execution (python flashscore/fetch.py): flat imports resolve
# like under the server. Idempotent when imported normally.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import egress
import identity_pool
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from scraper_utils import debug, fanout, sessions

SITE = "https://www.flashscore.com"
FEED = "https://global.flashscore.ninja/2/x/feed/"
GQL = "https://2.ds.lsapp.eu/pq_graphql"
ODDS_GQL = "https://global.ds.lsapp.eu/odds/pq_graphql"
SEARCH = "https://s.livesport.services/api/v2/search/"
IMPERSONATE = "chrome"

FEED_TIMEOUT = 45      # a day list is ~1 MB (1-2 s normally)
JSON_TIMEOUT = 45      # epmsd player stats are ~1 MB
PAGE_TIMEOUT = 60      # pages are 0.7-1.4 MB
FANOUT_WORKERS = 4     # parallel upstream calls for endpoints that stitch several

# Bodies that mean "this entity has no data for this tab" rather than an error.
# (HTTP 204 means the same thing on the live diff feeds and is handled in
# _classify — a quiet minute with no score changes is not a failure.)
EMPTY_BODIES = ("", "0", "A1÷¬~")

FEED_HEADERS = {
    "accept": "*/*",
    "accept-language": egress.site_accept_language("flashscore"),
    "referer": SITE + "/",
    "origin": SITE,
}
JSON_HEADERS = {
    "accept": "*/*",
    "accept-language": egress.site_accept_language("flashscore"),
    "referer": SITE + "/",
    "origin": SITE,
}
PAGE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": egress.site_accept_language("flashscore"),
    "upgrade-insecure-requests": "1",
}


class FlashscoreUpstreamError(UpstreamError):
    """Transport failure or 5xx — retryable."""


class FlashscoreBlocked(FlashscoreUpstreamError, Blocked):
    """401 / 403 / 429 or an HTML body where JSON was expected — retryable."""


class FlashscoreBadRequest(BadRequest):
    """Upstream 400 — the persisted-query params were rejected. Never retried."""


class FlashscoreNotFound(NotFound):
    """Entity / page does not exist — never retried."""


# ---- sessions ------------------------------------------------------------------
# One curl session per worker thread (a curl handle must not be shared across
# threads). Flashscore does not rate-limit this traffic, so a session lives
# until it fails; config.flashscore_proxy() is None by default (direct).
_local = threading.local()


def _session():
    sess = getattr(_local, "session", None)
    used = getattr(_local, "used", 0)
    if sess is not None and config.FLASHSCORE_REQUESTS_PER_EXIT and \
            used >= config.FLASHSCORE_REQUESTS_PER_EXIT:
        _drop_session()
        sess = None
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        proxy = config.flashscore_proxy()
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        _local.session = sess
        _local.used = 0
    _local.used = getattr(_local, "used", 0) + 1
    return sess


def _drop_session():
    """Close the thread's session so a poisoned exit / keep-alive dies."""
    sessions.drop(_local)


def dump_debug(name, text):
    """Write a raw response to $FLASHSCORE_DEBUG_DIR/<name>.txt."""
    debug.dump("FLASHSCORE_DEBUG_DIR", name, text)


# ---- feed signature --------------------------------------------------------------
# The feed host answers 401 without `x-fsign`. The token is a build constant
# printed in every page. It lives in a shared identity_pool (seeded from
# config, replaced the hand-rolled `_sign` global + lock on 2026-11-09): a
# 401 wave across the 64 request threads costs ONE homepage read, the other
# threads get the winner's value, and a second refresh inside
# SIGN_REFRESH_INTERVAL returns the same token (a block is not a rotation).
_SIGN_RE = re.compile(r"feed_sign\s*[:=]\s*[\"']([A-Za-z0-9_-]{4,32})[\"']")
SIGN_REFRESH_INTERVAL = 30     # seconds between two homepage re-reads


def _mint_sign(key, hint):
    """The pool's mint: read feed_sign from the homepage. Raises
    FlashscoreUpstreamError when the page does not carry it."""
    sess = _session()
    resp = sess.get(SITE + "/", headers=PAGE_HEADERS, timeout=PAGE_TIMEOUT)
    match = _SIGN_RE.search(resp.text or "")
    if not match:
        raise FlashscoreUpstreamError("feed_sign not found on the homepage")
    return match.group(1)


_signs = identity_pool.Pool("flashscore-feed-sign", _mint_sign, min_interval=SIGN_REFRESH_INTERVAL,
                            seed={"": config.FLASHSCORE_FEED_SIGN})


def feed_sign():
    """The current x-fsign value (the seed until a 401 ever rotates it)."""
    return _signs.get().value


def _refresh_sign(used):
    """`used` (the pool identity whose value just got a 401) is stale: ask the
    pool for its replacement. One harvest per 401 wave (compare-and-swap)
    and at most one per SIGN_REFRESH_INTERVAL. Returns the new value, or
    None when the token is unchanged or the harvest failed (same contract
    as the old global: the caller retries with whatever feed_sign() says)."""
    current = used.value
    try:
        fresh = _signs.get(replace=used)
    except Exception as e:
        print(f"flashscore: feed_sign refresh failed: {type(e).__name__}: {e}")
        return None
    if fresh.value == current:
        return None            # same token — a refresh would not help
    print(f"flashscore: feed_sign rotated {current} -> {fresh.value}")
    return fresh.value


# ---- requests --------------------------------------------------------------------

def _classify(resp, label):
    status = resp.status_code
    if status == 204:
        return              # "nothing changed" — the live diff feeds use this
    if status == 404:
        raise FlashscoreNotFound(f"{label} not found")
    if status in (401, 403, 429):
        dump_debug("blocked", resp.text)
        raise FlashscoreBlocked(f"HTTP {status} on {label}")
    if status == 400:
        raise FlashscoreBadRequest(f"upstream rejected {label}")
    if status >= 500 or status != 200:
        raise FlashscoreUpstreamError(f"HTTP {status} on {label}")


def _get(url, *, params=None, headers, timeout, label):
    sess = _session()
    try:
        resp = sess.get(url, params=params, headers=headers, timeout=timeout,
                        allow_redirects=True)
    except Exception as e:
        raise FlashscoreUpstreamError(f"request failed: {type(e).__name__}: {e}")
    _classify(resp, label)
    return resp


def _retrying(fn, *, on_unauthorized=None):
    """Shared retry policy: transport errors drop the session and back off.
    `on_unauthorized` runs once before the final attempt of a Blocked loop
    (used to refresh the feed signature)."""
    last = None
    refreshed = False
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return fn()
        except FlashscoreBlocked as e:
            last = e
            _drop_session()
            if on_unauthorized is not None and not refreshed:
                refreshed = True
                on_unauthorized()
        except FlashscoreUpstreamError as e:
            last = e
            _drop_session()
        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_BACKOFF * attempt)
    raise last


def get_feed(name, *, optional=False):
    """GET one /x/feed/<name> -> its raw text.

    Returns "" for the upstream's several ways of saying "nothing here"
    (empty body, "0", "A1÷¬~"). With optional=False a 404 raises NotFound;
    with optional=True it also returns "" (tabs that simply do not exist
    for a sport must not 404 the whole endpoint)."""
    url = FEED + name
    used = [None]              # the sign identity the last attempt sent

    def once():
        used[0] = _signs.get()
        headers = dict(FEED_HEADERS, **{"x-fsign": used[0].value})
        resp = _get(url, headers=headers, timeout=FEED_TIMEOUT, label=f"feed {name}")
        return resp.text

    try:
        text = _retrying(once, on_unauthorized=lambda: _refresh_sign(used[0]))
    except FlashscoreNotFound:
        if optional:
            return ""
        raise
    text = (text or "").strip()
    return "" if text in EMPTY_BODIES else text


def _json_of(resp, label):
    body = resp.text or ""
    if not body.lstrip().startswith(("{", "[")):
        dump_debug("nonjson", body)
        # Varnish answers rejected persisted-query params with an HTML 400 body
        # under a 200/400 status depending on the edge — treat HTML as a param
        # error, not a block, so it is never retried in a loop.
        raise FlashscoreBadRequest(f"{label} returned HTML, not JSON "
                                   "(persisted-query parameters rejected)")
    try:
        return resp.json()
    except Exception:
        dump_debug("badjson", body)
        raise FlashscoreUpstreamError(f"could not parse JSON from {label}")


def get_graphql(hash_name, params, *, odds=False):
    """GET one persisted query. `params` is an explicit dict — the upstream
    rejects unknown keys with a 400 HTML page, so callers pass exactly the
    documented set for that hash."""
    url = ODDS_GQL if odds else GQL
    query = {"_hash": hash_name}
    query.update({k: v for k, v in params.items() if v is not None})

    def once():
        resp = _get(url, params=query, headers=JSON_HEADERS, timeout=JSON_TIMEOUT,
                    label=f"graphql {hash_name}")
        return _json_of(resp, f"graphql {hash_name}")

    payload = _retrying(once)
    if isinstance(payload, dict) and payload.get("errors") and not payload.get("data"):
        message = (payload["errors"][0] or {}).get("message") if payload["errors"] else None
        raise FlashscoreBadRequest(message or f"graphql {hash_name} failed")
    return (payload or {}).get("data") or {}


def get_graphql_optional(hash_name, params, *, odds=False):
    """Like get_graphql but a rejected/absent query yields {}."""
    try:
        return get_graphql(hash_name, params, odds=odds)
    except (FlashscoreBadRequest, FlashscoreNotFound):
        return {}


def get_search(query, *, type_ids, sport_ids=None, language_id=1):
    """GET the autocomplete service -> a list of entity dicts."""
    params = {
        "q": query,
        "lang-id": language_id,
        "type-ids": ",".join(str(t) for t in type_ids),
        "project-id": 2,
        "project-type-id": 1,
    }
    if sport_ids:
        params["sport-ids"] = ",".join(str(s) for s in sport_ids)

    def once():
        resp = _get(SEARCH, params=params, headers=JSON_HEADERS, timeout=JSON_TIMEOUT,
                    label="search")
        return _json_of(resp, "search")

    payload = _retrying(once)
    return payload if isinstance(payload, list) else []


def get_page(path, *, expect=None, optional=False):
    """GET one flashscore.com page -> HTML text.

    `path` starts with "/". `expect` is a fragment the FINAL url must still
    contain — the site answers some unknown ids with a 200 redirect to a
    landing page rather than a 404."""
    url = SITE + path

    def once():
        resp = _get(url, headers=PAGE_HEADERS, timeout=PAGE_TIMEOUT, label=path)
        final = resp.url or ""
        if expect and expect not in final:
            raise FlashscoreNotFound(f"{path} not found")
        return resp.text

    try:
        return _retrying(once)
    except FlashscoreNotFound:
        if optional:
            return ""
        raise


# Long-lived pools (2026-10-23): their threads keep their thread-local curl
# sessions — and the sticky exits behind them — between requests. The
# per-call executor they replace opened fresh sessions/exits for every
# fan-out and threw them away when the call returned. Two levels: a fan-out
# running inside a fan-out goes to the second pool, so it neither waits on
# the workers it occupies (deadlock) nor loses its parallelism; a third
# level runs inline.
_pools = (ThreadPoolExecutor(max_workers=FANOUT_WORKERS * 4, thread_name_prefix="flashscore-fanout-0"),
          ThreadPoolExecutor(max_workers=FANOUT_WORKERS * 4, thread_name_prefix="flashscore-fanout-1"))


def _windowed(fns, workers):
    """Run `fns` on the pool for this nesting level, at most `workers` at a
    time; results align with `fns`. The first failure (in `fns` order)
    propagates once the callables already started have finished."""
    return fanout.windowed(_pools, "flashscore-fanout", fns, workers)


def run_parallel(fns):
    """Run zero-arg callables in parallel; results align with `fns`.
    Exceptions propagate from the first failing call."""
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]
    return _windowed(fns, FANOUT_WORKERS)


def gather(mapping):
    """Run {key: callable} in parallel -> {key: result}, where a NotFound or
    BadRequest for one key yields None instead of failing the whole set.
    Used by endpoints that stitch several optional tabs together."""
    return fanout.gather(run_parallel, mapping, (FlashscoreNotFound, FlashscoreBadRequest))


if __name__ == "__main__":
    # Smoke test: python flashscore/fetch.py [feed name]
    target = sys.argv[1] if len(sys.argv) > 1 else "f_1_0_5_en_1"
    text = get_feed(target)
    print(f"feed {target}: {len(text)} bytes")
    print(text[:400])
    page = get_page("/match/GCxZ2uHc/", expect="GCxZ2uHc")
    print(f"match page: {len(page)} bytes")
