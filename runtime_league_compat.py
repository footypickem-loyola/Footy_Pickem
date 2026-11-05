"""Read-only league relationships for the existing ORM; no schema initialization."""
from sqlalchemy import inspect, text, table, column, select, literal_column


def owned_league(db,model,id):
    if 'league_id' not in {c['name'] for c in inspect(db.connection()).get_columns(model.__tablename__)}:return None
    return db.connection().execute(text('SELECT league_id FROM '+model.__tablename__+' WHERE id=:id'),dict(id=id)).scalar_one()


def legacy_query(db,model):
    query=db.query(model)
    if 'league_id' not in {c['name'] for c in inspect(db.connection()).get_columns(model.__tablename__)}:return query
    league=db.connection().execute(text("SELECT id FROM leagues WHERE slug='loyola-pick-em'")).scalar_one_or_none()
    if league is not None:return query.filter(literal_column(model.__tablename__+'.league_id')==league)
    # Stage 1 additive columns may exist while all legacy rows are still unowned.
    owned=db.connection().execute(text('SELECT 1 FROM '+model.__tablename__+' WHERE league_id IS NOT NULL LIMIT 1')).first()
    if owned:raise ValueError('LOYOLA_CONTEXT_REQUIRED')
    return query.filter(literal_column(model.__tablename__+'.league_id').is_(None))


def refuse_unscoped_seed(db,model):
    if 'league_id' in {c['name'] for c in inspect(db.connection()).get_columns(model.__tablename__)}:
        if db.connection().execute(text('SELECT 1 FROM '+model.__tablename__+' WHERE league_id IS NOT NULL LIMIT 1')).first():
            raise ValueError('LEAGUE_SCOPED_INITIALIZATION_REQUIRED')


def league_players(db,season,Player):
    league=owned_league(db,type(season),season.id)
    query=db.query(Player)
    return query if league is None else query.filter(literal_column('players.league_id')==league)


def week_fixtures(db,week,Fixture,Season):
    columns={c['name'] for c in inspect(db.connection()).get_columns(Season.__tablename__)}
    if 'football_season_id' not in columns or owned_league(db,Season,week.season_id) is None:
        return db.query(Fixture).filter_by(week_id=week.id)
    slate=table('league_week_fixtures',column('week_id'),column('fixture_id'))
    return db.query(Fixture).filter(Fixture.id.in_(select(slate.c.fixture_id).where(slate.c.week_id==week.id)))


def install_disabled_boundary(app,engine):
    """Legacy fallback is safe only before non-Loyola gameplay data exists.

    No database access during installation. Verified mode uses its existing full
    boundary instead; static assets never depend on game storage availability.
    """
    from flask import abort, request
    @app.before_request
    def legacy_single_league_only():
        if request.endpoint=='static' or request.url_rule is None:return
        with engine.connect() as conn:
            inspector=inspect(conn)
            if not inspector.has_table('leagues'):return
            if not all('league_id' in {c['name'] for c in inspector.get_columns(t)} for t in ('players','seasons')):return
            # One read snapshot covers identity of the historical league and owners.
            conn.exec_driver_sql('BEGIN')
            loyola=conn.execute(text("SELECT id FROM leagues WHERE slug='loyola-pick-em'")).scalar_one_or_none()
            owners=conn.execute(text('SELECT league_id FROM players WHERE league_id IS NOT NULL UNION SELECT league_id FROM seasons WHERE league_id IS NOT NULL')).scalars().all()
            if any(owner!=loyola for owner in owners):
                abort(403,'Verified-account mode is required for multi-league data')
