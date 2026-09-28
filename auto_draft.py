"""Serialized SQLite draft commands. No request or presentation dependencies.

Every command uses a fresh session and BEGIN IMMEDIATE before reading state.
SQLite's bounded busy timeout is the only wait; commands are never blindly
replayed after an uncertain commit. The existing fixture uniqueness constraint
is a second line of defense. GETs never call this service.
"""
import json

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError, OperationalError


class DraftError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class DraftService:
    def __init__(self, domain):
        self.d = domain

    def command(self, matchup_id, player_id, *, manual=None, edit=None,
                week_id=None, expected_count=None):
        d = self.d
        with sessionmaker(bind=d.engine)() as db:
            try:
                db.execute(text("BEGIN IMMEDIATE"))
                m = db.get(d.Matchup, matchup_id)
                if m is None:
                    raise DraftError("Matchup not found", 404)
                if player_id not in (m.player_a_id, m.player_b_id):
                    raise DraftError("Not a participant", 403)
                week = db.get(d.Week, m.week_id)
                if week_id is not None and week.id != week_id:
                    raise DraftError("Bad matchup/week")
                if week.season.is_archived or week.status == "finalized":
                    raise DraftError("This draft is read only", 403)
                if db.query(d.Pick).filter_by(matchup_id=m.id).count() >= 10:
                    raise DraftError("Draft is complete", 409)
                if edit is not None:
                    self._edit(db, m, player_id, edit)
                if manual is not None:
                    self._pick(db, m, player_id, *manual, expected_count=expected_count)
                summary = self._run(db, m)
                db.commit()
                return summary
            except (IntegrityError, OperationalError) as exc:
                db.rollback()
                raise DraftError("Draft busy or changed. Refresh and try again.", 409) from exc
            except Exception:
                db.rollback()
                raise

    def _pick(self, db, m, player_id, fixture_id, team, expected_count=None):
        d = self.d
        count = db.query(d.Pick).filter_by(matchup_id=m.id).count()
        if count >= 10 or (expected_count is not None and count != expected_count):
            raise DraftError("Draft changed. Refresh before picking.", 409)
        if d.compute_next_turn(db, m) != player_id:
            raise DraftError("Not your turn in this matchup")
        fx = db.get(d.Fixture, fixture_id)
        if not fx or fx.week_id != m.week_id or db.query(d.Pick).filter_by(
                matchup_id=m.id, fixture_id=fixture_id).first():
            raise DraftError("Fixture already taken or not in this week")
        if team not in (fx.home, fx.away):
            raise DraftError("Team must be one of the fixture teams")
        db.add(d.Pick(matchup_id=m.id, player_id=player_id, fixture_id=fx.id, team=team))
        db.flush()

    def _run(self, db, m):
        d = self.d
        picks = []
        # At most ten picks, even when both players have enabled queues.
        while db.query(d.Pick).filter_by(matchup_id=m.id).count() < 10:
            player_id = d.compute_next_turn(db, m)
            setting = db.query(d.AutoDraftSetting).filter_by(
                matchup_id=m.id, player_id=player_id).first()
            if not setting or not setting.enabled:
                break
            rows = self.preferences(db, m.id, player_id)
            preference = next((p for p in rows if self.unavailable(db, m, p) is None), None)
            if preference is None:
                break
            self._pick(db, m, player_id, preference.fixture_id, preference.team)
            picks.append({"player_id": player_id, "team": preference.team})
        return {"picks": picks, "count": len(picks)}

    def preferences(self, db, matchup_id, player_id):
        d = self.d
        return db.query(d.AutoDraftPreference).filter_by(
            matchup_id=matchup_id, player_id=player_id).order_by(
                d.AutoDraftPreference.priority, d.AutoDraftPreference.id).all()

    def unavailable(self, db, m, preference):
        d = self.d
        fx = db.get(d.Fixture, preference.fixture_id)
        if not fx or fx.week_id != m.week_id:
            return "Fixture no longer in this matchup"
        if preference.team not in (fx.home, fx.away):
            return "Team no longer in this fixture"
        if db.query(d.Pick).filter_by(matchup_id=m.id, fixture_id=fx.id).first():
            pick = db.query(d.Pick).filter_by(matchup_id=m.id, fixture_id=fx.id).first()
            return f"{preference.team} Picked" if pick.player_id == preference.player_id else "Opponent Picked"
        return None

    def _edit(self, db, m, player_id, edit):
        d = self.d
        action = edit.get("action")
        scope = dict(matchup_id=m.id, player_id=player_id)
        rows = self.preferences(db, m.id, player_id)
        setting = db.query(d.AutoDraftSetting).filter_by(**scope).first()
        if setting and setting.confirmed_at and action != "toggle":
            raise DraftError("Your bulk picks are confirmed and cannot be edited.", 409)
        if action == "confirm":
            self._confirm(db, m, scope, rows, setting, edit)
            return
        if action == "toggle":
            if edit.get("enabled") not in ("0", "1"):
                raise DraftError("Invalid enabled state")
            if setting is None:
                setting = d.AutoDraftSetting(**scope)
                db.add(setting)
            setting.enabled = int(edit["enabled"])
        elif action == "add":
            try:
                fixture_id, team = edit.get("choice", "").split(":", 1)
                fixture_id = int(fixture_id)
            except ValueError:
                raise DraftError("Choose a fixture and team")
            candidate = d.AutoDraftPreference(**scope, fixture_id=fixture_id,
                                              team=team, priority=len(rows))
            if self.unavailable(db, m, candidate):
                raise DraftError("Preference is unavailable or invalid")
            if any(p.fixture_id == fixture_id for p in rows):
                raise DraftError("Fixture is already in your priorities")
            db.add(candidate)
        elif action in ("remove", "up", "down"):
            row = next((p for p in rows if str(p.id) == edit.get("preference_id")), None)
            if row is None:
                raise DraftError("Preference not found in your queue", 403)
            index = rows.index(row)
            if action == "remove":
                rows.remove(row)
                db.delete(row)
            else:
                target = index + (-1 if action == "up" else 1)
                if 0 <= target < len(rows):
                    rows[index], rows[target] = rows[target], rows[index]
            for priority, item in enumerate(rows):
                item.priority = priority
        else:
            raise DraftError("Unknown Auto-Draft action")
        db.flush()

    def _confirm(self, db, m, scope, rows, setting, edit):
        """Atomically validate, replace and lock a complete ordered bulk list."""
        d = self.d
        try:
            expected = int(edit.get("expected_count", ""))
            choices = json.loads(edit.get("preferences", ""))
        except (ValueError, TypeError):
            raise DraftError("Invalid bulk picks. Refresh and try again.")
        count = db.query(d.Pick).filter_by(matchup_id=m.id).count()
        if expected != count:
            raise DraftError("The draft changed while you were choosing. Close this window and refresh before confirming.", 409)
        available = {f.id: f for f in d.available_fixtures_for_matchup(db, m)}
        if not isinstance(choices, list) or len(choices) != len(available) or not 1 <= len(choices) <= 10:
            raise DraftError("Rank every remaining fixture and choose a team for each one.")
        seen = set()
        for choice in choices:
            if not isinstance(choice, dict) or type(choice.get("fixture_id")) is not int:
                raise DraftError("Invalid fixture in bulk picks.")
            fixture_id = choice["fixture_id"]
            fixture = available.get(fixture_id)
            if fixture is None or fixture_id in seen:
                raise DraftError("Each remaining fixture must appear exactly once.")
            if choice.get("team") not in (fixture.home, fixture.away):
                raise DraftError("Choose one of the two teams for each fixture. Draw is not a selection.")
            seen.add(fixture_id)
        for row in rows:
            db.delete(row)
        db.flush()
        for priority, choice in enumerate(choices):
            db.add(d.AutoDraftPreference(**scope, fixture_id=choice["fixture_id"],
                team=choice["team"], priority=priority))
        if setting is None:
            setting = d.AutoDraftSetting(**scope)
            db.add(setting)
        setting.enabled = 1
        setting.confirmed_at = d.utcnow()
        db.flush()
