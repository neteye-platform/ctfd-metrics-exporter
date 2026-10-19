"""Who is allowed to read the metrics."""

import hmac
import os

from CTFd.utils.user import is_admin
from flask import request

#: Shared secret the scraper has to present as ``Authorization: Bearer``.
TOKEN_ENV = "CTFD_METRICS_TOKEN"


def expected_token():
    return os.environ.get(TOKEN_ENV, "").strip()


def _presented_token():
    """Return the bearer token from the request, or an empty string."""
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()


def authorized():
    """Decide whether this request may read the metrics.

    Two ways in: the shared token, which is what Prometheus uses, and an
    admin session, which only exists so a human can open the endpoint in a
    browser without digging the token out of the environment.

    With no token configured the endpoint stays shut for everyone but
    admins. Failing closed matters more than a working scrape here, because
    the body is the live scoreboard.
    """
    if is_admin():
        return True

    expected = expected_token()
    if not expected:
        return False

    # compare_digest over bytes: the str form rejects non-ASCII tokens.
    return hmac.compare_digest(
        _presented_token().encode("utf-8"), expected.encode("utf-8")
    )
