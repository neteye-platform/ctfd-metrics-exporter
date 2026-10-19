# CTFd metrics exporter

Serves the live state of a CTF — challenges, solves, submissions, accounts
and scores — on an authenticated `/metrics` endpoint, in the Prometheus text
exposition format.

Despite the name, this is not a separate service. It is a CTFd plugin: it
runs inside CTFd's own Flask application and reads the database directly, so
there is no second container to deploy and no API token to hand out.

## Motivation

During a CTF the questions that matter are operational and time-boxed: is
anyone stuck, is a challenge unsolvable or accidentally trivial, is the flag
submission rate collapsing because the infrastructure broke. CTFd's admin
panel answers them one page refresh at a time, and only for right now.
Prometheus answers them as a graph over the whole event, next to the host and
container metrics the same box already ships.

## Why a plugin rather than a polling exporter

The usual shape for this is a sidecar that polls CTFd's REST API on a timer.
That needs an admin API key living in a second container, it can only report
what the API exposes, and what it reports is always one poll interval old.

Running in-process avoids all three. It also means the queries go through
CTFd's own SQLAlchemy models rather than hand-written SQL, which matters more
than it sounds: `Fails` is not a table — incorrect submissions are rows of
`submissions` told apart by a discriminator — and `account_id` is a hybrid
property resolving to either `team_id` or `user_id` depending on a
configuration row. Reusing the models means a schema change between CTFd
releases breaks loudly on import instead of silently returning plausible
wrong numbers.

Every value is read while the scrape is being answered, so the plugin holds
no counters of its own. It needs no warm-up, it cannot drift from the
database, and it stays correct behind several gunicorn workers, where
in-process counters would each hold a different partial view.

The tradeoff is honest: the endpoint dies with CTFd. If the application is
down you get no CTF metrics — though you do get `up == 0` on the target,
which is the alert you actually wanted, and a host exporter alongside it will
still tell you about the machine.

## Requirements

- CTFd 3.8 or newer
- `prometheus-client`, pinned in `ctfd_metrics/requirements.txt`

Tested against CTFd 3.8.8 on MariaDB, in both users and teams mode.

## Install

The importable plugin is the `ctfd_metrics` directory, not the repository
root, so clone this anywhere and put that directory into CTFd's `plugins`:

```sh
git clone https://github.com/neteye-platform/ctfd-metrics-exporter.git
cp -r ctfd-metrics-exporter/ctfd_metrics /path/to/CTFd/CTFd/plugins/
```

Under Docker, mounting it read-only works just as well:

```yaml
volumes:
  - ./ctfd-metrics-exporter/ctfd_metrics:/opt/CTFd/CTFd/plugins/ctfd_metrics:ro
```

Install `prometheus-client` into the same environment as CTFd, then restart
it. CTFd's official image installs each plugin's `requirements.txt` if its
entrypoint is set up to, otherwise add the pin to your own image.

## Configuration

Both settings are environment variables read by the CTFd process.

- `CTFD_METRICS_TOKEN` — the shared secret a scraper must present as
  `Authorization: Bearer <token>`. **Required.** Without it the endpoint
  answers 401 to everyone but a logged-in admin, because the response body
  is the live scoreboard. Generate one with `openssl rand -hex 32`.
- `CTFD_METRICS_TOP_ACCOUNTS` — keep only the N highest-scoring accounts in
  `ctfd_account_score`. Unset or `0` means all of them, which is right for a
  CTF with tens of teams and a footgun for one with thousands of users,
  where one series per account would swamp Prometheus.

## Metrics

Everything below counts **player accounts only**: hidden accounts, which is
what organizers solve challenges from while testing them, and banned ones are
excluded, the same way CTFd's own statistics pages exclude them. An *account*
is a team when CTFd runs in teams mode and a user when it runs in users mode.

CTF-level:

- `ctfd_build_info` (gauge, always 1) — labels `ctfd_version`,
  `plugin_version`, `user_mode`, `theme`.
- `ctfd_ctf_paused` (gauge) — 1 while the CTF is paused.
- `ctfd_ctf_start_timestamp_seconds`, `ctfd_ctf_end_timestamp_seconds`,
  `ctfd_ctf_freeze_timestamp_seconds` (gauges) — the configured times, as
  unix timestamps. A time nobody has configured yet emits no series at all,
  rather than a 0 that a dashboard would draw in 1970.
- `ctfd_metrics_scrape_duration_seconds` (gauge) — seconds this scrape spent
  on the database. Worth a panel: it is the warning that the endpoint is
  becoming a load on the CTF.

Challenges — all labelled `challenge_id`, `name`, `category`, `state`:

- `ctfd_challenges` (gauge) — how many challenges there are, labelled
  `category` and `state` only.
- `ctfd_challenge_points` (gauge) — the challenge's current value, after any
  dynamic decay. Note this is a metric and not a label: dynamic challenges
  change value as they are solved, so a `value` label would start a new time
  series on every decay.
- `ctfd_challenge_solves_total` (counter) — correct submissions.
- `ctfd_challenge_wrong_submissions_total` (counter) — incorrect ones.
- `ctfd_challenge_hint_unlocks_total` (counter) — hints unlocked on it.

Accounts:

- `ctfd_users` (gauge) — registered non-admin users, labelled `state`:
  `active`, `hidden` or `banned`. The two flags are independent in the
  database, so the buckets are made exclusive here — banned wins over hidden
  — which lets `sum(ctfd_users)` give the registered total.
