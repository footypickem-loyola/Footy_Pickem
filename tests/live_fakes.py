"""Synthetic PR12 fixtures; no upstream requests or copied database contents."""
from datetime import datetime, timedelta

NOW = datetime(2026, 9, 30, 19, 0)
TEAMS = [('Arsenal', 'Everton'), ('Chelsea', 'Brentford'), ('Man City', 'Wolves'),
         ('Fulham', 'Man United'), ('Liverpool', 'Bournemouth'), ('Brighton', 'Newcastle'),
         ('Tottenham', 'Aston Villa'), ('Nottingham', 'Sunderland'), ('Leeds', 'Crystal Palace'), ('Burnley', 'West Ham')]


def seed(db, d, week_count=1):
    season = d.Season(code='pr12-local', name='Premier League', is_active=1, api_competition_code='PL')
    db.add(season)
    players = [d.Player(name=name) for name in ('Steve', 'Joe', 'Marc', 'Drew', 'Scott', 'Connor')]
    db.add_all(players)
    db.flush()
    for number in range(1, week_count + 1):
        week = d.Week(season_id=season.id, number=number, room_code='PR12LOCAL', status='provisional')
        db.add(week)
        db.flush()
        fixtures = [d.Fixture(week_id=week.id, match_number=i+1, home=h, away=a,
                    kickoff_utc=NOW - timedelta(minutes=30) + timedelta(days=number-1, hours=0 if i < 2 else i),
                    external_match_id=10000 + number*10 + i) for i, (h, a) in enumerate(TEAMS)]
        db.add_all(fixtures)
        db.flush()
        for a, b in zip(players[::2], players[1::2]):
            matchup = d.Matchup(week_id=week.id, player_a_id=a.id, player_b_id=b.id, first_picker_id=a.id)
            db.add(matchup)
            db.flush()
            db.add_all([d.Pick(matchup_id=matchup.id, fixture_id=f.id,
                player_id=a.id if i % 2 == 0 else b.id, team=f.home,
                created_at=NOW + timedelta(seconds=i)) for i, f in enumerate(fixtures)])
    db.commit()
    return season


def payload(fixture, score=(1, 0), state=2, minute=30, events=None):
    return dict(id=50000+fixture.id, league_id=8, starting_at=fixture.kickoff_utc.isoformat(),
        state_id=state, participants=[dict(id=100+fixture.id*2, name=fixture.home, meta={'location': 'home'}),
        dict(id=101+fixture.id*2, name=fixture.away, meta={'location': 'away'})],
        scores=[dict(description='CURRENT', score=dict(participant=side, goals=value)) for side, value in zip(('home', 'away'), score)],
        periods=[dict(minutes=minute, counts_from=45 if minute > 45 else 0, period_length=45, sort_order=1)],
        events=events if events is not None else [dict(id=1, type_id=14, participant_id=(101 if score[1] > score[0] else 100)+fixture.id*2,
            player_id=7, player_name='Alex Forward', related_player_name='Sam Winger', minute=25, result=f'{score[0]}-{score[1]}', sort_order=1)])
