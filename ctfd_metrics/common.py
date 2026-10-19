"""Helpers shared by more than one metric section."""

import os

from CTFd.models import db

#: Caps how many ``ctfd_account_score`` series a scrape emits. Unset or 0
#: means every player account, which is the right default for a CTF with
#: tens of teams and a footgun for one with thousands of users.
TOP_ACCOUNTS_ENV = "CTFD_METRICS_TOP_ACCOUNTS"


def top_accounts():
    """Return the per-account series limit as a slice bound (None = all)."""
    try:
        count = int(os.environ.get(TOP_ACCOUNTS_ENV, "0"))
    except ValueError:
        count = 0
    return count if count > 0 else None


def players(model):
    """Criteria keeping only the accounts that are actually playing.

    Hidden accounts are what organizers solve challenges from while testing
    them, and banned ones are out of the game, so neither should move a
    metric. This is the same filter CTFd applies to its own statistics
    endpoints, which keeps the numbers here consistent with the admin panel.

    CTFd spells this `== False`, which renders the same SQL but is a shape
    linters like to rewrite into a plain `not`, and that would evaluate in
    Python instead of in the database.
    """
    return (db.not_(model.banned), db.not_(model.hidden))


def as_timestamp(value):
    """CTFd keeps the CTF times as stringified unix timestamps."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
