#!/usr/bin/env python3
import argparse
import os
import random
from datetime import datetime
from typing import List, Tuple, Dict, Iterable, Optional

from flask import Flask, request, session, redirect, url_for, render_template_string, abort
from flask_session import Session
from sqlalchemy import (
    create_engine, Column, Integer, String, ForeignKey, UniqueConstraint, DateTime, event
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, scoped_session
from jinja2 import DictLoader
import pandas as pd

# -------------------- In-memory base + partial templates --------------------
BASE_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>EPL Pick 'Em</title>
  <script src="https://unpkg.com/htmx.org@2.0.2"></script>
  <style>
    :root { --blue:#0ea5e9; }
    body { font-family: -apple-system, system-ui, Segoe UI, Roboto, sans-serif; margin: 24px; color: #111; }
    a { color: var(--blue); text-decoration: none; }
    nav { display:flex; gap:12px; align-items:center; margin-bottom:16px; }
    .tabs { display:flex; gap:8px; align-items:center; }
    .tab { padding:8px 12px; border:1px solid #ddd; border-radius:999px; cursor:pointer; background:#f8f8f8; }
    .tab.active { background:var(--blue); color:#fff; border-color:#0284c7; }
    .navright { margin-left:auto; }
    .card { border: 1px solid #ddd; border-radius: 12px; padding: 16px; margin: 12px 0; box-shadow: 0 1px 4px rgba(0,0,0,0.04); }
    .row { display: flex; gap: 16px; flex-wrap: wrap; }
    .col { flex: 1; min-width: 320px; }
    .btn { padding: 8px 12px; border: 1px solid #ccc; background: #f8f8f8; border-radius: 8px; cursor: pointer; }
    .btn.primary { background: var(--blue); color: white; border-color: #0284c7; }
    .muted { color: #666; }
    .badge { display: inline-block; padding: 2px 8px; background: #eef; border: 1px solid #aac; border-radius: 999px; font-size: 12px; }
    select, input { padding: 6px; border: 1px solid #ccc; border-radius: 6px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; }
    .chip { border: 1px solid #ddd; border-radius: 999px; padding: 6px 10px; display: flex; justify-content: space-between; align-items: center; }
    .status { display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid #ccc; font-size:12px; }
    .status.drafting { background:#eef; border-color:#99c; }
    .status.provisional { background:#ffe; border-color:#cc9; }
    .status.finalized { background:#efe; border-color:#9c9; }
  </style>
</head>
<body>
  <nav>
    <div class="tabs">
      <button class="tab {% if active_tab=='current' %}active{% endif %}"
              hx-get="{{ url_for('tab_current') }}"
              hx-target="#main" hx-swap="innerHTML" hx-push-url="true">
        Current Week
      </button>
      <button class="tab {% if active_tab=='open' %}active{% endif %}"
              hx-get="{{ url_for('tab_open') }}"
              hx-target="#main" hx-swap="innerHTML" hx-push-url="true">
        Open Weeks
      </button>
      <button class="tab {% if active_tab=='season' %}active{% endif %}"
              hx-get="{{ url_for('tab_season') }}"
              hx-target="#main" hx-swap="innerHTML" hx-push-url="true">
        Season
      </button>
    </div>
    <div class="navright muted">Logged in as: {{ you.name if you else 'Guest' }}</div>
  </nav>

  <div id="main">
    {% block body %}{% endblock %}
  </div>
</body>
</html>
"""

JOIN_HTML = """
<!doctype html>
<html><head><meta charset="utf-8"><title>Join</title>
  <style>body{font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;margin:24px;} .card{border:1px solid #ddd;border-radius:12px;padding:16px;}</style>
</head><body>
<div class="card">
  <h2>Join EPL Pick 'Em</h2>
  <form method="post">
    <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
      <label>Name <input name="name" required></label>
      <label>Room Code <input name="room_code" required></label>
      <button type="submit">Enter</button>
    </div>
  </form>
  <p class="muted">Allowed players: {{ allowed_names|join(', ') }}</p>
</div>
</body></html>
"""

CURRENT_PARTIAL = """
{% set wk = current_week %}
<div class="row">
  <div class="col">
    <div class="card">
      <h3>Week {{ wk.number }} — Hello, {{ you.name }}</h3>
      <div class="muted">Room: {{ wk.room_code }}</div>
      <div>Status: <span class="status {{ wk.status }}">{{ wk.status|capitalize }}</span></div>
    </div>

    <div class="card" id="fixtures" hx-get="{{ url_for('fixtures_partial', week_number=wk.number) }}" hx-trigger="load">
      Loading fixtures...
    </div>

    <div class="card" id="scores" hx-get="{{ url_for('scores_partial', week_number=wk.number) }}" hx-trigger="load">
      Loading scores...
    </div>
  </div>

  <div class="col">
    <div id="matchups" class="card" hx-get="{{ url_for('matchups_partial', week_number=wk.number) }}" hx-trigger="load">
      Loading matchups...
    </div>
  </div>
</div>
"""

OPEN_PARTIAL = """
<div class="card">
  <h3>Open Weeks</h3>
  <p class="muted">Any week not yet finalized shows up here. Auto-finalizes when all fixtures have results.</p>
  <table>
    <thead><tr><th>Week</th><th>Status</th><th>Completed Fixtures</th><th>Total Fixtures</th></tr></thead>
    <tbody>
      {% for row in open_rows %}
        <tr>
          <td><a href="#" hx-get="{{ url_for('tab_current') }}?force_week={{ row['week'] }}" hx-target="#main" hx-swap="innerHTML" hx-push-url="true">Week {{ row['week'] }}</a></td>
          <td><span class="status {{ row['status'] }}">{{ row['status']|capitalize }}</span></td>
          <td>{{ row['done'] }}</td>
          <td>{{ row['total'] }}</td>
        </tr>
      {% endfor %}
      {% if not open_rows %}
        <tr><td colspan="4" class="muted">All weeks are finalized ✅</td></tr>
      {% endif %}
    </tbody>
  </table>
</div>
"""

SEASON_PARTIAL = """
<div class="card">
  <h3>Season Summary (Final weeks only)</h3>
  <p class="muted">Cumulative points from finalized weeks. Net = For – Against.</p>
  <table>
    <thead><tr><th>Player</th><th>For</th><th>Against</th><th>Net</th></tr></thead>
    <tbody>
      {% for row in season_rows %}
        <tr><td>{{ row['name'] }}</td><td>{{ row['for'] }}</td><td>{{ row['against'] }}</td><td>{{ row['net'] }}</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<div class="card">
  <h4>Weekly rollup</h4>
  <table>
    <thead>
      <tr>
        <th>Week</th>
        {% for p in players %}<th>{{ p.name }}</th>{% endfor %}
        <th>Status</th>
      </tr>
    </thead>
    <tbody>
      {% for wk in weeks %}
      <tr>
        <td><a href="#" hx-get="{{ url_for('tab_current') }}?force_week={{ wk.number }}" hx-target="#main" hx-swap="innerHTML" hx-push-url="true">Week {{ wk.number }}</a></td>
        {% for p in players %}
          <td>{{ weekly_points[wk.number].get(p.id, 0) }}</td>
        {% endfor %}
        <td><span class="status {{ wk.status }}">{{ wk.status|capitalize }}</span></td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
"""

FIXTURES_PARTIAL = """
<h4>Fixtures</h4>
<div class="grid">
{% for f in fixtures %}
  <div class="chip"><span>#{{ f.match_number }}: {{ f.home }} vs {{ f.away }}</span></div>
{% endfor %}
</div>
"""

MATCHUPS_PARTIAL = """
<h4>Matchups (click to pick)</h4>
{% for m in matchups %}
  <div class="card">
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <div><strong>{{ m['a'] }}</strong> vs <strong>{{ m['b'] }}</strong></div>
      <div class="badge">First picker: {{ m['first'] }}</div>
    </div>

    <div class="muted">It's {{ m['turn_name'] }}'s turn</div>

    <div class="row">
      <div class="col">
        <h5>Available</h5>
        {% if m['available'] %}
          <form hx-post="{{ url_for('make_pick') }}" hx-target="#matchups" hx-swap="outerHTML">
            <input type="hidden" name="week" value="{{ week.number }}">
            <input type="hidden" name="matchup_id" value="{{ m['id'] }}">
            <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
              <label>Game
                <select name="fixture_id" required>
                  {% for fx in m['available'] %}
                    <option value="{{ fx['id'] }}">#{{ fx['match_number'] }}: {{ fx['home'] }} vs {{ fx['away'] }}</option>
                  {% endfor %}
                </select>
              </label>
              <label>Team
                <select name="team" required>
                  {% for fx in m['available'] %}
                    <option value="{{ fx['home'] }}">{{ fx['home'] }}</option>
                    <option value="{{ fx['away'] }}">{{ fx['away'] }}</option>
                  {% endfor %}
                </select>
              </label>
              {% if you.id == m['turn_id'] %}
                <button class="btn primary" type="submit">Pick</button>
              {% else %}
                <button class="btn" type="submit" disabled title="Not your turn">Pick</button>
              {% endif %}
            </div>
          </form>
        {% else %}
          <div class="muted">All games picked in this matchup.</div>
        {% endif %}
      </div>
      <div class="col">
        <h5>Pick Log</h5>
        {% if m['log'] %}
        <ul>
          {% for p in m['log'] %}
            <li>{{ p['when'] }} — <strong>{{ p['player'] }}</strong> picked <strong>{{ p['team'] }}</strong> in #{{ p['match_number'] }} ({{ p['home'] }} vs {{ p['away'] }})</li>
          {% endfor %}
        </ul>
        {% else %}
          <div class="muted">(no picks yet)</div>
        {% endif %}
      </div>
    </div>
  </div>
{% endfor %}
"""

SCORES_PARTIAL = """
<h4>Scores & Payouts</h4>
<table>
  <thead><tr><th>Player</th><th>Points</th></tr></thead>
  <tbody>
    {% for row in scores %}
      <tr><td>{{ row['name'] }}</td><td>{{ row['points'] }}</td></tr>
    {% endfor %}
  </tbody>
</table>
<h5>Payouts ($5/pt)</h5>
<table>
  <thead><tr><th>From</th><th>To</th><th>Point Diff</th><th>Payout</th></tr></thead>
  <tbody>
    {% for p in payouts %}
      <tr><td>{{ p.get('from','-') }}</td><td>{{ p.get('to','-') }}</td><td>{{ p.get('points',0) }}</td><td>{{ p.get('payout',0) }}</td></tr>
    {% endfor %}
  </tbody>
</table>
<div class="card">
  <h5>Enter Results</h5>
  <form hx-post="{{ url_for('set_result') }}" hx-target="#scores" hx-swap="outerHTML">
    <input type="hidden" name="week" value="{{ week.number }}">
    <label>Match #
      <select name="fixture_id"
              hx-get="{{ url_for('outcome_options') }}"
              hx-target="#outcome-box"
              hx-swap="innerHTML"
              hx-trigger="load, change"
              hx-include="closest form">
        {% for f in fixtures %}
          <option value="{{ f.id }}">#{{ f.match_number }}: {{ f.home }} vs {{ f.away }}</option>
        {% endfor %}
      </select>
    </label>
    <label>Outcome</label>
    <div id="outcome-box">
      <select name="outcome" id="outcome-options">
        <option>(pick a match above)</option>
      </select>
    </div>
    <button class="btn" type="submit">Set Result</button>
  </form>
</div>
"""

# -------------------- App + DB --------------------
DB_PATH = os.environ.get("DB_PATH", "sqlite:///pickem.db")
SECRET = os.environ.get("FLASK_SECRET", "devsecret")

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# For possible template inheritance later
app.jinja_loader = DictLoader({'base.html': BASE_HTML})

engine = create_engine(DB_PATH, connect_args={"check_same_thread": False})
SessionLocal = scoped_session(sessionmaker(bind=engine))
Base = declarative_base()

# --- SQLite performance pragmas (better concurrency) ---
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    try:
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.close()
    except Exception:
        pass

# --- Ensure sessions are cleaned up every request (prevents locks) ---
@app.teardown_appcontext
def remove_session(exception=None):
    SessionLocal.remove()

# -------------------- Models --------------------
class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

class Week(Base):
    __tablename__ = "weeks"
    id = Column(Integer, primary_key=True)
    number = Column(Integer, unique=True, nullable=False)
    room_code = Column(String, nullable=False)
    status = Column(String, default="drafting") # drafting | provisional | finalized

class Fixture(Base):
    __tablename__ = "fixtures"
    id = Column(Integer, primary_key=True)
    week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False)
    match_number = Column(Integer, nullable=False)
    home = Column(String, nullable=False)
    away = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint("week_id", "match_number", name="uix_week_matchnumber"),)

class Matchup(Base):
    __tablename__ = "matchups"
    id = Column(Integer, primary_key=True)
    week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False)
    player_a_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    player_b_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    first_picker_id = Column(Integer, ForeignKey("players.id"), nullable=False)

    player_a = relationship("Player", foreign_keys=[player_a_id])
    player_b = relationship("Player", foreign_keys=[player_b_id])
    first_picker = relationship("Player", foreign_keys=[first_picker_id])

class Pick(Base):
    __tablename__ = "picks"
    id = Column(Integer, primary_key=True)
    matchup_id = Column(Integer, ForeignKey("matchups.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    fixture_id = Column(Integer, ForeignKey("fixtures.id"), nullable=False)
    team = Column(String, nullable=False)  # team name selected
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("matchup_id", "fixture_id", name="uix_matchup_fixture_once"),)
    player = relationship("Player")
    fixture = relationship("Fixture")
    matchup = relationship("Matchup")

class Result(Base):
    __tablename__ = "results"
    id = Column(Integer, primary_key=True)
    fixture_id = Column(Integer, ForeignKey("fixtures.id"), nullable=False, unique=True)
    outcome = Column(String, nullable=False)  # Home|Away|Draw
    fixture = relationship("Fixture")

Base.metadata.create_all(engine)

# -------------------- Helpers --------------------
def current_player(db):
    name = session.get("player_name")
    if not name:
        return None
    return db.query(Player).filter_by(name=name).first()

def matchup_order(m: Matchup) -> Tuple[int, int]:
    first = m.first_picker_id
    second = m.player_b_id if first == m.player_a_id else m.player_a_id
    return first, second

def compute_next_turn(db, m: Matchup) -> int:
    picks = db.query(Pick).filter_by(matchup_id=m.id).order_by(Pick.created_at.asc(), Pick.id.asc()).all()
    count = len(picks)
    first, second = matchup_order(m)
    chunk = count // 2
    order = [first, second] if chunk % 2 == 0 else [second, first]
    return order[count % 2]

def available_fixtures_for_matchup(db, m: Matchup) -> list:
    picked_fixture_ids = [p.fixture_id for p in db.query(Pick.fixture_id).filter_by(matchup_id=m.id).all()]
    fixtures = db.query(Fixture).filter_by(week_id=m.week_id).order_by(Fixture.match_number.asc()).all()
    return [f for f in fixtures if f.id not in picked_fixture_ids]

def weekly_points_map(db, week: Week) -> Dict[int, int]:
    players = {p.id: 0 for p in db.query(Player).all()}
    results = {r.fixture_id: r.outcome for r in db.query(Result).join(Fixture).filter(Fixture.week_id==week.id)}
    for p in db.query(Pick).join(Matchup).filter(Matchup.week_id==week.id):
        fx = p.fixture
        outcome = results.get(fx.id)
        if outcome is None:
            continue
        if outcome == "Draw":
            delta = 0
        elif outcome == "Home":
            delta = 1 if p.team == fx.home else -1
        else:
            delta = 1 if p.team == fx.away else -1
        players[p.player_id] = players.get(p.player_id, 0) + delta
    return players

def weekly_for_against(db, week: Week) -> Dict[int, Dict[str,int]]:
    points = weekly_points_map(db, week)
    out: Dict[int, Dict[str,int]] = {pid: {'for': 0, 'against': 0} for pid in points.keys()}
    for m in db.query(Matchup).filter_by(week_id=week.id).all():
        pa = points.get(m.player_a_id, 0); pb = points.get(m.player_b_id, 0)
        out[m.player_a_id]['for'] += pa; out[m.player_a_id]['against'] += pb
        out[m.player_b_id]['for'] += pb; out[m.player_b_id]['against'] += pa
    return out

def season_totals_finalized(db) -> Dict[int, Dict[str,int]]:
    totals: Dict[int, Dict[str,int]] = {}
    for wk in db.query(Week).order_by(Week.number.asc()).all():
        if wk.status != "finalized":
            continue
        fa = weekly_for_against(db, wk)
        for pid, vals in fa.items():
            if pid not in totals:
                totals[pid] = {'for': 0, 'against': 0, 'net': 0}
            totals[pid]['for'] += vals['for']
            totals[pid]['against'] += vals['against']
    for pid, vals in totals.items():
        vals['net'] = vals['for'] - vals['against']
    return totals

def count_results_for_week(db, wk: Week) -> Tuple[int,int]:
    total = db.query(Fixture).filter_by(week_id=wk.id).count()
    done = db.query(Result).join(Fixture).filter(Fixture.week_id==wk.id).count()
    return done, total

def update_week_status(db, wk: Week) -> None:
    done, total = count_results_for_week(db, wk)
    if done == 0:
        wk.status = "drafting"
    elif done < total:
        wk.status = "provisional"
    else:
        wk.status = "finalized"
    db.add(wk); db.commit()

def current_drafting_week(db) -> Optional[Week]:
    wk = db.query(Week).filter_by(status="drafting").order_by(Week.number.asc()).first()
    if wk: return wk
    wk = db.query(Week).filter_by(status="provisional").order_by(Week.number.asc()).first()
    if wk: return wk
    return db.query(Week).order_by(Week.number.asc()).first()

# -------------------- Tab routes (HTMX content) --------------------
@app.get("/tab/current")
def tab_current():
    db = SessionLocal()
    you = current_player(db)
    # Optionally force a specific week via query param (?force_week=5)
    force = request.args.get("force_week", type=int)
    if force:
        wk = db.query(Week).filter_by(number=force).first()
        if wk is None:
            abort(404, "Week not found")
    else:
        wk = current_drafting_week(db)
    if wk is None:
        return "<div class='card'>No weeks initialized yet.</div>"
    update_week_status(db, wk)
    return render_template_string(CURRENT_PARTIAL, current_week=wk, you=you)

@app.get("/tab/open")
def tab_open():
    db = SessionLocal()
    you = current_player(db)
    rows = []
    for wk in db.query(Week).order_by(Week.number.asc()).all():
        if wk.status == "finalized":
            continue
        done, total = count_results_for_week(db, wk)
        rows.append({"week": wk.number, "status": wk.status, "done": done, "total": total})
    return render_template_string(OPEN_PARTIAL, open_rows=rows, you=you)

@app.get("/tab/season")
def tab_season():
    db = SessionLocal()
    you = current_player(db)
    weeks = db.query(Week).order_by(Week.number.asc()).all()
    players = db.query(Player).order_by(Player.name.asc()).all()
    totals = season_totals_finalized(db)
    season_rows = []
    for p in players:
        season_rows.append({
            "name": p.name,
            "for": totals.get(p.id, {}).get("for", 0),
            "against": totals.get(p.id, {}).get("against", 0),
            "net": totals.get(p.id, {}).get("net", 0),
        })
    weekly_points: Dict[int, Dict[int,int]] = {}
    for wk in weeks:
        weekly_points[wk.number] = weekly_points_map(db, wk)
    return render_template_string(SEASON_PARTIAL, season_rows=season_rows, players=players,
                                  weeks=weeks, weekly_points=weekly_points, you=you)

# -------------------- Page shell --------------------
@app.route("/")
def shell():
    db = SessionLocal()
    you = current_player(db)
    initial = tab_current()
    return render_template_string(BASE_HTML, you=you, active_tab='current', body=initial)

# -------------------- Partials used within tabs --------------------
@app.route("/partials/fixtures/<int:week_number>")
def fixtures_partial(week_number: int):
    db = SessionLocal()
    wk = db.query(Week).filter_by(number=week_number).first()
    fixtures = db.query(Fixture).filter_by(week_id=wk.id).order_by(Fixture.match_number.asc()).all()
    return render_template_string(FIXTURES_PARTIAL, fixtures=fixtures)

@app.route("/partials/matchups/<int:week_number>")
def matchups_partial(week_number: int):
    db = SessionLocal()
    wk = db.query(Week).filter_by(number=week_number).first()
    you = current_player(db)
    matchups = []
    for m in db.query(Matchup).filter_by(week_id=wk.id).all():
        turn_id = compute_next_turn(db, m)
        avail = available_fixtures_for_matchup(db, m)
        avail_view = [{"id": f.id, "match_number": f.match_number, "home": f.home, "away": f.away} for f in avail]
        log = []
        for p in db.query(Pick).filter_by(matchup_id=m.id).order_by(Pick.created_at.asc(), Pick.id.asc()).all():
            log.append({
                "player": p.player.name,
                "match_number": p.fixture.match_number,
                "home": p.fixture.home,
                "away": p.fixture.away,
                "team": p.team,
                "when": p.created_at.strftime("%H:%M:%S")
            })
        matchups.append({
            "id": m.id,
            "a": m.player_a.name,
            "b": m.player_b.name,
            "first": m.first_picker.name,
            "turn_id": turn_id,
            "turn_name": db.query(Player).get(turn_id).name,
            "available": avail_view,
            "log": log
        })
    return render_template_string(MATCHUPS_PARTIAL, matchups=matchups, week=wk, you=you)

@app.route("/partials/scores/<int:week_number>")
def scores_partial(week_number: int):
    db = SessionLocal()
    wk = db.query(Week).filter_by(number=week_number).first()
    update_week_status(db, wk)
    points = weekly_points_map(db, wk)
    scores = [{"name": pl.name, "points": points.get(pl.id, 0)} for pl in db.query(Player).all()]
    payouts = payouts_for_week(db, wk)
    fixtures = db.query(Fixture).filter_by(week_id=wk.id).order_by(Fixture.match_number.asc()).all()
    return render_template_string(SCORES_PARTIAL, week=wk, scores=scores, payouts=payouts, fixtures=fixtures)

def payouts_for_week(db, week):
    points = weekly_points_map(db, week)
    rows = []
    for m in db.query(Matchup).filter_by(week_id=week.id).all():
        pa = points.get(m.player_a_id, 0); pb = points.get(m.player_b_id, 0)
        diff = pa - pb
        if diff > 0:
            rows.append({"from": m.player_b.name, "to": m.player_a.name, "points": diff, "payout": diff*5})
        elif diff < 0:
            rows.append({"from": m.player_a.name, "to": m.player_b.name, "points": -diff, "payout": -diff*5})
        else:
            rows.append({"from": "-", "to": "-", "points": 0, "payout": 0})
    return rows

# ---------- Outcome options for team-name results (returns full <select>) ----------
@app.get("/partials/outcome-options")
def outcome_options():
    """Return a full <select> for the outcome based on selected fixture."""
    db = SessionLocal()
    fx_id = request.args.get("fixture_id", type=int) or request.form.get("fixture_id", type=int)
    if not fx_id:
        return '<select name="outcome" id="outcome-options"><option>Draw</option></select>'
    fx = db.query(Fixture).get(int(fx_id))
    if not fx:
        return '<select name="outcome" id="outcome-options"><option>Draw</option></select>'
    return (
        f'<select name="outcome" id="outcome-options">'
        f'<option>{fx.home}</option>'
        f'<option>{fx.away}</option>'
        f'<option>Draw</option>'
        f'</select>'
    )

# -------------------- Actions --------------------
@app.post("/pick")
def make_pick():
    db = SessionLocal()
    me = current_player(db)
    if me is None:
        abort(403, "Not logged in")
    wk_number = int(request.form["week"])
    wk = db.query(Week).filter_by(number=wk_number).first()
    m = db.query(Matchup).get(int(request.form["matchup_id"]))
    fx_id = int(request.form["fixture_id"])
    team_name = request.form["team"].strip()

    if m.week_id != wk.id:
        abort(400, "Bad matchup/week")

    turn_id = compute_next_turn(db, m)
    if me.id != turn_id:
        abort(400, "Not your turn in this matchup")

    # Ensure the fixture is still available in this matchup
    avail_ids = [f.id for f in available_fixtures_for_matchup(db, m)]
    if fx_id not in avail_ids:
        abort(400, "Fixture already taken or not in this week")

    fx = db.query(Fixture).get(fx_id)
    if team_name not in (fx.home, fx.away):
        abort(400, "Team must be one of the fixture teams")

    try:
        p = Pick(matchup_id=m.id, player_id=me.id, fixture_id=fx.id, team=team_name)
        db.add(p); db.commit()
    except Exception as e:
        db.rollback()
        abort(400, f"Pick failed: {e}")

    # Re-render the matchups panel after pick
    return matchups_partial(wk.number)

@app.post("/set_result")
def set_result():
    db = SessionLocal()
    wk_number = int(request.form["week"])
    wk = db.query(Week).filter_by(number=wk_number).first()
    fx_id = int(request.form["fixture_id"])
    raw = request.form["outcome"].strip()

    fx = db.query(Fixture).get(fx_id)
    if fx.week_id != wk.id:
        abort(400, "Fixture not in this week")

    # Map team name / Draw to canonical outcome
    if raw.lower() == "draw":
        outcome = "Draw"
    elif raw == fx.home:
        outcome = "Home"
    elif raw == fx.away:
        outcome = "Away"
    else:
        abort(400, "Outcome must be one of the fixture's team names or Draw")

    existing = db.query(Result).filter_by(fixture_id=fx.id).first()
    if existing:
        existing.outcome = outcome
    else:
        db.add(Result(fixture_id=fx.id, outcome=outcome))
    db.commit()
    # Auto-finalization update
    update_week_status(db, wk)
    return scores_partial(wk.number)

# -------------------- Join flow --------------------
@app.route("/join", methods=["GET", "POST"])
def join():
    db = SessionLocal()
    # Determine a sensible week to join against
    wk = current_drafting_week(db)
    allowed_names = [p.name for p in db.query(Player).all()]
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        code = request.form.get("room_code", "").strip()
        if wk is None:
            abort(400, "Week not initialized yet.")
        if code != wk.room_code:
            abort(403, "Wrong room code.")
        if name not in allowed_names:
            abort(403, "Name not in allowed players.")
        session["player_name"] = name
        return redirect(url_for("shell"))
    return render_template_string(JOIN_HTML, allowed_names=allowed_names)

# -------------------- Initialization helpers --------------------
def parse_weeks_arg(weeks_arg: str, df: pd.DataFrame) -> List[int]:
    if weeks_arg.strip().lower() in ("all", "any"):
        return sorted(int(x) for x in df["Round Number"].unique().tolist())
    parts = [p.strip() for p in weeks_arg.split(",")]
    out = set()
    for p in parts:
        if "-" in p:
            a, b = p.split("-", 1)
            a, b = int(a), int(b)
            for n in range(min(a,b), max(a,b)+1):
                out.add(n)
        else:
            out.add(int(p))
    return sorted(out)

def init_weeks_from_csv(csv_path: str, weeks: Iterable[int], players: List[str], room_code: str):
    db = SessionLocal()
    df = pd.read_csv(csv_path)

    # Reset players to the provided 6
    db.query(Player).delete()
    for name in players:
        db.add(Player(name=name))
    db.commit()

    for week_number in weeks:
        wk = db.query(Week).filter_by(number=week_number).first()
        if wk:
            # wipe week data
            fxs = db.query(Fixture).filter_by(week_id=wk.id).all()
            fx_ids = [f.id for f in fxs]
            if fx_ids:
                db.query(Result).filter(Result.fixture_id.in_(fx_ids)).delete(synchronize_session=False)
            mus = db.query(Matchup).filter_by(week_id=wk.id).all()
            mu_ids = [m.id for m in mus]
            if mu_ids:
                db.query(Pick).filter(Pick.matchup_id.in_(mu_ids)).delete(synchronize_session=False)
                db.query(Matchup).filter_by(week_id=wk.id).delete()
            db.query(Fixture).filter_by(week_id=wk.id).delete()
            wk.room_code = room_code
            wk.status = "drafting"
        else:
            wk = Week(number=week_number, room_code=room_code, status="drafting")
            db.add(wk)
            db.flush()

        wkdf = df[df["Round Number"] == week_number]
        for _, r in wkdf.iterrows():
            db.add(Fixture(week_id=wk.id,
                           match_number=int(r["Match Number"]),
                           home=str(r["Home Team"]),
                           away=str(r["Away Team"])))
        db.commit()

        # create 3 matchups for the week
        pls = db.query(Player).order_by(Player.name.asc()).all()
        names = [p.name for p in pls]
        random.shuffle(names)
        pairs = [(names[i], names[i+1]) for i in range(0, len(names), 2)]
        for a_name, b_name in pairs:
            a = db.query(Player).filter_by(name=a_name).first()
            b = db.query(Player).filter_by(name=b_name).first()
            first = random.choice([a, b])
            db.add(Matchup(week_id=wk.id, player_a_id=a.id, player_b_id=b.id, first_picker_id=first.id))
        db.commit()

        # ensure initial status is set correctly
        update_week_status(db, wk)

# -------------------- CLI --------------------
def main():
    parser = argparse.ArgumentParser(description="Pick 'Em Flask + HTMX (tabs, multi-week, team-name picks)")
    parser.add_argument("--csv", required=True, help="Path to fixtures CSV")
    parser.add_argument("--weeks", required=True, help="Weeks to init: '1', '1-4', '1,3,8-10', or 'all'")
    parser.add_argument("--players", required=True, help="Comma-separated 6 player names")
    parser.add_argument("--room", required=True, help="Room code (shared password)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    players = [p.strip() for p in args.players.split(",") if p.strip()]
    if len(players) != 6:
        print("Please supply exactly 6 players.")
        return

    # --- Only initialize when requested (so we don't wipe DB on every restart) ---
    do_init = os.environ.get("INIT_ON_START", "0") == "1"
    if do_init:
        df = pd.read_csv(args.csv)
        weeks = parse_weeks_arg(args.weeks, df)
        if not weeks:
            print("No weeks selected to initialize.")
            return
        init_weeks_from_csv(args.csv, weeks, players, args.room)

    app.jinja_env.globals.update(zip=zip)

    # Stable run (no reloader); enable threading for concurrency
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)

if __name__ == "__main__":
    main()
