from CTFd.models import db
from flask import Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

from .auth import TOKEN_ENV, authorized, expected_token
from .collector import CTFdCollector
from .version import __version__

__all__ = ["__version__", "load"]

METRICS_PATH = "/metrics"


def load(app):
    # Not the default registry: its process metrics are per gunicorn worker
    registry = CollectorRegistry()
    registry.register(CTFdCollector())

    if not expected_token():
        app.logger.warning(
            "%s is unset: %s stays closed to everything but an admin session.",
            TOKEN_ENV,
            METRICS_PATH,
        )

    @app.route(METRICS_PATH, methods=["GET"])
    def ctfd_metrics():
        if not authorized():
            return Response(
                "Unauthorized\n",
                status=401,
                headers={"WWW-Authenticate": 'Bearer realm="CTFd metrics"'},
            )

        try:
            payload = generate_latest(registry)
        finally:
            # Don't leave a stale read snapshot to the next request
            db.session.close()

        return Response(payload, content_type=CONTENT_TYPE_LATEST)
