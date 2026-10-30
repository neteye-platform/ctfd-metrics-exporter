import os

from CTFd.models import db

TOP_ACCOUNTS_ENV = "CTFD_METRICS_TOP_ACCOUNTS"


def top_accounts():
    try:
        count = int(os.environ.get(TOP_ACCOUNTS_ENV, "0"))
    except ValueError:
        count = 0
    return count if count > 0 else None


def players(model):
    # db.not_, not `not`: must stay SQL
    return (db.not_(model.banned), db.not_(model.hidden))


def as_timestamp(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
