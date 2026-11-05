"""Authenticated navigation over existing league/season/week relationships."""
from flask import abort, render_template, request
from sqlalchemy import text


def memberships(db,user,league_id=None):
    if league_id is not None and not 0<league_id<2**63:abort(404)
    query="""SELECT l.id,l.slug,l.name,l.status,p.id AS membership_id,p.role
        FROM players p JOIN leagues l ON l.id=p.league_id
        WHERE p.user_id=:user AND p.status='active' AND p.left_at IS NULL
        AND l.status IN ('setup','active')"""
    params=dict(user=user)
    if league_id is not None:query+=' AND l.id=:league';params['league']=league_id
    rows=[dict(row) for row in db.execute(text(query+' ORDER BY l.id'),params).mappings()]
    if league_id is not None and not rows:abort(404)
    return rows


def season_choices(db,league_id):
    return [dict(row) for row in db.execute(text("""SELECT s.id,s.name,s.code,
        EXISTS(SELECT 1 FROM football_seasons fs WHERE fs.id=s.football_season_id) AS ready
        FROM seasons s WHERE s.league_id=:league AND s.is_active=1 AND s.is_archived=0
        ORDER BY s.id DESC"""),dict(league=league_id)).mappings()]


def install_routes(bp, *, session_factory, resolve_user):
    @bp.get('/leagues/<int:league_id>/seasons/<int:season_id>')
    def season_navigation(league_id,season_id):
        with session_factory() as db:
            db.execute(text('BEGIN'))
            user=resolve_user(db)
            if request.args or request.content_length:abort(400)
            league=memberships(db,user,league_id)[0]
            if not 0<season_id<2**63:abort(404)
            season=db.execute(text("""SELECT id,name,code,football_season_id FROM seasons
                WHERE id=:id AND league_id=:league AND is_active=1 AND is_archived=0"""),
                dict(id=season_id,league=league_id)).mappings().one_or_none()
            if season is None:abort(404)
            if not db.execute(text('SELECT 1 FROM football_seasons WHERE id=:id'),dict(id=season['football_season_id'])).first():abort(409)
            rows=db.execute(text("""SELECT w.id,w.number,w.status,fm.football_season_id
                FROM weeks w LEFT JOIN football_matchweeks fm ON fm.id=w.football_matchweek_id
                WHERE w.season_id=:season ORDER BY w.number"""),dict(season=season_id)).mappings().all()
            if any(w['number']<=0 or w['football_season_id']!=season['football_season_id'] or
                   w['status'] not in ('drafting','provisional','finalized') for w in rows):abort(409)
            invalid=db.execute(text("""SELECT 1 FROM league_week_fixtures lwf
                JOIN weeks w ON w.id=lwf.week_id LEFT JOIN fixtures f ON f.id=lwf.fixture_id
                WHERE w.season_id=:season AND (f.id IS NULL OR f.football_season_id IS NULL
                    OR f.football_matchweek_id IS NULL OR f.football_season_id!=:football
                    OR f.football_matchweek_id!=w.football_matchweek_id) LIMIT 1"""),
                dict(season=season_id,football=season['football_season_id'])).first()
            if invalid:abort(409)
            weeks=[dict(w) for w in rows]
            unfinished=[w for w in weeks if w['status']!='finalized']
            current=unfinished[0] if len(unfinished)==1 else None
        return render_template('runtime_league_navigation.html',league=league,season=season,
                               weeks=weeks,current=current)
