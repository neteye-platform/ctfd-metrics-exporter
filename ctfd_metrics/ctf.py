from CTFd.utils import get_config
from CTFd.utils.config import ctf_theme
from prometheus_client.core import GaugeMetricFamily

from .common import as_timestamp
from .version import __version__


def ctf_metrics():
    build = GaugeMetricFamily(
        "ctfd_build_info",
        "CTFd version, plugin version and scoring mode. Always 1.",
        labels=["ctfd_version", "plugin_version", "user_mode", "theme"],
    )
    build.add_metric(
        [
            str(get_config("ctf_version") or ""),
            __version__,
            str(get_config("user_mode") or ""),
            ctf_theme(),
        ],
        1,
    )

    paused = GaugeMetricFamily(
        "ctfd_ctf_paused",
        "1 while the CTF is paused in the CTFd configuration, 0 otherwise.",
    )
    paused.add_metric([], 1 if get_config("paused") else 0)

    metrics = [build, paused]

    # Unset times emit nothing: 0 would plot as 1970
    for key, description in (
        ("start", "Configured CTF start time"),
        ("end", "Configured CTF end time"),
        ("freeze", "Configured scoreboard freeze time"),
    ):
        timestamp = as_timestamp(get_config(key))
        if timestamp is None:
            continue
        gauge = GaugeMetricFamily(
            f"ctfd_ctf_{key}_timestamp_seconds",
            f"{description}, as a unix timestamp.",
        )
        gauge.add_metric([], timestamp)
        metrics.append(gauge)

    return metrics
