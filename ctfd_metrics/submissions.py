"""Submission totals across the whole CTF."""

from CTFd.models import Submissions, db
from CTFd.utils.modes import get_model
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from .common import players


def submission_metrics():
    model = get_model()

    by_result = dict(
        db.session.query(Submissions.type, db.func.count(Submissions.id))
        .join(model, Submissions.account_id == model.id)
        .filter(*players(model))
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
        .filter(*players(model))
        .scalar()
    )

    ips = GaugeMetricFamily(
        "ctfd_submission_source_ips",
        "Distinct addresses player accounts have submitted flags from.",
    )
    ips.add_metric([], unique_ips or 0)

    return [submissions, ips]
