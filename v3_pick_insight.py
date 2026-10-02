"""Read-only, deterministic club insight from official season results.

No live projections or provider requests. A result is authoritative even when
its manual outcome has no score. Never invent goals from an outcome.
"""
from datetime import timezone


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def build_pick_insight(*, fixture, team, week_number, results, now, crest=None):
    """results contains (Fixture, Result, week number) from ONE selected season.

    Use results before this fixture's kickoff (or earlier weeks when kickoff is
    unknown), also bounded by now. Form reads oldest to newest, left to right.
    Unknown historical kickoffs use matchweek order, before known timestamps.
    """
    home = team == fixture.home
    cutoff = min(_utc(fixture.kickoff_utc) or _utc(now), _utc(now))
    history = []
    for prior, result, number in results:
        if prior.id == fixture.id or team not in (prior.home, prior.away):
            continue
        kickoff = _utc(prior.kickoff_utc)
        if kickoff is not None and _utc(fixture.kickoff_utc) is not None:
            if kickoff >= cutoff:
                continue
        elif number >= week_number or (kickoff is not None and kickoff >= cutoff):
            continue
        if result.outcome not in ('Home', 'Away', 'Draw'):
            continue
        is_home = team == prior.home
        outcome = 'D' if result.outcome == 'Draw' else 'W' if result.outcome == ('Home' if is_home else 'Away') else 'L'
        scores = (result.home_score, result.away_score) if is_home else (result.away_score, result.home_score)
        history.append(dict(outcome=outcome, home=is_home, scores=scores,
                            order=(kickoff is not None, kickoff.timestamp() if kickoff else number,
                                   prior.match_number, prior.id)))
    history.sort(key=lambda row: row['order'])

    def record(rows):
        return ' '.join(f"{sum(row['outcome'] == value for row in rows)}{value}" for value in ('W', 'D', 'L'))

    complete_scores = bool(history) and all(type(score) is int and score >= 0
                                           for row in history for score in row['scores'])
    averages = [f"{sum(row['scores'][side] for row in history) / len(history):.1f}"
                if complete_scores else '—' for side in (0, 1)]
    return dict(team=team, crest=crest, opponent=fixture.away if home else fixture.home,
                venue='Home vs' if home else 'Away at', kickoff=fixture.kickoff_utc,
                sample=len(history), bands=[
                    dict(label='Overall Record', value=record(history)),
                    dict(label='Home Record' if home else 'Away Record',
                         value=record([row for row in history if row['home'] == home])),
                    dict(label='Last 5', value=' '.join(row['outcome'] for row in history[-5:]) or '—'),
                    dict(label='Goals / Game', value=averages[0]),
                    dict(label='Goals Against / Game', value=averages[1]),
                    # Live MatchEvent snapshots are not a complete PL season ledger.
                    dict(label='Top Scorer', value='—'),
                ])
