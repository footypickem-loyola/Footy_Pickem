"""Real Flask Auth0 slice; signed synthetic OIDC, disposable DBs, no sockets."""
import base64
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import sys
import tempfile
import time
import unittest
from urllib.parse import parse_qs,urlsplit,urlencode
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from flask_session import Session
import runtime_auth0 as oidc
from runtime_auth_sessions import SessionStore,AuthRefused
from runtime_league_auth import COOKIE

ROOT=Path(__file__).resolve().parents[1]
BASE='https://localhost'
ISSUER='https://oidc.example.invalid/'


class Wire:
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    wrong=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    def __init__(self):
        self.claims={};self.headers={};self.bad_signature=False;self.subject='synthetic-one'
        self.codes={};self.calls=[];self.metadata_changes={};self.jwks_changes={}
    def request(self,url,*,form=None):
        self.calls.append(url)
        if url.endswith('openid-configuration'):
            return dict(dict(issuer=ISSUER,authorization_endpoint=ISSUER+'authorize',token_endpoint=ISSUER+'oauth/token',jwks_uri=ISSUER+'.well-known/jwks.json',id_token_signing_alg_values_supported=['RS256'],response_types_supported=['code'],code_challenge_methods_supported=['S256'],token_endpoint_auth_methods_supported=['client_secret_post'],authorization_response_iss_parameter_supported=True),**self.metadata_changes)
        if url.endswith('jwks.json'):
            return dict(keys=[{**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key())), 'kid':'synthetic-key','alg':'RS256','use':'sig',**self.jwks_changes}])
        if url.endswith('oauth/token'):
            challenge,token=self.codes.pop(form['code'])
            actual=base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'].encode('ascii')).digest()).rstrip(b'=').decode('ascii')
            if actual!=challenge: raise AuthRefused('PKCE_REFUSED')
            return dict(id_token=token)
        raise AssertionError('unexpected synthetic URL')
    def authorize(self,url):
        q={k:v[0] for k,v in parse_qs(urlsplit(url).query).items()}
        assert q['scope']=='openid' and q['code_challenge_method']=='S256'
        assert q['redirect_uri']==BASE+'/auth/callback' and q['client_id']=='synthetic-client'
        claims=dict(iss=ISSUER,sub=self.subject,aud='synthetic-client',iat=int(time.time()),exp=int(time.time())+300,nonce=q['nonce'])
        claims.update(self.claims)
        token=jwt.encode(claims,self.wrong if self.bad_signature else self.key,algorithm='RS256',headers={'kid':'synthetic-key',**self.headers})
        code=secrets.token_urlsafe(32);self.codes[code]=(q['code_challenge'],token)
        return '/auth/callback?'+urlencode(dict(code=code,state=q['state'],iss=ISSUER))


class RuntimeAuthTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(socket.socket,'connect',side_effect=AssertionError('NETWORK_DISABLED')))
        self.enterContext(patch.object(socket,'create_connection',side_effect=AssertionError('NETWORK_DISABLED')))
        self.folder=Path(self.enterContext(tempfile.TemporaryDirectory(prefix='runtime-auth-synthetic-')))
        self.game=self.folder/'synthetic-game.db';self.auth=self.folder/'synthetic-auth.db'
        SessionStore.initialize(self.auth)
        self.values=dict(DB_PATH='sqlite:///'+str(self.game),INIT_ON_START='0',FLASK_SECRET='synthetic-flask-key-not-real',RUNTIME_AUTH0_ENABLED='1',RUNTIME_AUTH0_ISSUER=ISSUER,RUNTIME_AUTH0_CLIENT_ID='synthetic-client',RUNTIME_AUTH0_CLIENT_SECRET='synthetic-client-secret-not-real',RUNTIME_AUTH0_CALLBACK_URL=BASE+'/auth/callback',RUNTIME_AUTH0_SESSION_DB=str(self.auth))
        self.wire=Wire();self.enterContext(patch.object(oidc,'request_json',side_effect=self.wire.request))
        self.module=self.load_app(self.values)
        self.app=self.module.app;self.app.testing=True;self.client=self.app.test_client()
        self.store=self.app.extensions['runtime_auth']['store']
        self.module.Base.metadata.create_all(self.module.engine)
        with closing(sqlite3.connect(self.game)) as db:
            db.executescript("""
            CREATE TABLE users(id INTEGER PRIMARY KEY,display_name TEXT,email TEXT,status TEXT);
            CREATE TABLE user_identities(id INTEGER PRIMARY KEY,user_id INTEGER,issuer TEXT,subject TEXT,UNIQUE(issuer,subject));
            CREATE TABLE leagues(id INTEGER PRIMARY KEY,slug TEXT,name TEXT,status TEXT);
            ALTER TABLE players ADD COLUMN user_id INTEGER;
            ALTER TABLE players ADD COLUMN league_id INTEGER;
            ALTER TABLE players ADD COLUMN status TEXT;
            ALTER TABLE players ADD COLUMN left_at TEXT;
            ALTER TABLE players ADD COLUMN role TEXT;
            INSERT INTO users VALUES(1,'Synthetic One','shared@example.invalid','active'),(2,'Synthetic Two','other@example.invalid','active'),(3,'Unclaimed','unclaimed@example.invalid','unclaimed');
            INSERT INTO user_identities VALUES(1,1,'https://oidc.example.invalid/','synthetic-one'),(2,2,'https://oidc.example.invalid/','synthetic-two'),(3,3,'https://oidc.example.invalid/','unclaimed');
            INSERT INTO leagues VALUES(1,'loyola-synthetic','Loyola Synthetic','setup'),(2,'other-synthetic','Other Synthetic','active'),(3,'also-synthetic','Also Synthetic','active');
            INSERT INTO players(id,name,user_id,league_id,status,role) VALUES(1,'Synthetic Player',1,1,'active','member'),(2,'Other Player',2,2,'active','commissioner'),(3,'Second Membership',1,3,'active','member');
            """)
    def load_app(self,values):
        name='runtime_actual_'+secrets.token_hex(6)
        spec=importlib.util.spec_from_file_location(name,ROOT/'pickem_flask_htmx_tabs.py')
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module
        self.addCleanup(lambda:sys.modules.pop(name,None))
        original=Session.init_app
        def configure(self_,app):
            app.config['SESSION_FILE_DIR']=str(self.folder/(name+'-legacy-sessions'))
            return original(self_,app)
        with patch.dict(os.environ,values),patch.object(Session,'init_app',configure):
            spec.loader.exec_module(module)
        self.addCleanup(module.engine.dispose);self.addCleanup(module.SessionLocal.remove)
        return module
    def get(self,path,client=None,**kwargs):return (client or self.client).get(path,base_url=BASE,**kwargs)
    def post(self,path,data,client=None,origin=BASE):return (client or self.client).post(path,data=data,base_url=BASE,headers={'Origin':origin})
    def begin(self):
        page=self.get('/auth/login');self.assertEqual(page.status_code,200)
        csrf=re.search(b'name="csrf" value="([^"]+)"',page.data)[1].decode()
        response=self.post('/auth/login',dict(csrf=csrf));self.assertEqual(response.status_code,303)
        return self.wire.authorize(response.location)
    def login(self):
        callback=self.begin();response=self.get(callback);self.assertEqual(response.status_code,303)
        return callback,response
    def snapshot(self):
        with closing(sqlite3.connect(self.game)) as db:
            return hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest()
    def change(self,sql,args=()):
        with closing(sqlite3.connect(self.game)) as db,db:db.execute(sql,args)

    def test_login_returns_only_active_member_leagues_without_game_writes(self):
        before=self.snapshot();self.assertEqual(self.wire.calls,[])
        self.login();response=self.get('/auth/leagues');self.assertEqual(response.status_code,200)
        self.assertEqual([r['id'] for r in response.json['leagues']],[1,3])
        self.assertEqual(self.get('/auth/leagues/1').status_code,200)
        self.assertEqual(self.get('/auth/leagues/2').status_code,404)
        self.assertEqual(self.get('/auth/leagues/999').status_code,404)
        self.assertNotIn(b'Other Synthetic',response.data)
        self.assertEqual(before,self.snapshot())
        self.assertEqual(response.headers['Cache-Control'],'no-store')

    def test_unauthenticated_legacy_and_user_supplied_identity_are_not_accounts(self):
        self.assertEqual(self.get('/auth/leagues').status_code,401)
        with self.client.session_transaction() as s:s['player_name']='Synthetic Player';s['user_id']=1;s['issuer']=ISSUER;s['subject']='synthetic-one'
        self.assertEqual(self.get('/auth/leagues').status_code,401)
        self.client.set_cookie(COOKIE,'attacker-controlled-session')
        self.assertEqual(self.get('/auth/leagues').status_code,401)
        self.assertEqual(self.get('/auth/leagues?user_id=1').status_code,403)

    def test_bad_claims_signature_key_and_header_are_rejected(self):
        for change in ({'iss':'https://wrong.example.invalid/'},{'aud':'wrong'},{'aud':['synthetic-client','extra']},{'nonce':'wrong'},{'nonce':None},{'exp':int(time.time())-1},{'iat':int(time.time())+3600},{'sub':''},{'azp':'wrong'}):
            with self.subTest(fields=list(change)):
                self.wire.claims=change
                self.assertEqual(self.get(self.begin()).status_code,403)
        self.wire.claims={};self.wire.bad_signature=True
        self.assertEqual(self.get(self.begin()).status_code,403)
        self.wire.bad_signature=False
        for headers in ({'kid':'unknown'},{'jku':'https://wrong.example.invalid/keys'},{'crit':['unsafe']}):
            with self.subTest(header=list(headers)):
                self.wire.headers=headers
                self.assertEqual(self.get(self.begin()).status_code,403)

    def test_state_browser_binding_replay_and_expiry(self):
        callback=self.begin();other=self.app.test_client()
        self.assertEqual(self.get(callback,client=other).status_code,403)
        self.assertEqual(self.get(re.sub('state=[^&]+','state=wrong',callback)).status_code,403)
        self.assertEqual(self.get(callback).status_code,303)
        self.assertEqual(self.get(callback).status_code,403)
        callback=self.begin();self.store.clock=lambda:time.time()+301
        self.assertEqual(self.get(callback).status_code,403)
        self.store.clock=time.time
        self.login();self.store.clock=lambda:time.time()+28801
        self.assertEqual(self.get('/auth/leagues').status_code,401)

    def test_unknown_unclaimed_disabled_and_email_collision_do_not_link(self):
        for subject in ('unknown','unclaimed'):
            self.wire.subject=subject
            self.wire.claims=dict(email='shared@example.invalid',email_verified=True,name='Synthetic One',user_id=1)
            before=self.snapshot();self.assertEqual(self.get(self.begin()).status_code,403)
            self.assertEqual(before,self.snapshot())
        self.wire.subject='synthetic-one';self.wire.claims={}
        self.change("UPDATE users SET status='disabled' WHERE id=1")
        self.assertEqual(self.get(self.begin()).status_code,403)

    def test_membership_and_identity_revocation_are_rechecked(self):
        self.login();self.change("UPDATE players SET status='inactive' WHERE league_id=3")
        self.assertEqual([r['id'] for r in self.get('/auth/leagues').json['leagues']],[1])
        self.change("UPDATE players SET left_at='2026-01-01' WHERE league_id=1")
        self.assertEqual(self.get('/auth/leagues').json['leagues'],[])
        self.change("DELETE FROM user_identities WHERE user_id=1")
        self.assertEqual(self.get('/auth/leagues').status_code,403)

    def test_session_rotation_logout_and_csrf_do_not_upgrade_legacy_access(self):
        callback=self.begin();old=self.client.get_cookie(COOKIE).value
        response=self.get(callback);new=self.client.get_cookie(COOKIE).value
        self.assertNotEqual(old,new);self.assertIsNone(self.store.get(old))
        cookie=response.headers['Set-Cookie']
        for flag in ('Secure','HttpOnly','SameSite=Lax'):self.assertIn(flag,cookie)
        self.assertNotIn('Domain=',cookie)
        with self.client.session_transaction() as s:self.assertNotIn('player_name',s)
        data=self.get('/auth/leagues').json
        self.assertEqual(self.post('/auth/logout',dict(csrf=data['logout_csrf']),origin='https://wrong.example.invalid').status_code,403)
        self.assertEqual(self.post('/auth/logout',dict(csrf=data['logout_csrf'])).status_code,204)
        self.assertIsNone(self.store.get(new));self.assertEqual(self.get('/auth/leagues').status_code,401)

    def test_secure_origin_duplicate_parameters_and_response_issuer(self):
        self.assertEqual(self.client.get('/auth/login',base_url='http://localhost').status_code,403)
        callback=self.begin()
        self.assertEqual(self.get(callback+'&state=duplicate').status_code,403)
        self.assertEqual(self.get(callback.replace('iss=https%3A%2F%2Foidc.example.invalid%2F','iss=https%3A%2F%2Fwrong.example.invalid%2F')).status_code,403)
        self.assertEqual(self.get(self.begin().split('&iss=')[0]).status_code,403)
        self.assertEqual(self.get('/auth/login?next=https://wrong.example.invalid').status_code,403)

    def test_feature_disabled_keeps_legacy_join_on_unmigrated_schema(self):
        values=dict(self.values,RUNTIME_AUTH0_ENABLED='0',DB_PATH='sqlite:///'+str(self.folder/'legacy-only.db'))
        with patch.object(oidc,'Auth0Provider',side_effect=AssertionError('must not construct')):
            module=self.load_app(values)
        module.app.testing=True;module.Base.metadata.create_all(module.engine)
        with module.SessionLocal() as db:
            player=module.Player(name='Synthetic Legacy');season=module.Season(code='year-2',name='Legacy',is_active=1,is_archived=0)
            db.add_all([player,season]);db.flush();db.add(module.Week(season_id=season.id,number=1,room_code='SYNTHETIC',status='drafting'));db.commit()
        client=module.app.test_client()
        self.assertEqual(client.get('/join').status_code,200)
        self.assertEqual(client.post('/join',data=dict(name='Synthetic Legacy',room_code='SYNTHETIC')).status_code,302)
        with client.session_transaction() as s:self.assertEqual(s['player_name'],'Synthetic Legacy')
        self.assertEqual(client.get('/auth/login').status_code,404)
        self.assertEqual(client.get('/auth/leagues').status_code,404)

    def test_missing_foundation_fails_closed_without_schema_changes(self):
        self.change('DROP TABLE user_identities')
        before=self.snapshot();self.assertEqual(self.get(self.begin()).status_code,503)
        self.assertEqual(before,self.snapshot())

    def test_disabled_startup_needs_no_optional_jwt_import_or_database(self):
        import builtins
        original=builtins.__import__
        def guarded(name,*args,**kwargs):
            if name in ('runtime_league_auth','runtime_auth0','jwt'):raise AssertionError('optional auth imported')
            return original(name,*args,**kwargs)
        path=self.folder/'never-opened.db'
        with patch.object(builtins,'__import__',side_effect=guarded):
            module=self.load_app(dict(self.values,RUNTIME_AUTH0_ENABLED='0',DB_PATH='sqlite:///'+str(path)))
        self.assertFalse(path.exists())


    def test_wrong_algorithm_missing_expiry_and_pkce_are_rejected(self):
        for kind in ('algorithm','expiry','pkce'):
            with self.subTest(kind=kind):
                callback=self.begin();code=parse_qs(urlsplit(callback).query)['code'][0]
                challenge,token=self.wire.codes[code]
                if kind=='pkce':challenge='wrong-synthetic-challenge'
                else:
                    claims=jwt.decode(token,options={'verify_signature':False})  # Test fixture only.
                    if kind=='expiry':del claims['exp']
                    token=jwt.encode(claims,b'synthetic-hmac-key-for-rejection-only' if kind=='algorithm' else self.wire.key,
                        algorithm='HS256' if kind=='algorithm' else 'RS256',headers={'kid':'synthetic-key'})
                self.wire.codes[code]=(challenge,token)
                self.assertEqual(self.get(callback).status_code,403)

    def test_untrusted_discovery_and_jwks_are_rejected(self):
        self.wire.metadata_changes={'jwks_uri':'https://wrong.example.invalid/keys'}
        self.assertEqual(self.get(self.begin()).status_code,403)
        self.wire.metadata_changes={};self.wire.jwks_changes={'alg':'HS256'}
        self.assertEqual(self.get(self.begin()).status_code,403)

    def test_configuration_and_game_store_alias_refuse_before_use(self):
        from runtime_league_auth import install_from_environment
        for changes in ({'RUNTIME_AUTH0_ENABLED':'wrong'},
                        {'RUNTIME_AUTH0_SESSION_DB':str(self.folder/'missing.db')},
                        {'RUNTIME_AUTH0_ISSUER':'http://oidc.example.invalid/'}):
            with self.subTest(fields=list(changes)):
                with self.assertRaises(AuthRefused):oidc.Auth0Config.from_mapping(dict(self.values,**changes))
        with self.assertRaises(AuthRefused):
            install_from_environment(self.app,session_factory=self.module.SessionLocal,game_database=str(self.game),
                values=dict(self.values,RUNTIME_AUTH0_SESSION_DB=str(self.game)))
        self.assertNotIn(self.values['RUNTIME_AUTH0_CLIENT_SECRET'],repr(oidc.Auth0Config.from_mapping(self.values)))

    def test_exact_issuer_association_and_wrong_store_contract_refuse(self):
        self.change("UPDATE user_identities SET issuer='https://other.example.invalid/' WHERE user_id=1")
        self.assertEqual(self.get(self.begin()).status_code,403)
        other=self.folder/'wrong-store.db'
        with closing(sqlite3.connect(other)) as db:
            db.executescript('CREATE TABLE auth_schema(version INTEGER); INSERT INTO auth_schema VALUES(1);')
        with self.assertRaises(sqlite3.Error):SessionStore(other)

    def test_session_state_is_consumed_once_across_store_instances(self):
        callback=self.begin();sid=self.client.get_cookie(COOKIE).value
        state=parse_qs(urlsplit(callback).query)['state'][0]
        other=SessionStore(self.auth)
        self.assertEqual(len(other.consume(sid,state)),2)
        with self.assertRaises(AuthRefused):self.store.consume(sid,state)
        self.assertEqual(self.get(callback).status_code,403)


if __name__=='__main__':unittest.main()
