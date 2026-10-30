"""Prometheus metrics for CTFd.

Adds an authenticated ``GET /metrics`` endpoint serving the Prometheus text
exposition format. Every value is read from the CTFd database while the
scrape is being answered, so the plugin keeps no counters of its own: it
needs no warm-up, it cannot drift from the database, and it stays correct
with more than one gunicorn worker, where in-process counters would each
hold a different partial view.

The endpoint reports the *unfrozen* truth, hidden accounts and all, which
is what organizers need and exactly what players must not see. It is
therefore closed unless the scraper proves itself, see ``auth.authorized``.

The metrics themselves live in one module per section — ``ctf``,
``challenges``, ``accounts``, ``users``, ``submissions`` — which
``collector`` composes.
"""

from CTFd.models import db
from flask import Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

from .auth import TOKEN_ENV, authorized, expected_token
from .collector import CTFdCollector
from .version import __version__

__all__ = ["__version__", "load"]

METRICS_PATH = "/metrics"


def load(app):
    # A registry of our own rather than the process-wide default one, which
    # would also serve the interpreter's own process and GC metrics. Those
    # describe one gunicorn worker picked at random per scrape, so they
    # would be noise here.
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
            # A scrape only reads, so end its transaction instead of leaving
            # one open with a stale snapshot for the next request on this
            # connection to inherit.
            db.session.close()

        return Response(payload, content_type=CONTENT_TYPE_LATEST)
