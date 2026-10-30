"""Per-user submission activity.

The only section that is not account-level, which is what makes it the one
view that distinguishes the members of a team.
"""

from CTFd.models import Submissions, Users, db
from CTFd.utils.modes import get_model
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from .common import players


def user_metrics():
    """Per-user submission activity.

    Everything else here is account-level, and in teams mode an account is
    the team, so a team's members are indistinguishable. CTFd records the
    submitting user on every row regardless of mode, so this is the one view
    that says which member of a team is actually playing — and the one that
    shows a registered user who has never submitted anything at all.
    """
    model = get_model()

    users = db.session.query(Users.id, Users.name).filter(
        Users.type == "user", *players(Users)
    )

    # In teams mode the user's team has to be a player too, otherwise a
    # tester hiding behind a hidden team would count through their user row.
    # The join also drops users with no team, which in teams mode cannot
    # submit anything anyway.
    if model is not Users:
        users = users.join(model, Users.team_id == model.id).filter(*players(model))

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