- `ctfd_teams` (gauge) — same, for teams. All zero in users mode.
- `ctfd_admin_users` (gauge) — users holding the admin role.
- `ctfd_accounts_scored` (gauge) — accounts with at least one solve or award
  worth points.
- `ctfd_account_score` (gauge) — score per account, labels `account_id` and
  `name`. Both labels are carried on purpose: CTFd deliberately does not
  constrain account names to be unique, so a name alone would let two
  accounts collapse into one series.

Submissions:

- `ctfd_submissions_total` (counter) — labelled `result`, which is `correct`
  or `incorrect`.
- `ctfd_submission_source_ips` (gauge) — distinct addresses flags have been
  submitted from.

Per user — both labelled `user_id` and `name`:

- `ctfd_user_submissions_total` (counter) — submissions from one user, also
  labelled `result`.
- `ctfd_user_source_ips` (gauge) — distinct addresses one user has submitted
  from.

Every other metric here is account-level, so in teams mode a team's members
are indistinguishable. These two are the exception: CTFd records the
submitting user on every row whatever the mode, so they are the view that
says *which* member of a team is carrying it. A user who has never submitted
still gets a series, at zero, so an account that registered and never turned
up is visible rather than simply absent.

That is three series per player user, which scales with the field: fine for
hundreds of players, not for tens of thousands. Unlike `ctfd_account_score`
there is no cap on it.

### Useful queries

```promql
# Submission rate, which is the liveness signal for the whole event
sum(rate(ctfd_submissions_total[5m])) by (result)

# Points still unclaimed, on challenges nobody has solved yet
sum(ctfd_challenge_points{state="visible"}
    and on(challenge_id) (ctfd_challenge_solves_total == 0))

# Challenges nobody has solved an hour into the CTF
ctfd_challenge_solves_total{state="visible"} == 0

# Where people are stuck: wrong guesses per solve
ctfd_challenge_wrong_submissions_total
  / clamp_min(ctfd_challenge_solves_total, 1)

# Who is grinding right now, and who has gone quiet
topk(10, rate(ctfd_user_submissions_total[15m]))

# Possible account sharing: one login submitting from many addresses
topk(10, ctfd_user_source_ips)
```

## Scraping it

```yaml
scrape_configs:
  - job_name: ctfd
    scheme: https
    scrape_interval: 30s
    authorization:
      credentials: <CTFD_METRICS_TOKEN>
    static_configs:
      - targets: ["ctf.example.com"]
```

### Keeping it away from players

The response reports the **unfrozen** scoreboard, hidden accounts and all.
That is what organizers need and exactly what players must not see, so the
token is not optional and the plugin fails closed without it.

Treat the token as the last line rather than the only one. CTFd serves
`/metrics` on the same port as the rest of the site, so if your reverse proxy
has a catch-all the endpoint is reachable from the internet, guarded by the
token alone. Two ways to tighten that, which combine well:

```nginx
# 1. Refuse it on the player-facing vhost, so the token is never exposed
#    to guessing from the internet.
location = /metrics {

  return 404;
}

# 2. Serve it from a vhost on a port of its own, which answers 404 to
#    every other path, restricted to where the scraper comes from.
server {

  listen 9000 ssl;

  location = /metrics {

    allow 203.0.113.10;
    deny all;

    proxy_pass http://ctfd:8000;
  }

  location / {

    return 404;
  }
}
```

If you do both, remember the scraper must then use the second port, and an
admin browsing to `/metrics` has to come from an allowed address too.

## Things worth knowing

- **Do not send `Content-Type: application/json`.** CTFd inspects the
  `Authorization` header on every JSON request and tries to resolve it as one
  of its own API tokens, failing the request with 401 before the plugin is
  reached. Prometheus sends no request body, so this only bites hand-rolled
  `curl` calls — pass the bearer header alone.
- **Before CTFd is set up, `/metrics` answers 302 to `/setup`.** CTFd
  installs that redirect before any plugin loads, so it cannot be bypassed
  from here. Expect the target to be down until the setup wizard is finished.
- **Scores can be up to 60 seconds stale.** They come from CTFd's own
  standings query, which CTFd memoizes for 60s. Scraping faster than that
  buys nothing; 30s is a sensible interval.
- **An account with no points emits no `ctfd_account_score` series**, because
  CTFd's standings only contain accounts that have scored. Compare
  `ctfd_accounts_scored` against `sum(ctfd_teams)` or `sum(ctfd_users)` to
  see how many registered accounts are still on zero.
- **An admin session can read `/metrics` too**, which is only there so a
  human can open it in a browser without digging the token out of the
  process environment.
- **Banning a user does not remove their team's solves** in teams mode, since
  the filter applies to the account that scores, which is the team. Ban or
  hide the team to take its numbers out of the metrics. This mirrors what
  CTFd's own statistics pages do. The `ctfd_user_*` metrics are the
  exception, because they filter on the user: a hidden or banned user drops
  out of those at once, and in teams mode their team has to be a player too.
  A user with no team gets no series there, since in teams mode they cannot
  submit anything anyway.

## License

Dual-licensed under Apache 2.0 and MIT, at your option. See
[LICENSE-APACHE](LICENSE-APACHE) and [LICENSE-MIT](LICENSE-MIT).
