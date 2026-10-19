"""Prometheus metrics for CTFd.

Adds an authenticated ``GET /metrics`` endpoint serving the Prometheus text
exposition format. Every value is read from the CTFd database while the
scrape is being answered, so the plugin keeps no counters of its own: it
needs no warm-up, it cannot drift from the database, and it stays correct
with more than one gunicorn worker, where in-process counters would each
hold a different partial view.

The endpoint reports the *unfrozen* truth, hidden accounts and all, which
is what organizers need and exactly what players must not see. It is
therefore closed unless the scraper proves itself, see ``_authorized``.
"""

import hmac
import os
import time
from collections import defaultdict

from CTFd.models import (
    Challenges,
    Hints,
    Solves,
    Submissions,
    Teams,
    Unlocks,
    Users,
    db,
)
from CTFd.utils import get_config
from CTFd.utils.config import ctf_theme
from CTFd.utils.modes import get_model
from CTFd.utils.scores import get_standings
from CTFd.utils.user import is_admin
from flask import Response, request
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.registry import Collector

__version__ = "1.1.0"

#: Shared secret the scraper has to present as ``Authorization: Bearer``.
TOKEN_ENV = "CTFD_METRICS_TOKEN"

#: Caps how many ``ctfd_account_score`` series a scrape emits. Unset or 0
#: means every player account, which is the right default for a CTF with
#: tens of teams and a footgun for one with thousands of users.
TOP_ACCOUNTS_ENV = "CTFD_METRICS_TOP_ACCOUNTS"

METRICS_PATH = "/metrics"


def _expected_token():
    return os.environ.get(TOKEN_ENV, "").strip()


def _presented_token():
    """Return the bearer token from the request, or an empty string."""
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()


def _authorized():
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

    expected = _expected_token()
    if not expected:
        return False

    # compare_digest over bytes: the str form rejects non-ASCII tokens.
    return hmac.compare_digest(
        _presented_token().encode("utf-8"), expected.encode("utf-8")
    )


def _top_accounts():
    """Return the per-account series limit as a slice bound (None = all)."""
    try:
        count = int(os.environ.get(TOP_ACCOUNTS_ENV, "0"))
    except ValueError:
        count = 0
    return count if count > 0 else None


def _players(model):
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


