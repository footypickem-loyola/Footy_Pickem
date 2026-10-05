"""Sportmonks reference snapshots, isolated from authoritative Pick 'Em state."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from football_reference_schema import TABLES, create_schema
from sportmonks_season import NoRedirect
from sportmonks_live import EVENT_TYPES, STATES

PROVIDER = "sportmonks"
SEASONS = {23614: "2024/2025", 25583: "2025/2026", 28083: "2026/2027"}
EVENT_TYPES = {**EVENT_TYPES, 17: "missed_penalty"}
FINAL_STATES = {5, 7, 8}
GOAL_TYPES = {14, 15, 16}


class ReferenceError(ValueError):
    """Only static, credential-free messages may cross this boundary."""
    def __init__(self, message, retry_after=60, code="validation_failed"):
        super().__init__(message)
        self.retry_after = max(60, min(retry_after, 3600))
        self.code = code


def season_id_checked(season_id):
    if type(season_id) is not int or season_id not in SEASONS:
        raise ReferenceError("Unsupported reference season")
    return season_id


class ReferenceClient:
    def __init__(self, token=None, opener=None):
        self.token = token if token is not None else os.environ.get("SPORTMONKS_API_TOKEN", "")
        self.opener = opener or build_opener(NoRedirect()).open
        self.rate_limit = None

    def request(self, resource, **params):
        if not self.token:
            raise ReferenceError("Sportmonks token is not configured", code="missing_token")
        req = Request("https://api.sportmonks.com/v3/football/" + resource + "?" + urlencode(params),
                      headers={"Authorization": self.token, "Accept": "application/json"})
        try:
            with self.opener(req, timeout=15) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError()
            payload = json.loads(raw)
            if not isinstance(payload, dict) or "data" not in payload:
                raise ValueError()
            self.rate_limit = payload.get("rate_limit")
            return payload
        except HTTPError as exc:
            retry = exc.headers.get("Retry-After", "60") if exc.headers else "60"
            raise ReferenceError(f"Sportmonks reference HTTP {exc.code}", int(retry) if retry.isdigit() else 60,
                                 code="rate_limited" if exc.code == 429 else "provider_http_error") from None
        except Exception:
            raise ReferenceError("Invalid or unavailable Sportmonks reference response", code="provider_unavailable") from None

    def season(self, season_id):
        return self.request(f"seasons/{season_id_checked(season_id)}")["data"]

    def fixtures(self, season_id):
        season_id_checked(season_id)
        rows, seen = [], set()
        for page in range(1, 101):
            payload = self.request("fixtures", filters=f"fixtureSeasons:{season_id}",
                                   include="participants;scores;events", page=page, per_page=50)
            data, pagination = payload.get("data"), payload.get("pagination")
            if (not isinstance(data, list) or not all(isinstance(r, dict) for r in data)
                    or not isinstance(pagination, dict)
                    or type(pagination.get("has_more")) is not bool
                    or type(pagination.get("current_page")) is not int
                    or pagination["current_page"] != page
                    or (pagination["has_more"] and not data)):
                raise ReferenceError("Invalid reference pagination")
            fingerprint = json.dumps(data, sort_keys=True)
            if fingerprint in seen:
                raise ReferenceError("Repeated reference page")
            seen.add(fingerprint)
            rows.extend(data)
            if len(rows) > 380:
                raise ReferenceError("Too many reference fixtures")
            if not pagination["has_more"]:
                return rows
        raise ReferenceError("Reference pagination limit exceeded")


def integer(value, nullable=False, minimum=1):
    if nullable and value is None:
        return None
    if type(value) is not int or value < minimum:
        raise ValueError()
    return value


def text_value(value, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError()
    return value


def timestamp(value):
    if not isinstance(value, str) or len(value) < 16 or value[10] not in ("T", " "):
        raise ValueError()
    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def flag(value, nullable=False):
    if nullable and value is None:
        return None
    if type(value) not in (bool, int) or value not in (0, 1):
        raise ValueError()
    return int(value)


def validate(season_id, metadata, raw_fixtures):
    """Normalize all data or reject it before any database is opened for writes."""
    season_id_checked(season_id)
    try:
        if metadata["id"] != season_id or metadata["league_id"] != 8:
            raise ValueError()
        season = dict(provider=PROVIDER, external_season_id=season_id, league_id=8,
                      name=text_value(metadata.get("name"), required=True),
                      finished=flag(metadata.get("finished"), nullable=True),
                      is_current=flag(metadata.get("is_current"), nullable=True))
        completed = season_id in (23614, 25583) or season["finished"] == 1
        if not isinstance(raw_fixtures, list) or len(raw_fixtures) != 380:
            raise ValueError()
        fixtures, fixture_ids, event_ids = [], set(), set()
        for raw in raw_fixtures:
            fid = integer(raw["id"])
            if fid in fixture_ids or raw["season_id"] != season_id or raw["league_id"] != 8:
                raise ValueError()
            fixture_ids.add(fid)
            participants = raw["participants"]
            if not isinstance(participants, list) or len(participants) != 2:
                raise ValueError()
            sides = {p["meta"]["location"]: p for p in participants}
            if set(sides) != {"home", "away"}:
                raise ValueError()
            home_id, away_id = (integer(sides[s]["id"]) for s in ("home", "away"))
            if home_id == away_id:
                raise ValueError()
            names = {integer(p["id"]): text_value(p["name"], required=True) for p in participants}
            state_id = integer(raw["state_id"])
            if completed and state_id not in FINAL_STATES:
                raise ValueError()
            scores = raw["scores"]
            if not isinstance(scores, list):
                raise ValueError()
            current = {}
            for score in scores:
                # Even non-CURRENT records must have a usable score shape.
                side = score["score"]["participant"]
                goals = integer(score["score"]["goals"], minimum=0)
                if side not in sides or score.get("participant_id") != sides[side]["id"]:
                    raise ValueError()
                if score.get("description") == "CURRENT":
                    if side in current:
                        raise ValueError()
                    current[side] = goals
            if (current and set(current) != {"home", "away"}) or (state_id in FINAL_STATES and len(current) != 2):
                raise ValueError()
            raw_events = raw["events"]  # Omitted/null is not a complete empty snapshot.
            if not isinstance(raw_events, list):
                raise ValueError()
            events = []
            for event in raw_events:
                eid = integer(event["id"])
                if eid in event_ids or event.get("fixture_id") != fid:
                    raise ValueError()
                event_ids.add(eid)
                type_id = integer(event["type_id"])
                team_id = integer(event.get("participant_id"), nullable=True)
                if team_id is not None and team_id not in names:
                    raise ValueError()
                # Provider documents null for non-card/historical events. Preserve
                # that uncertainty; only explicit true is a rescission.
                rescinded = flag(event.get("rescinded"), nullable=True)
                events.append(dict(provider=PROVIDER, external_event_id=eid, type_id=type_id,
                    event_type=EVENT_TYPES.get(type_id, "unknown"),
                    minute=integer(event.get("minute"), nullable=True, minimum=0),
                    extra_minute=integer(event.get("extra_minute"), nullable=True, minimum=0),
                    team_provider_id=team_id, team_name=names.get(team_id),
                    player_provider_id=integer(event.get("player_id"), nullable=True),
                    player_name=text_value(event.get("player_name")),
                    related_player_provider_id=integer(event.get("related_player_id"), nullable=True),
                    related_player_name=text_value(event.get("related_player_name")),
                    sub_type_id=integer(event.get("sub_type_id"), nullable=True),
                    detail=text_value(event.get("detail")), info=text_value(event.get("info")),
                    addition=text_value(event.get("addition")),
                    running_score=text_value(event.get("result")),
                    sort_order=integer(event.get("sort_order"), nullable=True, minimum=0),
                    rescinded=rescinded, is_active=int(rescinded != 1), is_present=1,
                    provider_updated_at=text_value(event.get("updated_at"))))
            fixtures.append(dict(provider=PROVIDER, external_fixture_id=fid, league_id=8,
                kickoff_utc=timestamp(raw["starting_at"]), home_team_id=home_id, home_team_name=names[home_id],
                away_team_id=away_id, away_team_name=names[away_id],
                home_score=current.get("home"), away_score=current.get("away"), state_id=state_id,
                state=STATES.get(state_id, "unknown"), provider_updated_at=text_value(raw.get("updated_at") or raw.get("last_processed_at")),
                events=sorted(events, key=lambda e: (e["sort_order"] or 0, e["external_event_id"]))))
        return season, sorted(fixtures, key=lambda f: f["external_fixture_id"])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ReferenceError("Reference payload validation failed; prior dataset retained") from None


def counts(fixtures):
    retained = [e for f in fixtures for e in f["events"]]
    events = [e for e in retained if e["is_present"]]
    goals = [e for e in events if e["type_id"] in GOAL_TYPES]
    return dict(fixture_count=len(fixtures),
                final_fixture_count=sum(f["state_id"] in FINAL_STATES for f in fixtures),
                scored_fixture_count=sum(f["home_score"] is not None and f["away_score"] is not None for f in fixtures),
                fixtures_with_events=sum(any(e["is_present"] for e in f["events"]) for f in fixtures),
                total_event_count=len(events), goal_event_count=len(goals),
                goal_events_with_player_identity=sum(bool(e["player_provider_id"] and e["player_name"] and e["player_name"].strip()) for e in goals),
                earliest_kickoff=min((f["kickoff_utc"] for f in fixtures), default=None),
                latest_kickoff=max((f["kickoff_utc"] for f in fixtures), default=None),
                event_type_distribution=dict(sorted(Counter(str(e["type_id"]) for e in events).items())),
                inactive_event_count=sum(not e["is_active"] for e in retained),
                active_event_count=sum(e["is_active"] for e in events),
                rescinded_event_count=sum(e["rescinded"] == 1 for e in events),
                retained_event_count=len(retained), withdrawn_event_count=len(retained)-len(events))


def database_path(value):
    if not value:
        raise ReferenceError("An explicit existing SQLite database path is required")
    try:
        path = Path(value).resolve(strict=True)
        if not path.is_file():
            raise ValueError()
        return path
    except (OSError, ValueError):
        raise ReferenceError("An explicit existing SQLite database path is required") from None


def write_guard(action, table, column, database, trigger):
    if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
        if table not in TABLES:
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def upsert(db, table, values, keys):
    # All identifiers originate from this module, never from provider keys.
    columns = list(values)
    updates = [c for c in columns if c not in keys and c != "created_at"]
    db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
               f"ON CONFLICT ({','.join(keys)}) DO UPDATE SET " + ','.join(f"{c}=excluded.{c}" for c in updates),
               [values[c] for c in columns])
    return db.execute(f"SELECT id FROM {table} WHERE " + ' AND '.join(f"{k}=?" for k in keys),
                      [values[k] for k in keys]).fetchone()[0]


def sync_reference(*, database=None, season_id, client, dry_run=False, before_write=None, before_commit=None):
    """Optional transaction hooks fence task ownership; all fetching stays outside."""
    season_id_checked(season_id)
    path = database_path(database) if not dry_run else None
    started = datetime.now(timezone.utc).isoformat()
    season, fixtures = validate(season_id, client.season(season_id), client.fixtures(season_id))
    report = counts(fixtures)
    if dry_run:
        return dict(season_id=season_id, dry_run=True, **report)
    now = datetime.now(timezone.utc).isoformat()
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                create_schema(db)
                db.set_authorizer(write_guard)
                if before_write is not None:
                    before_write(db)
                sid = upsert(db, "football_reference_seasons", dict(season, sync_status="completed",
                             fixture_count=len(fixtures), last_successful_sync_at=now), ("provider", "external_season_id"))
                prior_ids = {r[0] for r in db.execute("SELECT external_fixture_id FROM football_reference_fixtures WHERE reference_season_id=?", (sid,))}
                # Avoid silently deleting history when provider fixture identities change.
                if prior_ids and prior_ids != {f["external_fixture_id"] for f in fixtures}:
                    raise ReferenceError("Reference fixture identity set changed; manual investigation required")
                for fixture in fixtures:
                    values = {k: v for k, v in fixture.items() if k != "events"}
                    old = db.execute("SELECT reference_season_id,state_id FROM football_reference_fixtures WHERE provider=? AND external_fixture_id=?",
                                     (PROVIDER, fixture["external_fixture_id"])).fetchone()
                    if old and old[0] != sid:
                        raise ReferenceError("Reference fixture belongs to another season")
                    if old and old[1] in FINAL_STATES and fixture["state_id"] not in FINAL_STATES:
                        raise ReferenceError("Completed reference fixture regressed; prior dataset retained")
                    fid = upsert(db, "football_reference_fixtures", dict(values, reference_season_id=sid,
                                 created_at=now, updated_at=now), ("provider", "external_fixture_id"))
                    # Complete event snapshot: missing old IDs become inactive; retained IDs update in place.
                    db.execute("UPDATE football_reference_events SET is_active=0, is_present=0, updated_at=? WHERE reference_fixture_id=? AND is_present=1", (now, fid))
                    for event in fixture["events"]:
                        old = db.execute("SELECT reference_fixture_id FROM football_reference_events WHERE provider=? AND external_event_id=?",
                                         (PROVIDER, event["external_event_id"])).fetchone()
                        if old and old[0] != fid:
                            raise ReferenceError("Reference event belongs to another fixture")
                        upsert(db, "football_reference_events", dict(event, reference_fixture_id=fid,
                               created_at=now, updated_at=now), ("provider", "external_event_id"))
                db.execute("INSERT INTO football_reference_syncs(reference_season_id,status,started_at,completed_at,counts_json) VALUES(?,?,?,?,?)",
                           (sid, "completed", started, now, json.dumps(report, sort_keys=True)))
                if before_commit is not None:
                    before_commit(db)
                db.commit()
            except Exception:
                db.rollback()
                raise
    except ReferenceError:
        raise
    except sqlite3.OperationalError as exc:
        code = "database_busy" if getattr(exc, "sqlite_errorcode", 0) & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else "database_error"
        raise ReferenceError("Reference database update failed; transaction rolled back", code=code) from None
    except Exception:
        raise ReferenceError("Reference database update failed; transaction rolled back", code="database_error") from None
    return dict(season_id=season_id, dry_run=False, **report)


def inspect_reference(database):
    path = database_path(database)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tasks = {}
        if 'football_reference_tasks' in tables:
            for row in db.execute("SELECT provider,external_season_id,status,started_at,completed_at,lease_until,next_attempt_after,last_successful_sync_at,error_code FROM football_reference_tasks"):
                task = dict(row)
                tasks[(task.pop('provider'), task.pop('external_season_id'))] = task
        reports = []
        seasons = db.execute("SELECT * FROM football_reference_seasons ORDER BY provider,external_season_id") if 'football_reference_seasons' in tables else []
        for season in seasons:
            fixtures = [dict(r) for r in db.execute("SELECT * FROM football_reference_fixtures WHERE reference_season_id=? ORDER BY external_fixture_id", (season["id"],))]
            for f in fixtures:
                f["events"] = [dict(r) for r in db.execute("SELECT * FROM football_reference_events WHERE reference_fixture_id=? ORDER BY external_event_id", (f["id"],))]
            reports.append(dict(season_id=season["external_season_id"], provider=season["provider"], name=season["name"],
                                sync_status=season["sync_status"], last_successful_sync_at=season["last_successful_sync_at"], **counts(fixtures)))
            task = tasks.pop((season['provider'], season['external_season_id']), None)
            if task:
                reports[-1]['latest_attempt'] = task
        for (provider, season_id), task in sorted(tasks.items()):
            reports.append(dict(provider=provider, season_id=season_id, sync_status='not_imported',
                                last_successful_sync_at=None, latest_attempt=task, **counts([])))
        return reports
