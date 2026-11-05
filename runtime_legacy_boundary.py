"""Explicit verified-mode route policy and request-local Loyola ORM boundary.

No new schema, league switching, account claiming, or legacy admin authority.
Machine routes retain separate service credentials and existing endpoint guards.
"""
import hmac
import os
import sqlite3
from flask import abort, current_app, g, has_request_context, request
from sqlalchemy import and_, column, event, or_, select, table, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, with_loader_criteria
from runtime_auth_sessions import AuthRefused

# Exact registered signatures, not prefix exemptions. New signatures default deny.
ROUTES = {}
def routes(category, entries):
    for endpoint, rule, methods in entries:
        ROUTES[(endpoint,rule,frozenset(methods.split()))] = category

routes('public_static', [('static','/static/<path:filename>','GET')])
routes('public_auth', [('runtime_auth.login_form','/auth/login','GET'),
    ('runtime_auth.login','/auth/login','POST'),('runtime_auth.callback','/auth/callback','GET')])
routes('account', [('runtime_auth.leagues','/auth/leagues','GET'),
    ('runtime_auth.leagues','/auth/leagues/<int:league_id>','GET'),('runtime_auth.logout','/auth/logout','POST')])
routes('legacy', [('shell','/','GET'),('tab_current','/tab/current','GET'),
    ('matchweek_partial','/partials/matchweek/<int:week_number>','GET'),
    ('tab_open','/tab/open','GET'),('tab_season','/tab/season','GET'),
    ('tab_stats','/tab/stats','GET'),('tab_league','/tab/league','GET'),
    ('fixtures_partial','/partials/fixtures/<int:week_number>','GET'),
    ('matchups_partial','/partials/matchups/<int:week_number>','GET'),
    ('scores_partial','/partials/scores/<int:week_number>','GET'),
    ('outcome_options','/partials/outcome-options','GET'),
    ('pick_insight','/pick-insight/<int:matchup_id>','GET'),
    ('make_pick','/pick','POST'),('edit_auto_draft','/auto-draft/<int:matchup_id>','POST')])
routes('denied_admin_or_join', [('admin','/admin','GET'),('admin_login','/admin/login','POST'),
    ('admin_logout','/admin/logout','POST'),('admin_import_api_season','/admin/import-api-season','POST'),
    ('admin_sync_api_results','/admin/sync-api-results','POST'),('admin_generate_recap','/admin/generate-recap','POST'),
    ('admin_classify_correspondent_sources','/admin/classify-correspondent-sources','POST'),
    ('admin_sync_correspondent_classification_jobs','/admin/sync-correspondent-classification-jobs','POST'),
    ('admin_add_correspondent_source','/admin/correspondent-sources','POST'),
    ('admin_select_recap','/admin/select-recap','POST'),
    ('admin_set_correspondent_source_status','/admin/correspondent-sources/<int:source_id>/status','POST'),
    ('admin_set_results','/admin/set-results','POST'),('set_result','/set_result','POST'),('join','/join','GET POST')])
MACHINE = {
    'live_ingest_snapshot': ('LIVE_INGEST_SECRET','X-Live-Ingest-Secret'),
    'scheduled_sync_pick_insight': ('SYNC_SECRET','X-Sync-Secret'),
    'scheduled_sync_football_reference': ('SYNC_SECRET','X-Sync-Secret'),
    'scheduled_pre_match_brief_status': ('SYNC_SECRET','X-Sync-Secret'),
    'scheduled_sync_results': ('SYNC_SECRET','X-Sync-Secret'),
    'ingest_correspondent_source_api': ('CORRESPONDENT_INGEST_SECRET','X-Correspondent-Secret'),
    'ingest_correspondent_source_batch_api': ('CORRESPONDENT_INGEST_SECRET','X-Correspondent-Secret'),
}
for endpoint, rule, method in [
    ('live_ingest_snapshot','/tasks/live-ingest','POST'),
    ('scheduled_sync_pick_insight','/tasks/sync-pick-insight','POST'),
    ('scheduled_sync_football_reference','/tasks/sync-football-reference','POST'),
    ('scheduled_pre_match_brief_status','/tasks/pre-match-briefs/status','GET'),
    ('scheduled_sync_results','/tasks/sync-results','POST'),
    ('ingest_correspondent_source_api','/api/correspondent/sources','POST'),
    ('ingest_correspondent_source_batch_api','/api/correspondent/sources/batch','POST')]:
    routes('machine', [(endpoint,rule,method)])
for suffix, endpoint, method in [
    ('status','status','GET'),('targets','targets','GET'),('x/claim','x_claim','POST'),
    ('x/checkpoint','x_checkpoint','POST'),('classification/submit','classification_submit','POST'),
    ('classification/reconcile','classification_reconcile','POST'),('recap/generate','recap_generate','POST'),
    ('delivery/claim','delivery_claim','POST'),('delivery/ack','delivery_ack','POST'),
    ('notifications/x-cap/claim','x_cap_notification_claim','POST'),
    ('notifications/x-cap/ack','x_cap_notification_ack','POST')]:
    endpoint = 'scheduled_correspondent_' + endpoint
    MACHINE[endpoint] = ('CORRESPONDENT_AUTOMATION_SECRET','X-Correspondent-Automation-Secret')
    routes('machine', [(endpoint,'/tasks/correspondent/'+suffix,method)])


