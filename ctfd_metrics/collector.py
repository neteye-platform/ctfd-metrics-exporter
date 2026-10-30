"""The collector that composes the metric sections."""

import time

from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from .accounts import account_metrics
from .challenges import challenge_metrics
from .ctf import ctf_metrics
from .submissions import submission_metrics
from .users import user_metrics


class CTFdCollector(Collector):
    """Answers a scrape by querying the database, holding no state."""

    def collect(self):
        started = time.monotonic()

        yield from ctf_metrics()
        yield from challenge_metrics()
        yield from account_metrics()
        yield from user_metrics()
        yield from submission_metrics()

        # Last, so it covers all the queries above. Worth watching: it is
        # the warning that the endpoint is becoming a load on the CTF.
        duration = GaugeMetricFamily(
            "ctfd_metrics_scrape_duration_seconds",
            "Seconds the plugin spent on the database for this scrape.",
        )
        duration.add_metric([], time.monotonic() - started)
        yield duration
