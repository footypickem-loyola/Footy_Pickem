"""PR12 synthetic browser harness, loopback only; never opens an existing DB.

python scripts/review_live_matchweek.py --port 58192
Join Steve / PR12LOCAL. Scenario control exists only in this temporary harness.
"""
import argparse
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=58192)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='footy-pr12-browser-') as temp:
        os.environ['DB_PATH'] = f"sqlite:///{Path(temp) / 'review.db'}"
        os.environ['INIT_ON_START'] = '0'
        os.environ['SPORTMONKS_API_TOKEN'] = ''
        os.environ['LIVE_SYNC_ENABLED'] = '0'
        import pickem_flask_htmx_tabs as d
        from live_fakes import seed, payload
        from live_sync import ingest
        from sportmonks_live import normalize_fixture
        from flask import session
        with d.SessionLocal() as db:
            seed(db, d)
            for i, f in enumerate(db.query(d.Fixture).order_by(d.Fixture.id)):
                f.kickoff_utc = datetime.utcnow() + timedelta(minutes=-30 if i < 2 else 120+i*30)
            db.commit()

        @d.app.post('/__review/scenario/<int:number>')
        def scenario(number):
            # Only this isolated harness exposes scenario mutation.
            db = d.SessionLocal()
            db.query(d.MatchEvent).delete(); db.query(d.LiveFixtureState).delete()
            db.query(d.Result).delete()
            week = db.query(d.Week).one()
            week.status = 'provisional'
            fixtures = db.query(d.Fixture).order_by(d.Fixture.id).all()
            fixtures[1].kickoff_utc = datetime.utcnow() + timedelta(minutes=-60 if number >= 5 else 120)
            if number > 1:
                score = (0, 0) if number == 2 else (1, 1) if number == 4 else (1, 0)
                raw = payload(fixtures[0], score=score, state=3 if number == 7 else 2,
                              minute=94 if number == 8 else 45 if number == 7 else 30)
                if number == 2: raw['events'] = []
                if number == 4:
                    raw['events'].append(dict(id=2,type_id=14,participant_id=101+fixtures[0].id*2,player_name='Jamie Equalizer',minute=29,result='1-1',sort_order=2))
                if number == 8:
                    raw['events'][0].update(minute=90, extra_minute=4)
                raw['events'].append(dict(id=3,type_id=20,participant_id=101+fixtures[0].id*2,player_name='Robin Defender',minute=24))
                raws = [raw]
                if number in (5,6,7,8,9,10): raws.append(payload(fixtures[1], score=(0,1), minute=68))
                ingest(db,d,[normalize_fixture(r) for r in raws],now=datetime.utcnow()-timedelta(minutes=10 if number == 9 else 0))
            if number == 10:
                for f in fixtures:
                    db.add(d.Result(fixture_id=f.id, outcome='Home', home_score=1, away_score=0, source='manual'))
                week.status = 'finalized'
            db.commit()
            session['player_name'] = 'Steve'
            return {'scenario':number}

        try:
            print(f'Synthetic temporary DB: {Path(temp) / "review.db"}', flush=True)
            d.app.run(host='127.0.0.1', port=args.port, use_reloader=False, threaded=True)
        finally:
            d.SessionLocal.remove()
            d.engine.dispose()


if __name__ == '__main__':
    main()
