from CTFd.models import Submissions, Users, db
from CTFd.utils.modes import get_model
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from .common import players


def user_metrics():
    model = get_model()

    users = db.session.query(Users.id, Users.name).filter(
        Users.type == "user", *players(Users)
    )

    # In teams mode the team must be a player too
    if model is not Users:
        users = users.join(model, Users.team_id == model.id).filter(*players(model))

    users = users.order_by(Users.id).all()

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
