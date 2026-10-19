"""The account census and the scoreboard."""

from CTFd.models import Teams, Users, db
from CTFd.utils.scores import get_standings
from prometheus_client.core import GaugeMetricFamily

from .common import top_accounts


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


def account_metrics():
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
    for row in standings[: top_accounts()]:
        score.add_metric([str(row.account_id), row.name or ""], row.score or 0)

    return [users, teams, admins, scored, score]
