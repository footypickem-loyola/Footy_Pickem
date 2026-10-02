"""PR15 browser harness. NEW temporary synthetic DB only; never opens app data."""
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix='footy-pr15-') as temp:
        os.environ['DB_PATH'] = f"sqlite:///{Path(temp) / 'review.db'}"
        os.environ['INIT_ON_START'] = '0'
        import pickem_flask_htmx_tabs as d
        from flask import session

        @d.app.post('/__review/scenario/<int:scenario>')
        def scenario(scenario):
            d.SessionLocal.remove()
            d.Base.metadata.drop_all(d.engine)
            d.Base.metadata.create_all(d.engine)
            d.init_weeks_from_csv(str(ROOT / 'epl_2025.csv'), [1, 2],
                ['Steve', 'Joe', 'Marc', 'Drew', 'Scott', 'Connor'], 'PR15LOCAL')
            db = d.SessionLocal()
            season = db.query(d.Season).filter_by(code='year-2').one()
            steve = db.query(d.Player).filter_by(name='Steve').one()
            week = db.query(d.Week).filter_by(season_id=season.id, number=2).one()
            matchup = db.query(d.Matchup).filter(d.Matchup.week_id == week.id,
                (d.Matchup.player_a_id == steve.id) | (d.Matchup.player_b_id == steve.id)).one()
            other = matchup.player_b_id if matchup.player_a_id == steve.id else matchup.player_a_id
            matchup.first_picker_id = other
            if scenario == 4:
                matchup.first_picker_id = steve.id
            fixtures = db.query(d.Fixture).filter_by(week_id=week.id).order_by(d.Fixture.match_number).all()
            fixtures[1].home, fixtures[1].away = 'Arsenal', 'Leeds United'
            for fixture in fixtures:
                fixture.kickoff_utc = datetime(2026, 10, 10, 16)
            prior = db.query(d.Week).filter_by(season_id=season.id, number=1).one()
            history = db.query(d.Fixture).filter_by(week_id=prior.id).order_by(d.Fixture.match_number).all()
            for i, fixture in enumerate(history[:7]):
                fixture.home, fixture.away = ('Arsenal', f'Club {i}') if i % 2 == 0 else (f'Club {i}', 'Arsenal')
                fixture.kickoff_utc = datetime(2026, 9, i + 1, 16)
                home_score, away_score = [(3, 1), (1, 2), (2, 2), (0, 1), (1, 0), (3, 1), (2, 0)][i]
                db.add(d.Result(fixture_id=fixture.id, outcome='Home' if home_score > away_score else 'Away' if away_score > home_score else 'Draw',
                                home_score=home_score, away_score=away_score, source='manual'))
            if scenario in (3, 4):
                db.add(d.AutoDraftSetting(matchup_id=matchup.id, player_id=steve.id,
                                         enabled=0, confirmed_at=datetime(2026, 9, 30)))
                for i, fixture in enumerate(fixtures):
                    db.add(d.AutoDraftPreference(matchup_id=matchup.id, player_id=steve.id,
                        fixture_id=fixture.id, team=fixture.home, priority=i))
            if scenario in (1, 3, 4, 5, 6):
                for i in range(10 if scenario == 3 else 9 if scenario == 4 else 1):
                    first, second = (steve.id, other) if scenario == 4 else (other, steve.id)
                    db.add(d.Pick(matchup_id=matchup.id, player_id=d.draft_turn_at(first, second, i),
                                  fixture_id=fixtures[i].id, team=fixtures[i].home))
            if scenario in (5, 6):
                season.api_competition_code, season.api_season_year = 'PL', 2026
                db.add(d.ClubSeasonEnrichment(season_id=season.id, sportmonks_team_id=19,
                    sportmonks_season_id=28083, league_id=8, footy_club='Arsenal',
                    crest_url='https://cdn.sportmonks.com/images/soccer/teams/19/19.png',
                    top_scorers=[dict(player_id=1, name='First Synthetic Scorer'),
                                 dict(player_id=2, name='Second Synthetic Scorer')], goals=5,
                    synced_at=d.utcnow() - timedelta(days=36 if scenario == 6 else 0)))
            db.commit()
            session['player_name'] = 'Steve'
            return {'matchup_id': matchup.id}

        try:
            d.app.run(host='127.0.0.1', port=5115, debug=False, use_reloader=False)
        finally:
            d.SessionLocal.remove()
            d.engine.dispose()


if __name__ == '__main__':
    main()
