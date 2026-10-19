"""Per-challenge metrics: points, solves, wrong guesses and hint unlocks."""

from collections import defaultdict

from CTFd.models import Challenges, Hints, Solves, Submissions, Unlocks, db
from CTFd.utils.modes import get_model
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from .common import players


def challenge_metrics():
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
        .filter(*players(model))
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
        .filter(Submissions.type == "incorrect", *players(model))
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
        .filter(Unlocks.type == "hints", *players(model))
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