def _as_timestamp(value):
    """CTFd keeps the CTF times as stringified unix timestamps."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ctf_metrics():
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

    # These three are simply unset until an organizer fills them in. Drop
    # the series in that case: a 0 would render as 1970 on a dashboard and
    # read as a misconfigured CTF rather than an unconfigured one.
    for key, description in (
        ("start", "Configured CTF start time"),
        ("end", "Configured CTF end time"),
        ("freeze", "Configured scoreboard freeze time"),
    ):
        timestamp = _as_timestamp(get_config(key))
        if timestamp is None:
            continue
        gauge = GaugeMetricFamily(
            f"ctfd_ctf_{key}_timestamp_seconds",
            f"{description}, as a unix timestamp.",
        )
        gauge.add_metric([], timestamp)
        metrics.append(gauge)

    return metrics


def _challenge_metrics():
    model = get_model()

    # Base-table columns only: loading whole Challenges objects would drag
    # in every challenge-type subclass table a plugin has registered, which
    # a scrape has no use for. `value` is a plain column that CTFd rewrites
    # as dynamic challenges decay, so it is already the current price.
    challenges = (
        db.session.query(
            Challenges.id,
            Challenges.name,
            Challenges.category,
            Challenges.state,
            Challenges.value,
        )
        .order_by(Challenges.id)
        .all()
    )

    solves = dict(
        db.session.query(Solves.challenge_id, db.func.count(Solves.id))
        .join(model, Solves.account_id == model.id)
        .filter(*_players(model))
        .group_by(Solves.challenge_id)
        .all()
    )

    # Fails has no table of its own — wrong submissions are rows of
    # `submissions` told apart by the type discriminator — and selecting
    # bare columns off it would not add that discriminator to the SQL. So
    # filter on the type explicitly instead of querying Fails.
    wrong = dict(
        db.session.query(Submissions.challenge_id, db.func.count(Submissions.id))
        .join(model, Submissions.account_id == model.id)
        .filter(Submissions.type == "incorrect", *_players(model))
        .group_by(Submissions.challenge_id)
        .all()
    )

    # Unlocks.target holds the hint id, so the challenge it belongs to only
    # comes out of the join with Hints.
    hint_unlocks = dict(
        db.session.query(Hints.challenge_id, db.func.count(Unlocks.id))
        .select_from(Unlocks)
        .join(Hints, Unlocks.target == Hints.id)
        .join(model, Unlocks.account_id == model.id)
        .filter(Unlocks.type == "hints", *_players(model))
        .group_by(Hints.challenge_id)
        .all()
    )

    labels = ["challenge_id", "name", "category", "state"]

    counts = GaugeMetricFamily(
        "ctfd_challenges",
        "Number of challenges, by category and state.",
        labels=["category", "state"],
    )
    points = GaugeMetricFamily(
        "ctfd_challenge_points",
        "Current point value of a challenge, after any dynamic decay.",
        labels=labels,
    )
    challenge_solves = CounterMetricFamily(
        "ctfd_challenge_solves",
        "Correct submissions for a challenge, from player accounts.",
        labels=labels,
    )
    challenge_wrong = CounterMetricFamily(
        "ctfd_challenge_wrong_submissions",
        "Incorrect submissions for a challenge, from player accounts.",
        labels=labels,
    )
    challenge_hints = CounterMetricFamily(
        "ctfd_challenge_hint_unlocks",
        "Hints unlocked on a challenge, by player accounts.",
        labels=labels,
    )

    per_group = defaultdict(int)

    for challenge_id, name, category, state, value in challenges:
        per_group[(category or "", state or "")] += 1
        values = [str(challenge_id), name or "", category or "", state or ""]
        points.add_metric(values, value or 0)
        challenge_solves.add_metric(values, solves.get(challenge_id, 0))
        challenge_wrong.add_metric(values, wrong.get(challenge_id, 0))
        challenge_hints.add_metric(values, hint_unlocks.get(challenge_id, 0))

    for (category, state), count in sorted(per_group.items()):
        counts.add_metric([category, state], count)

    return [counts, points, challenge_solves, challenge_wrong, challenge_hints]


def _state_counts(model, *criteria):
    """Group accounts into active / hidden / banned.

    The two flags are independent in the database, so the buckets are made
    exclusive here — banned wins over hidden — which lets a dashboard sum
    over the label to get the registered total.
    """
    rows = (
        db.session.query(model.banned, model.hidden, db.func.count(model.id))
        .filter(*criteria)
        .group_by(model.banned, model.hidden)
        .all()
    )

    counts = {"active": 0, "hidden": 0, "banned": 0}

    for banned, hidden, count in rows:
        if banned:
            counts["banned"] += count
        elif hidden:
            counts["hidden"] += count
        else:
            counts["active"] += count

    return counts


def _account_metrics():
    users = GaugeMetricFamily(
        "ctfd_users",
        "Registered non-admin users, by state.",
        labels=["state"],
    )
    for state, count in sorted(_state_counts(Users, Users.type == "user").items()):
        users.add_metric([state], count)

    teams = GaugeMetricFamily(
        "ctfd_teams",
        "Registered teams, by state. Always 0 while CTFd runs in users mode.",
        labels=["state"],
    )
    for state, count in sorted(_state_counts(Teams).items()):
        teams.add_metric([state], count)

    admins = GaugeMetricFamily("ctfd_admin_users", "Users holding the admin role.")
    admins.add_metric([], Users.query.filter(Users.type == "admin").count())

    # admin=True is the only variant of the standings that ignores the
    # scoreboard freeze *and* reports the hidden/banned flags, so the player
    # filter can be applied here rather than being traded for the freeze.
    # CTFd memoizes this for 60s, so a faster scrape interval buys nothing.
    standings = [
        row for row in get_standings(admin=True) if not row.hidden and not row.banned
    ]

    scored = GaugeMetricFamily(
        "ctfd_accounts_scored",
        "Player accounts with at least one solve or award worth points.",
    )
    scored.add_metric([], len(standings))

    score = GaugeMetricFamily(
        "ctfd_account_score",
        "Score of a player account, ignoring the scoreboard freeze.",
        labels=["account_id", "name"],
    )
    # Already ordered best first by get_standings, so the cap keeps the top.
    for row in standings[: _top_accounts()]:
        score.add_metric([str(row.account_id), row.name or ""], row.score or 0)

    return [users, teams, admins, scored, score]


def _user_metrics():
    """Per-user submission activity.

    Everything else here is account-level, and in teams mode an account is
    the team, so a team's members are indistinguishable. CTFd records the
    submitting user on every row regardless of mode, so this is the one view
    that says which member of a team is actually playing — and the one that
    shows a registered user who has never submitted anything at all.
    """
    model = get_model()

    users = db.session.query(Users.id, Users.name).filter(
        Users.type == "user", *_players(Users)
    )

    # In teams mode the user's team has to be a player too, otherwise a
    # tester hiding behind a hidden team would count through their user row.
    # The join also drops users with no team, which in teams mode cannot
    # submit anything anyway.
    if model is not Users:
        users = users.join(model, Users.team_id == model.id).filter(*_players(model))

    users = users.order_by(Users.id).all()

    # No player filter on these two: they are only ever read through the
    # already-filtered user list above.
    counts = {
        (user_id, result): count
        for user_id, result, count in db.session.query(
            Submissions.user_id, Submissions.type, db.func.count(Submissions.id)
        )
        .group_by(Submissions.user_id, Submissions.type)
        .all()
    }

    sources = dict(
        db.session.query(
            Submissions.user_id, db.func.count(db.distinct(Submissions.ip))
        )
        .group_by(Submissions.user_id)
        .all()
    )

    submissions = CounterMetricFamily(
        "ctfd_user_submissions",
        "Flag submissions from one user, by result.",
        labels=["user_id", "name", "result"],
    )
    user_ips = GaugeMetricFamily(
        "ctfd_user_source_ips",
        "Distinct addresses one user has submitted flags from.",
        labels=["user_id", "name"],
    )

    for user_id, name in users:
        labels = [str(user_id), name or ""]
        for result in ("correct", "incorrect"):
            submissions.add_metric(labels + [result], counts.get((user_id, result), 0))
        user_ips.add_metric(labels, sources.get(user_id, 0))

    return [submissions, user_ips]


def _submission_metrics():
    model = get_model()

    by_result = dict(
        db.session.query(Submissions.type, db.func.count(Submissions.id))
        .join(model, Submissions.account_id == model.id)
        .filter(*_players(model))
        .group_by(Submissions.type)
        .all()
    )

    submissions = CounterMetricFamily(
        "ctfd_submissions",
        "Flag submissions from player accounts, by result.",
        labels=["result"],
    )
    # correct and incorrect are always emitted, so a dashboard does not have
    # to cope with a series that only appears once the CTF gets going. A
    # challenge plugin is free to record other result types; pass those
    # through rather than dropping them.
    for result in sorted({"correct", "incorrect"} | {r for r in by_result if r}):
        submissions.add_metric([result], by_result.get(result, 0))

    unique_ips = (
        db.session.query(db.func.count(db.distinct(Submissions.ip)))
        .select_from(Submissions)
        .join(model, Submissions.account_id == model.id)
        .filter(*_players(model))
        .scalar()
    )

    ips = GaugeMetricFamily(
        "ctfd_submission_source_ips",
        "Distinct addresses player accounts have submitted flags from.",
    )
    ips.add_metric([], unique_ips or 0)

    return [submissions, ips]


class CTFdCollector(Collector):
    """Answers a scrape by querying the database, holding no state."""

    def collect(self):
        started = time.monotonic()

        yield from _ctf_metrics()
        yield from _challenge_metrics()
        yield from _account_metrics()
        yield from _user_metrics()
        yield from _submission_metrics()

        # Last, so it covers all the queries above. Worth watching: it is
        # the warning that the endpoint is becoming a load on the CTF.
        duration = GaugeMetricFamily(
            "ctfd_metrics_scrape_duration_seconds",
            "Seconds the plugin spent on the database for this scrape.",
        )
        duration.add_metric([], time.monotonic() - started)
        yield duration


def load(app):
    # A registry of our own rather than the process-wide default one, which
    # would also serve the interpreter's own process and GC metrics. Those
    # describe one gunicorn worker picked at random per scrape, so they
    # would be noise here.
    registry = CollectorRegistry()
    registry.register(CTFdCollector())

    if not _expected_token():
        app.logger.warning(
            "%s is unset: %s stays closed to everything but an admin session.",
            TOKEN_ENV,
            METRICS_PATH,
        )

    @app.route(METRICS_PATH, methods=["GET"])
    def ctfd_metrics():
        if not _authorized():
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
