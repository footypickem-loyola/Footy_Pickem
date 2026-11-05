"""Scoped adapter over the real draft engine; no provider/background dispatch."""
from flask import abort, jsonify, redirect, request, url_for
from werkzeug.exceptions import HTTPException
from sqlalchemy import text
from auto_draft import DraftService, DraftError
from runtime_matchweek import load_matchweek
from runtime_draft_state import draft_lock_reason, fixture_lock_reason


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
        selected=next(m for m in context['matchups'] if m['id']==matchup.id)
        if matchup.week_id!=context['week']['id'] or draft_lock_reason(db,context,selected):abort(409)
        self.slate={f['id']:f for f in context['fixtures']}
        return player_id

    def fixture(self, db, matchup, fixture_id):
        row=self.slate.get(fixture_id)
        if row is None:return None
        reason=fixture_lock_reason(row)
        if reason:raise DraftError(reason,409)
        return db.get(self.d.Fixture,fixture_id)

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
        html=request.accept_mimetypes.best=='text/html'
        view=dict(league_id=league_id,season_id=season_id,week_number=week_number)
        try:
            summary=service.command(matchup_id,None,manual=(fixture_id,team),expected_count=expected)
        except (DraftError,HTTPException) as exc:
            status=exc.status if isinstance(exc,DraftError) else exc.code
            if html and status in (400,409):
                return redirect(url_for('runtime_auth.matchweek_view',**view,notice='changed'),303)
            if isinstance(exc,HTTPException):raise
            abort(status,str(exc))
        if html:return redirect(url_for('runtime_auth.matchweek_view',**view,notice='saved'),303)
        return jsonify(submitted=True,matchup_id=matchup_id,auto_draft=summary),201
