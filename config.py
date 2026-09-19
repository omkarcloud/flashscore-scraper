"""Configuration for the Flashscore Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT              port the API listens on (default 8000)
    FLASHSCORE_PROXY  proxy URL for every request, e.g. http://user:pass@host:port
                      (default: none — direct). Flashscore runs no bot
                      protection and did not rate-limit hundreds of sequential
                      requests from one IP, so you very likely don't need this.
                      Set it only if you start seeing failures at high volume.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

# Rotate the HTTP session after this many requests. 0 keeps one session for
# the life of the process, which is what you want without a proxy.
FLASHSCORE_REQUESTS_PER_EXIT = 0

# Flashscore's feed host answers 401 without the `x-fsign` header. The value is
# a build constant printed in every page; it has been stable for years, and
# flashscore/fetch.py re-reads it from the homepage if it ever stops working,
# so this is only the seed.
FLASHSCORE_FEED_SIGN = "SW9D1eZo"

FLASHSCORE_PROXY = os.environ.get("FLASHSCORE_PROXY") or None


def flashscore_proxy():
    """The proxy every request goes through, or None for direct egress."""
    return FLASHSCORE_PROXY