def signature(rule):
    return rule.endpoint,rule.rule,frozenset(rule.methods-{'HEAD','OPTIONS'})


def inventory(app):
    return [dict(endpoint=r.endpoint,rule=r.rule,methods=sorted(r.methods-{'HEAD','OPTIONS'}),
                 category=ROUTES.get(signature(r),'unclassified_deny'))
            for r in app.url_map.iter_rules()]


def install_boundary(domain):
    app=domain.app
    auth=app.extensions['runtime_auth']
    models={m.local_table.name:m.class_ for m in domain.Base.registry.mappers}
    allowed={}
    def limits(name,columns):
        if name in ('players','seasons'):
            root=table(name,column('id'),column('league_id')).alias('boundary_'+name)
            return columns['id'].in_(select(root.c.id).where(root.c.league_id==1))
        conditions=[]
        for field in models[name].__table__.columns:
            for fk in field.foreign_keys:
                parent=fk.column.table.name
                if parent not in models:raise RuntimeError('UNCLASSIFIED_LEGACY_MODEL')
                condition=columns[field.name].in_(ids(parent,fk.column.name))
                conditions.append(or_(columns[field.name].is_(None),condition) if field.nullable else condition)
        if not conditions:raise RuntimeError('UNCLASSIFIED_LEGACY_MODEL')
        return and_(*conditions)
    def ids(name,key):
        if (name,key) not in allowed:
            alias=models[name].__table__.alias('boundary_'+name)
            allowed[name,key]=select(alias.c[key]).where(limits(name,alias.c))
        return allowed[name,key]
    options=[with_loader_criteria(model,limits(name,{c.name:getattr(model,c.name)
             for c in model.__table__.columns}),include_aliases=True)
             for name,model in sorted(models.items())]

    def scope_queries(state):
        if (not has_request_context() or current_app._get_current_object() is not app or
                not getattr(g,'loyola_legacy_boundary',False) or state.session.get_bind() is not domain.engine):return
        if state.is_orm_statement:
            state.statement=state.statement.options(*options)
        elif str(state.statement).strip().upper() != 'BEGIN IMMEDIATE':
            # The existing draft command's transaction opener is the only raw
            # statement allowed; no unscoped Core/text reads or writes.
            abort(403)
    event.listen(Session,'do_orm_execute',scope_queries)

    @app.before_request
    def guard():
        if request.url_rule is None:return
        category=ROUTES.get(signature(request.url_rule),'unclassified_deny')
        if category in ('public_static','public_auth','account'):return
        if category=='unclassified_deny':abort(403)
        if category=='machine':
            key,header=MACHINE[request.endpoint]
            expected=os.environ.get(key,'')
            if key!='LIVE_INGEST_SECRET':expected=expected.strip()
            if not expected.strip():abort(503)
            if not hmac.compare_digest(expected.encode('utf-8'),request.headers.get(header,'').encode('utf-8')):abort(403)
            return  # Original endpoint authentication and validation still execute.
        if not request.is_secure or request.host_url.rstrip('/')!=auth['origin']:abort(403)
        try:
            with domain.SessionLocal() as db:
                user=auth['resolve_user'](db)
                if category!='legacy':abort(403)
                member=db.execute(text("""SELECT p.id FROM players p JOIN leagues l ON l.id=p.league_id
                    WHERE p.user_id=:user AND p.status='active' AND p.left_at IS NULL
                    AND l.id=1 AND l.slug='loyola-pick-em' AND l.status IN ('setup','active')"""),dict(user=user)).scalar_one_or_none()
                if member is None:abort(403)
                g.loyola_legacy_boundary=True
                g.loyola_legacy_player_id=member
                # Selectors narrow existing authorized data; none establishes identity.
                selectors={'fixture_id':domain.Fixture,'matchup_id':domain.Matchup,'pick_id':domain.Pick,
                           'recap_id':domain.WeeklyRecap,'source_id':domain.CorrespondentSource,
                           'player':domain.Player,'player_id':domain.Player,'opponent':domain.Player,'club_player':domain.Player}
                values={}
                for key,value in (request.view_args or {}).items():values.setdefault(key,[]).append(str(value))
                for source in (request.args,request.form):
                    for key in source:values.setdefault(key,[]).extend(source.getlist(key))
                for value in values.get('season',[]):
                    if not value or db.query(domain.Season).filter_by(code=value).first() is None:abort(404)
                for key,model in selectors.items():
                    for value in values.get(key,[]):
                        if (not value.isascii() or not value.isdecimal() or len(value)>19
                                or not 0<int(value)<2**63 or db.get(model,int(value)) is None):abort(404)
                for key in ('league','league_id'):
                    if any(v!='1' for v in values.get(key,[])):abort(404)
                if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('Origin')!=auth['origin']:abort(403)
        except AuthRefused:
            return 'Verified membership refused',403
        except (sqlite3.Error,SQLAlchemyError):
            return 'Verified legacy boundary unavailable',503

    @app.after_request
    def prevent_private_caching(response):
        if request.url_rule is not None and ROUTES.get(signature(request.url_rule)) not in ('public_static','public_auth','account'):
            response.headers['Cache-Control']='private, no-store'
            response.headers['Referrer-Policy']='no-referrer'
        return response

    app.extensions['legacy_boundary']=dict(inventory=lambda:inventory(app),listener=scope_queries)
