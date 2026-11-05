"""Explicitly initialized, shared-process SQLite session store; never the game DB."""
from contextlib import contextmanager,closing
from pathlib import Path
import hashlib,secrets,sqlite3,time

class AuthRefused(ValueError): pass

def need(ok):
    if not ok: raise AuthRefused('AUTHENTICATION_REFUSED')

def digest(value): return hashlib.sha256(value.encode('ascii')).hexdigest()

DDL="""CREATE TABLE runtime_auth_schema(version INTEGER PRIMARY KEY CHECK(version=1));
INSERT INTO runtime_auth_schema VALUES(1);
CREATE TABLE auth_sessions(id TEXT PRIMARY KEY,issuer TEXT,subject TEXT,csrf TEXT NOT NULL,expires INTEGER NOT NULL);
CREATE TABLE auth_transactions(state TEXT PRIMARY KEY,session_id TEXT NOT NULL,nonce TEXT NOT NULL,
 verifier TEXT NOT NULL,expires INTEGER NOT NULL);
"""

class SessionStore:
    @staticmethod
    def initialize(path):
        p=Path(path); need(p.is_absolute() and p.parent.is_dir())
        # Explicit installation only. Never adopt or overwrite another database.
        with p.open('xb'): pass
        with closing(sqlite3.connect(p)) as db:
            db.executescript(DDL);db.commit()

    def __init__(self,path,*,clock=time.time,session_seconds=28800,transaction_seconds=300):
        p=Path(path); need(p.is_absolute() and p.is_file())
        need(60<=session_seconds<=28800 and 30<=transaction_seconds<=600)
        self.path=p.resolve();self.clock=clock;self.ttl=session_seconds;self.tx_ttl=transaction_seconds
        # Check an existing store; never provision or migrate one on startup.
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
            need(db.execute('SELECT version FROM runtime_auth_schema').fetchall()==[(1,)])

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=2)) as db:
            try:
                need(db.execute('SELECT version FROM runtime_auth_schema').fetchall()==[(1,)])
                db.execute('BEGIN IMMEDIATE'); yield db; db.commit()
            except BaseException: db.rollback(); raise

    def new(self):
        sid=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('INSERT INTO auth_sessions VALUES(?,NULL,NULL,?,?)',(digest(sid),csrf,int(self.clock())+self.ttl))
        return sid

    def get(self,sid):
        if not isinstance(sid,str) or not 40<=len(sid)<=128 or not sid.isascii(): return None
        with self.db() as db:
            row=db.execute('SELECT issuer,subject,csrf,expires FROM auth_sessions WHERE id=?',(digest(sid),)).fetchone()
            return row if row and row[3]>self.clock() else None

    def begin(self,sid):
        state=secrets.token_urlsafe(32); nonce=secrets.token_urlsafe(32); verifier=secrets.token_urlsafe(48)
        with self.db() as db:
            need(db.execute('SELECT 1 FROM auth_sessions WHERE id=? AND expires>?',(digest(sid),self.clock())).fetchone() is not None)
            db.execute('INSERT INTO auth_transactions VALUES(?,?,?,?,?)',
                (digest(state),digest(sid),nonce,verifier,int(self.clock())+self.tx_ttl))
        return state,nonce,verifier

    def consume(self,sid,state):
        need(isinstance(state,str) and state.isascii() and 40<=len(state)<=128)
        with self.db() as db:
            row=db.execute('SELECT nonce,verifier,expires FROM auth_transactions WHERE state=? AND session_id=?',(digest(state),digest(sid))).fetchone()
            need(row is not None)
            db.execute('DELETE FROM auth_transactions WHERE state=?',(digest(state),))
        need(row[2]>self.clock())
        return row[:2]

    def rotate(self,old,issuer,subject):
        sid=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
        with self.db() as db:
            need(db.execute('DELETE FROM auth_sessions WHERE id=? AND expires>?',(digest(old),self.clock())).rowcount==1)
            db.execute('DELETE FROM auth_transactions WHERE session_id=?',(digest(old),))
            db.execute('INSERT INTO auth_sessions VALUES(?,?,?,?,?)',(digest(sid),issuer,subject,csrf,int(self.clock())+self.ttl))
        return sid

    def revoke(self,sid):
        with self.db() as db:
            db.execute('DELETE FROM auth_transactions WHERE session_id=?',(digest(sid),))
            db.execute('DELETE FROM auth_sessions WHERE id=?',(digest(sid),))
