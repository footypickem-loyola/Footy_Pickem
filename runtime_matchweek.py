"""Read-only Matchweek projection on the real runtime's authenticated blueprint.

Explicit league/season/roster/slate joins; never legacy global helper fallbacks.
"""
from flask import abort, jsonify, request
from sqlalchemy import text


def install_routes(bp, *, session_factory, resolve_user):
    @bp.get('/leagues/<int:league_id>/seasons/<int:season_id>/matchweeks/<int:week_number>')
    def matchweek(league_id,season_id,week_number):
        with session_factory() as db:
            # One consistent SQLite snapshot for authorization and all game reads.
            db.execute(text('BEGIN'))
            user=resolve_user(db)
            if not all(0<v<2**63 for v in (league_id,season_id,week_number)):abort(404)
            if request.args or request.content_length:abort(400)
            data=load_matchweek(db,user,league_id,season_id,week_number)
        response=jsonify(data)
        response.headers['Cache-Control']='private, no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response


def load_matchweek(db,user,league_id,season_id,week_number):
    """Validate and project the scoped graph inside the caller's transaction."""
    league=db.execute(text("""SELECT l.id,l.slug,l.name,p.id AS membership_id
        FROM leagues l JOIN players p ON p.league_id=l.id
        WHERE l.id=:league AND p.user_id=:user AND p.status='active'
        AND p.left_at IS NULL AND l.status IN ('setup','active')"""),
        dict(league=league_id,user=user)).mappings().one_or_none()
    if league is None:abort(404)
    season=db.execute(text("""SELECT id,code,name,football_season_id FROM seasons
        WHERE id=:season AND league_id=:league"""),dict(season=season_id,league=league_id)).mappings().one_or_none()
    if season is None:abort(404)
    if season['football_season_id'] is None:abort(409)
    week=db.execute(text("""SELECT id,number,status,football_matchweek_id FROM weeks
        WHERE season_id=:season AND number=:number"""),dict(season=season_id,number=week_number)).mappings().one_or_none()
    if week is None:abort(404)
    football=db.execute(text("""SELECT id,number FROM football_matchweeks
        WHERE id=:matchweek AND football_season_id=:football_season"""),
        dict(matchweek=week['football_matchweek_id'],football_season=season['football_season_id'])).mappings().one_or_none()
    if football is None:abort(409)
    fixtures=db.execute(text("""SELECT f.id,f.home,f.away,f.kickoff_utc,
        f.home_club_id,f.away_club_id,f.football_season_id,f.football_matchweek_id,lwf.display_order
        FROM league_week_fixtures lwf JOIN fixtures f ON f.id=lwf.fixture_id
        WHERE lwf.week_id=:week ORDER BY lwf.display_order"""),dict(week=week['id'])).mappings().all()
    clubs=set(db.execute(text('SELECT club_id FROM football_season_clubs WHERE football_season_id=:season'),
                         dict(season=season['football_season_id'])).scalars())
    for f in fixtures:
        if (f['football_season_id']!=season['football_season_id'] or
            f['football_matchweek_id']!=football['id'] or f['home_club_id'] not in clubs or
            f['away_club_id'] not in clubs or f['home_club_id']==f['away_club_id']):abort(409)
    fixture_map={f['id']:f for f in fixtures}
    players=db.execute(text("""SELECT p.id,p.name FROM league_season_memberships r
        JOIN players p ON p.id=r.membership_id
        WHERE r.season_id=:season AND p.league_id=:league ORDER BY p.id"""),
        dict(season=season_id,league=league_id)).mappings().all()
    roster_count=db.execute(text('SELECT COUNT(*) FROM league_season_memberships WHERE season_id=:season'),dict(season=season_id)).scalar_one()
    if len(players)!=roster_count:abort(409)
    player_map={p['id']:dict(p) for p in players}
    matchups=db.execute(text("""SELECT id,player_a_id,player_b_id,first_picker_id
        FROM matchups WHERE week_id=:week ORDER BY id"""),dict(week=week['id'])).mappings().all()
    matchup_map={m['id']:m for m in matchups}
    for m in matchups:
        pair={m['player_a_id'],m['player_b_id']}
        if len(pair)!=2 or not pair<=player_map.keys() or m['first_picker_id'] not in pair:abort(409)
    picks=db.execute(text("""SELECT p.id,p.matchup_id,p.player_id,p.fixture_id,p.team,p.selected_club_id
        FROM picks p JOIN matchups m ON m.id=p.matchup_id
        WHERE m.week_id=:week ORDER BY p.created_at,p.id"""),dict(week=week['id'])).mappings().all()
    for p in picks:
        m=matchup_map[p['matchup_id']];f=fixture_map.get(p['fixture_id'])
        if f is None or p['player_id'] not in {m['player_a_id'],m['player_b_id']}:abort(409)
        if p['team'] not in (f['home'],f['away']):abort(409)
        expected_club=f['home_club_id'] if p['team']==f['home'] else f['away_club_id']
        if p['selected_club_id'] is not None and p['selected_club_id']!=expected_club:abort(409)
    results=db.execute(text("""SELECT r.fixture_id,r.outcome,r.home_score,r.away_score
        FROM results r JOIN league_week_fixtures lwf ON lwf.fixture_id=r.fixture_id
        WHERE lwf.week_id=:week"""),dict(week=week['id'])).mappings().all()
    result_map={r['fixture_id']:dict(r) for r in results}
    data=dict(read_only=True,league={k:league[k] for k in ('id','slug','name')},
        viewer=dict(membership_id=league['membership_id']),season=dict(season),
        week=dict(week,football_round=football['number']),
        fixtures=[dict(f,result=result_map.get(f['id'])) for f in fixtures],
        matchups=[dict(id=m['id'],players=[player_map[m['player_a_id']],player_map[m['player_b_id']]],
                       first_picker_id=m['first_picker_id'],picks=[dict(p) for p in picks if p['matchup_id']==m['id']]) for m in matchups])
    return data
