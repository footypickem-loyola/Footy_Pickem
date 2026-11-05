"""Opt-in verified identity and read-only memberships in the actual Flask runtime.

No legacy session upgrade, game DB writes, automatic linking or gameplay routing.
"""
import base64
import hashlib
import hmac
from pathlib import Path
from urllib.parse import urlencode
import sqlite3
from flask import Blueprint, abort, jsonify, redirect, render_template_string, request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from runtime_auth0 import Auth0Config, Auth0Provider, verify_id_token
from runtime_auth_sessions import SessionStore, AuthRefused, need

COOKIE = '__Host-footy_runtime'


def install_from_environment(app, *, session_factory, game_database, values):
    config = Auth0Config.from_mapping(values)
    if not config.enabled:
        return False
    need(bool(app.secret_key) and app.secret_key != 'devsecret')
    path = Path(config.session_path)
    need(path.stat().st_nlink == 1)
    need(all(not p.is_symlink() and not getattr(p,'is_junction',lambda:False)()
             for p in (path,*path.parents)))
    need(game_database and path.resolve() != Path(game_database).resolve())
    store = SessionStore(path)
    provider = Auth0Provider(config)  # No discovery/JWKS request during startup.
    settings = config.settings
    bp = Blueprint('runtime_auth',__name__,url_prefix='/auth')

    @bp.before_request
    def secure_origin():
        need(request.is_secure and request.host_url.rstrip('/') == settings.origin)

    @bp.after_request
    def private_headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'none'; form-action 'self' " + settings.issuer.rstrip('/') + "; frame-ancestors 'none'; base-uri 'none'"
        return response

    @bp.errorhandler(AuthRefused)
    def refused(error):
        return 'Authentication or membership refused',403

    @bp.errorhandler(sqlite3.Error)
    @bp.errorhandler(SQLAlchemyError)
    def unavailable(error):
        return 'Authentication storage unavailable',503

    def current():
        sid = request.cookies.get(COOKIE)
        row = store.get(sid)
        if row is None or row[0] != settings.issuer or row[1] is None:
            abort(401)
        return sid,row

    def csrf(row):
        value = request.form.get('csrf','')
        need(request.headers.get('Origin') == settings.origin and
             isinstance(value,str) and value.isascii() and hmac.compare_digest(value,row[2]))

    def cookie(response,sid):
        response.set_cookie(COOKIE,sid,secure=True,httponly=True,samesite='Lax',path='/',max_age=store.ttl)
        return response

    def account(db,issuer,subject):
        # No lookup by request user_id, display name, email or legacy player_name.
        user = db.execute(text("""SELECT u.id FROM user_identities i
            JOIN users u ON u.id=i.user_id
            WHERE i.issuer=:issuer AND i.subject=:subject AND u.status='active'"""),
            dict(issuer=issuer,subject=subject)).scalar_one_or_none()
        need(user is not None)
        return user

    @bp.get('/login')
    def login_form():
        need(not request.args)
        sid = request.cookies.get(COOKIE)
        row = store.get(sid)
        if row is None:
            sid = store.new(); row = store.get(sid)
        page = render_template_string('<h1>Account login</h1><form method="post"><input type="hidden" name="csrf" value="{{csrf}}"><button>Continue to login</button></form>',csrf=row[2])
        return cookie(app.make_response(page),sid)

    @bp.post('/login')
    def login():
        need(not request.args and set(request.form)=={'csrf'} and len(request.form.getlist('csrf'))==1)
        sid = request.cookies.get(COOKIE); row = store.get(sid); need(row is not None); csrf(row)
        state,nonce,verifier = store.begin(sid)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        return redirect(settings.authorization_endpoint+'?'+urlencode(dict(response_type='code',client_id=settings.client_id,redirect_uri=settings.redirect_uri,scope='openid',state=state,nonce=nonce,code_challenge=challenge,code_challenge_method='S256')),303)

    @bp.get('/callback')
    def callback():
        sid = request.cookies.get(COOKIE); need(store.get(sid) is not None)
        need(set(request.args) in ({'code','state'},{'code','state','iss'}) and
             all(len(request.args.getlist(k))==1 for k in request.args))
        need(isinstance(request.args['code'],str) and 0<len(request.args['code'])<=2048)
        nonce,verifier = store.consume(sid,request.args['state'])
        provider.validate_response_issuer(request.args.get('iss'))
        token = provider.exchange(code=request.args['code'],code_verifier=verifier,redirect_uri=settings.redirect_uri)
        issuer,subject = verify_id_token(token,settings,provider,nonce)
        with session_factory() as db:
            account(db,issuer,subject)
        new = store.rotate(sid,issuer,subject)
        return cookie(redirect('/auth/leagues',303),new)

    @bp.get('/leagues')
    @bp.get('/leagues/<int:league_id>')
    def leagues(league_id=None):
        need(not request.args)
        _,row = current()
        with session_factory() as db:
            user = account(db,row[0],row[1])  # Revalidate revocation on every request.
            query = """SELECT l.id,l.slug,l.name,l.status,p.id AS membership_id,p.role
                FROM players p JOIN leagues l ON l.id=p.league_id
                WHERE p.user_id=:user AND p.status='active' AND p.left_at IS NULL
                AND l.status IN ('setup','active')"""
            params = dict(user=user)
            if league_id is not None:
                query += ' AND l.id=:league'; params['league'] = league_id
            rows = db.execute(text(query+' ORDER BY l.id'),params).mappings().all()
            if league_id is not None and not rows:
                abort(404)  # Same response for unknown and unauthorized leagues.
            data = [dict(r) for r in rows]
        return jsonify(leagues=data,logout_csrf=row[2])

    @bp.post('/logout')
    def logout():
        need(not request.args and set(request.form)=={'csrf'} and len(request.form.getlist('csrf'))==1)
        sid,row = current(); csrf(row); store.revoke(sid)
        response = app.make_response(('',204))
        response.delete_cookie(COOKIE,path='/',secure=True,httponly=True,samesite='Lax')
        return response  # Local logout only; does not call Auth0 or clear Loyola.

    app.register_blueprint(bp)
    def resolve_user(db):
        _,row=current()
        return account(db,row[0],row[1])

    app.extensions['runtime_auth'] = dict(store=store,provider=provider,
                                          resolve_user=resolve_user,origin=settings.origin)
    return True
