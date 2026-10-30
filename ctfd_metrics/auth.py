import hmac
import os

from CTFd.utils.user import is_admin
from flask import request

TOKEN_ENV = "CTFD_METRICS_TOKEN"


def expected_token():
    return os.environ.get(TOKEN_ENV, "").strip()


def _presented_token():
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()


def authorized():
    if is_admin():
        return True

    expected = expected_token()
    if not expected:
        return False

    # bytes: compare_digest rejects non-ASCII str
    return hmac.compare_digest(
        _presented_token().encode("utf-8"), expected.encode("utf-8")
    )
