"""Scoped adapter over the real draft engine; no provider/background dispatch."""
from datetime import datetime, timezone
from flask import abort, jsonify, request
from sqlalchemy import text
from auto_draft import DraftService, DraftError
from runtime_matchweek import load_matchweek


class LeagueDraftService(DraftService):
    def __init__(self, domain, authorize_write, league_id, season_id, week_number, club_id):
        super().__init__(domain)
        self.authorize_write=authorize_write
        self.league_id,self.season_id,self.week_number=league_id,season_id,week_number
        self.club_id=club_id

    def authorize(self, db, matchup, player_id):
        user=self.authorize_write(db)  # Identity, CSRF and revocation under BEGIN IMMEDIATE.
        context=load_matchweek(db,user,self.league_id,self.season_id,self.week_number)
        if matchup is None or matchup.id not in {m['id'] for m in context['matchups']}:abort(404)
        player_id=context['viewer']['membership_id']
        if player_id not in (matchup.player_a_id,matchup.player_b_id):abort(403)
        league=db.execute(text('SELECT status FROM leagues WHERE id=:id'),dict(id=self.league_id)).scalar_one()
        if league!='active' or matchup.week_id!=context['week']['id']:abort(409)
        season=db.get(self.d.Season,self.season_id)
        if not season.is_active or season.is_archived or context['week']['status']!='drafting':abort(409)
        # Both participants must still be current active members of this season.
        count=db.execute(text("""SELECT COUNT(*) FROM league_season_memberships r
            JOIN players p ON p.id=r.membership_id WHERE r.season_id=:season
            AND r.membership_id IN (:a,:b) AND r.left_at IS NULL AND p.left_at IS NULL
            AND p.status='active' AND p.league_id=:league"""),
            dict(season=self.season_id,a=matchup.player_a_id,b=matchup.player_b_id,league=self.league_id)).scalar_one()
        if count!=2:abort(409)
        self.slate={f['id']:f for f in context['fixtures']}
        return player_id

    def fixture(self, db, matchup, fixture_id):
        row=self.slate.get(fixture_id)
        if row is None:return None
        fx=db.get(self.d.Fixture,fixture_id)
        # A missing/unknown kickoff cannot establish pre-kickoff eligibility.
        kickoff=fx.kickoff_utc
        if kickoff is None:raise DraftError('Fixture kickoff is unverified',409)
        kickoff=kickoff.replace(tzinfo=timezone.utc) if kickoff.tzinfo is None else kickoff.astimezone(timezone.utc)
        if kickoff<=datetime.now(timezone.utc) or row['result'] is not None:
            raise DraftError('Fixture is locked',409)
        return fx

    def _pick(self, db, matchup, player_id, fixture_id, team, expected_count=None):
        if expected_count is not None:  # Explicit manual selection, not a persisted queue.
            fx=self.fixture(db,matchup,fixture_id)
            if fx is None:raise DraftError('Fixture not in selected league week',404)
            row=self.slate[fixture_id]
            club=row['home_club_id'] if team==fx.home else row['away_club_id'] if team==fx.away else None
            if club!=self.club_id:raise DraftError('Team/club selection is inconsistent',400)
        return super()._pick(db,matchup,player_id,fixture_id,team,expected_count=expected_count)

    def after_pick(self, db, pick, fixture):
        row=self.slate[fixture.id]
        club=row['home_club_id'] if pick.team==fixture.home else row['away_club_id']
        db.execute(text('UPDATE picks SET selected_club_id=:club WHERE id=:id'),dict(club=club,id=pick.id))


def install_routes(bp, *, domain, authorize_write):
    @bp.post('/leagues/<int:league_id>/seasons/<int:season_id>/matchweeks/<int:week_number>/matchups/<int:matchup_id>/picks')
    def submit_pick(league_id,season_id,week_number,matchup_id):
        if domain is None:abort(503)
        if not all(0<v<2**63 for v in (league_id,season_id,week_number,matchup_id)):abort(404)
        fields={'fixture_id','team','selected_club_id','expected_count','csrf'}
        if (request.args or request.mimetype!='application/x-www-form-urlencoded' or
            request.content_length is None or request.content_length>4096 or
            set(request.form)!=fields or any(len(request.form.getlist(k))!=1 for k in fields)):abort(400)
        try:
            fixture_id=int(request.form['fixture_id']);club_id=int(request.form['selected_club_id'])
            expected=int(request.form['expected_count'])
        except ValueError:abort(400)
        if not 0<fixture_id<2**63 or not 0<club_id<2**63 or not 0<=expected<10:abort(400)
        team=request.form['team']
        service=LeagueDraftService(domain,authorize_write,league_id,season_id,week_number,club_id)
        try:
            summary=service.command(matchup_id,None,manual=(fixture_id,team),expected_count=expected)
        except DraftError as exc:abort(exc.status,str(exc))
        return jsonify(submitted=True,matchup_id=matchup_id,auto_draft=summary),201
