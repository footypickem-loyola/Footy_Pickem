"""Durable finalization tasks. No provider access while enqueueing."""
from datetime import datetime, timedelta
import uuid

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, or_, exists

from pick_insight_enrichment import supported_season

LEASE = timedelta(minutes=10)


def register_models(Base):
    class PickInsightRefresh(Base):
        __tablename__ = 'pick_insight_refreshes'
        week_id = Column(Integer, ForeignKey('weeks.id'), primary_key=True)
        season_id = Column(Integer, ForeignKey('seasons.id'), nullable=False, index=True)
        status = Column(String, nullable=False, default='pending')
        created_at = Column(DateTime, nullable=False)
        completed_at = Column(DateTime)
        claim_token = Column(String)
        lease_until = Column(DateTime)
    return PickInsightRefresh


def enqueue(db, models, week, now):
    """Called only on transition, in the same transaction as finalization."""
    if supported_season(week.season) and week.season.is_active and not week.season.is_archived:
        if db.get(models.PickInsightRefresh, week.id) is None:
            db.add(models.PickInsightRefresh(week_id=week.id, season_id=week.season_id,
                                            status='pending', created_at=now))


class TaskBusy(Exception):
    pass


class TaskUnavailable(Exception):
    pass


def run_task(db, models, client, week_id=None, clock=datetime.utcnow):
    """Claim briefly, fetch outside the write transaction, then fence/commit.

    At most one unexpired claim per season; expired workers cannot publish.
    Cache replacement and task completion share sync_season's atomic commit.
    """
    from sportmonks_season import sync_season
    Task = models.PickInsightRefresh
    season = models.active_season(db)
    if not supported_season(season) or season.is_archived:
        raise TaskUnavailable()
    season_id = season.id
    query = db.query(Task).filter_by(season_id=season_id)
    if week_id is not None:
        query = query.filter_by(week_id=week_id)
    else:
        query = query.filter(Task.status != 'completed')
    task = query.order_by(Task.created_at, Task.week_id).first()
    if task is None and week_id is not None:
        # Explicit admin recovery for a missed transition (e.g. before rollout).
        week = db.get(models.Week, week_id)
        if week is None or week.season_id != season_id or week.status != 'finalized':
            raise TaskUnavailable()
        done, total = models.count_results_for_week(db, week)
        if not total or done != total:
            raise TaskUnavailable()
        from sqlalchemy.dialects.sqlite import insert
        db.execute(insert(Task).values(week_id=week_id, season_id=season_id,
                   status='pending', created_at=clock()).on_conflict_do_nothing(index_elements=['week_id']))
        db.commit()
        task = db.get(Task, week_id)
    if task is None:
        return dict(status='no_work', clubs=0, clubs_with_scorers=0)
    if task.status == 'completed':
        return dict(status='already_completed', clubs=0, clubs_with_scorers=0)
    week = db.get(models.Week, task.week_id)
    done, total = models.count_results_for_week(db, week)
    if week.status != 'finalized' or not total or done != total:
        raise TaskUnavailable()
    target = task.week_id
    token, now = uuid.uuid4().hex, clock()
    db.rollback()  # Release read transaction before atomic claim.
    busy = exists().where(Task.season_id == season_id, Task.status == 'running', Task.lease_until > now)
    claimed = db.query(Task).filter(Task.week_id == target, Task.status != 'completed',
        or_(Task.lease_until == None, Task.lease_until <= now), ~busy).update(
            {Task.status: 'running', Task.claim_token: token, Task.lease_until: now + LEASE},
            synchronize_session=False)
    db.commit()
    if not claimed:
        raise TaskBusy()

    def complete(session):
        updated = session.query(Task).filter_by(week_id=target, status='running', claim_token=token).filter(
            Task.lease_until > clock()).update(
                {Task.status: 'completed', Task.completed_at: clock(),
                 Task.claim_token: None, Task.lease_until: None}, synchronize_session=False)
        if not updated:
            raise TaskBusy()

    try:
        # A task's completion marker and its cache snapshot cannot diverge.
        season = db.get(models.Season, season_id)
        summary = sync_season(db, models, season, client, before_commit=complete)
        return dict(status='completed', **summary)
    except Exception:
        db.rollback()
        db.query(Task).filter_by(week_id=target, claim_token=token).update(
            {Task.status: 'pending', Task.claim_token: None, Task.lease_until: None},
            synchronize_session=False)
        db.commit()
        raise
