"""flashscore/fetch.py's feed_sign on identity_pool: no network, the homepage
harvest is faked. The pool's own rules live in tests/test_identity_pool.py.

    python -m pytest flashscore/test_fetch.py -q
"""
import threading
import time

import pytest

from flashscore import fetch


class FakeResponse:
    def __init__(self, status=200, text=""):
        self.status_code, self.text, self.url = status, text, fetch.FEED


@pytest.fixture
def signs(monkeypatch):
    """The module's pool with a counting fake harvest and no retry sleeps."""
    pool = fetch._signs
    pool.clear()
    pool._last_mint.clear()
    pool._mint_times.clear()
    harvests = []

    def harvest(key, hint):
        time.sleep(0.05)                     # let the wave pile up behind one mint
        harvests.append(key)
        return "ROTATED1"

    monkeypatch.setattr(pool, "_mint_fn", harvest)
    monkeypatch.setattr(fetch.config, "RETRY_BACKOFF", 0)
    pool.harvests = harvests
    yield pool
    pool.clear()
    pool._last_mint.clear()
    pool._mint_times.clear()


def test_a_401_wave_costs_one_harvest_and_none_inside_the_interval(signs, monkeypatch):
    accepted = {"ROTATED1"}

    def fake_get(url, *, params=None, headers, timeout, label):
        if headers["x-fsign"] not in accepted:
            raise fetch.FlashscoreBlocked(f"HTTP 401 on {label}")
        return FakeResponse(text="A1÷x¬~")

    monkeypatch.setattr(fetch, "_get", fake_get)
    assert fetch.feed_sign() == fetch.config.FLASHSCORE_FEED_SIGN, "the seed serves before any harvest"

    results, errors = [], []

    def worker():
        try:
            results.append(fetch.get_feed("f_1_0_5_en_1"))
        except Exception as e:                # pragma: no cover - the assert below reports it
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors and len(results) == 8
    assert signs.harvests == [""], "a wave of 401s across threads: ONE homepage read"
    assert fetch.feed_sign() == "ROTATED1"

    # the rotated token now fails too: inside the interval the pool hands
    # back the same token and the request fails as it always did - no harvest
    accepted.clear()
    with pytest.raises(fetch.FlashscoreBlocked):
        fetch.get_feed("f_1_0_5_en_1")
    assert signs.harvests == [""]
    assert fetch.feed_sign() == "ROTATED1"
