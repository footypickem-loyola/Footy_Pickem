"""Explicit post-Stage-2 table rebuild. No startup hook, CLI or implicit target.

prepare() verifies unchanged Stage 2 evidence and simulates the rebuild in memory.
apply() requires that exact approved packet, a new checkpoint and exclusive writer
coordination. Neither API is authorization to touch production.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
import tempfile

from foundation_migrations import (schema, digest, quoted, table_columns, row_manifest,
                                   check_integrity, backup_database)
from foundation_backfill_receipt import verify_state
from foundation_backfill_execute import _path
from foundation_backfill_operator import private_directory
from foundation_backfill_rehearsal import _restrict_new_windows_artifact, _inputs

VERSION='private_beta_003_league_keys'
CONTRACT='post-stage2-league-keys-v1'
INDEX='CREATE UNIQUE INDEX uix_league_season_code ON seasons(league_id,code)'
CHECKS={'players': "league_id IS NOT NULL AND user_id IS NOT NULL AND name_key IS NOT NULL AND trim(name_key) <> ''",
        'seasons': 'league_id IS NOT NULL AND football_season_id IS NOT NULL'}

class CompatibilityError(ValueError):pass

def need(value,code):
    if not value:raise CompatibilityError(code)

def implementation():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

def ledger(db):
    return db.execute('SELECT version,checksum,applied_at,before_schema_hash,after_schema_hash FROM schema_migrations ORDER BY version').fetchall()

def fingerprints(db):
    return row_manifest(db,{t:c for t,c in table_columns(db).items() if t!='schema_migrations'})

def foreign_keys(db):
    return {t:db.execute('PRAGMA foreign_key_list('+quoted(t)+')').fetchall() for t in table_columns(db)}

def validate_dependencies(db):
    check_integrity(db)
    for kind,name,_,sql in schema(db):
        if kind=='view':db.execute('EXPLAIN SELECT * FROM '+quoted(name)+' LIMIT 0').fetchall()
    # Prepare (never execute) trigger programs. Unknown functions/dependencies refuse.
    for table,columns in table_columns(db).items():
        fields=','.join(quoted(c)+'='+quoted(c) for c in columns)
        for statement in ('INSERT INTO '+quoted(table)+' DEFAULT VALUES',
                          'UPDATE '+quoted(table)+' SET '+fields,'DELETE FROM '+quoted(table)):
            db.execute('EXPLAIN '+statement).fetchall()

def rebuild(db, fail_at=None):
    before_columns=table_columns(db);before_fk=foreign_keys(db)
    before_rows=fingerprints(db);before_objects=schema(db)
    for table,key in (('players','name'),('seasons','code')):
        sql=next(r[3] for r in before_objects if r[:2]==('table',table))
        need(not re.search(r'AUTOINCREMENT|WITHOUT ROWID|GENERATED',sql,re.I),'UNSUPPORTED_TABLE_LAYOUT')
        indexes=db.execute('PRAGMA index_list('+quoted(table)+')').fetchall()
        global_keys=[i for i in indexes if i[2] and tuple(r[2] for r in db.execute('PRAGMA index_info('+quoted(i[1])+')'))==(key,)]
        need(len(global_keys)==1 and global_keys[0][3]=='u','UNEXPECTED_GLOBAL_UNIQUENESS')
        for refs in before_fk.values():
            need(not any(r[2]==table and r[4]==key for r in refs),'REFERENCED_GLOBAL_KEY')
        need(not any(re.search(re.escape(global_keys[0][1]),r[3] or '',re.I) for r in before_objects),'AUTO_INDEX_DEPENDENCY')
        pattern=r',\s*UNIQUE\s*\(\s*'+key+r'\s*\)'
        sql,count=re.subn(pattern,'',sql,flags=re.I)
        need(count==1,'UNSUPPORTED_UNIQUE_DECLARATION')
        need(not db.execute('SELECT 1 FROM '+quoted(table)+' WHERE NOT ('+CHECKS[table]+') LIMIT 1').fetchone(),'UNOWNED_ROWS')
        scratch='_league_keys_'+table
        need(scratch not in before_columns,'PARTIAL_REBUILD')
        sql,count=re.subn(r'^CREATE TABLE\s+"?'+table+r'"?', 'CREATE TABLE '+quoted(scratch),sql,count=1,flags=re.I)
        need(count==1 and sql.rstrip().endswith(')'),'UNSUPPORTED_TABLE_DECLARATION')
        sql=sql.rstrip()[:-1]+', CHECK ('+CHECKS[table]+'))'
        db.execute(sql)
        fields=','.join(map(quoted,before_columns[table]))
        db.execute('INSERT INTO '+quoted(scratch)+' ('+fields+') SELECT '+fields+' FROM '+quoted(table))
        db.execute('DROP TABLE '+quoted(table))
        if fail_at==table:raise CompatibilityError('INJECTED_FAILURE')
        db.execute('ALTER TABLE '+quoted(scratch)+' RENAME TO '+quoted(table))
        for kind,_,owner,ddl in before_objects:
            if owner==table and kind in ('index','trigger'):db.execute(ddl)
    db.execute(INDEX)
    need(before_columns==table_columns(db),'COLUMN_DRIFT')
    need(before_fk==foreign_keys(db),'FOREIGN_KEY_DRIFT')
    need(before_rows==fingerprints(db),'ROW_DRIFT')
    kept=[r for r in before_objects if not (r[0]=='table' and r[1] in CHECKS)]
    after=schema(db)
    need(all(r in after for r in kept),'DEPENDENCY_DRIFT')
    validate_dependencies(db)

def connection(path,writable=False):
    db=sqlite3.connect(path.as_uri()+('?mode=rw' if writable else '?mode=ro'),uri=True,timeout=2,isolation_level=None)
    db.execute('PRAGMA foreign_keys=OFF' if writable else 'PRAGMA foreign_keys=ON')
    db.execute('PRAGMA legacy_alter_table=ON')
    db.execute('PRAGMA trusted_schema=OFF')
    if not writable:db.execute('PRAGMA query_only=ON')
    return db

def evidence_binding(evidence):
    # Revalidate manifest/plan bytes and offline owner evidence on reruns too.
    _,_,validation=_inputs(evidence['manifest'],evidence['plan'],evidence['expected_manifest_checksum'],
                           evidence['expected_plan_sha256'],offline_review=evidence.get('offline_review'))
    need(validation['status']=='valid','STAGE2_INPUTS_REFUSED')
    return digest(dict(manifest=evidence['expected_manifest_checksum'],plan=evidence['expected_plan_sha256'],
                       source=evidence['plan']['source'],offline=evidence.get('offline_review') is not None))

def prepare(path, *, stage2_evidence):
    path=_path(path);private_directory(path.parent)
    with closing(connection(path)) as db:
        db.execute('BEGIN')
        need(VERSION not in [r[0] for r in ledger(db)],'ALREADY_APPLIED_REVIEW_PACKET_REQUIRED')
        verified=verify_state(db,**dict(stage2_evidence,require_completed=True))
        need(verified['status']=='stage2_complete','VERIFIED_STAGE2_REQUIRED')
        before=schema(db);rows=fingerprints(db);prior=ledger(db)
        with closing(sqlite3.connect(':memory:',isolation_level=None)) as trial:
            db.backup(trial)
            trial.execute('PRAGMA foreign_keys=OFF');trial.execute('PRAGMA legacy_alter_table=ON')
            trial.execute('BEGIN IMMEDIATE')
            rebuild(trial)
            after=schema(trial);trial.rollback()
        return dict(contract=CONTRACT,version=VERSION,implementation=implementation(),
            evidence=evidence_binding(stage2_evidence),before_schema=before,after_schema=after,
            preserved_rows=rows,prior_ledger=prior,offline_rehearsal_only=verified.get('offline_rehearsal_only',False))

def verify_applied(db,packet,expected_packet_sha256):
    need(digest(packet)==expected_packet_sha256 and packet['implementation']==implementation(),'PACKET_MISMATCH')
    need(packet['contract']==CONTRACT and packet['version']==VERSION,'CONTRACT_MISMATCH')
    need(digest(schema(db))==digest(packet['after_schema']),'POST_SCHEMA_DRIFT')
    rows=ledger(db);current=[r for r in rows if r[0]==VERSION]
    need(len(current)==1 and current[0][1]==expected_packet_sha256 and
         current[0][3:]==(digest(packet['before_schema']),digest(packet['after_schema'])),'MIGRATION_RECEIPT_MISMATCH')
    need(digest([r for r in rows if r[0]!=VERSION])==digest(packet['prior_ledger']),'STAGE2_LEDGER_CHANGED')
    try:
        timestamp=datetime.fromisoformat(current[0][2]);need(timestamp.utcoffset()==timezone.utc.utcoffset(timestamp),'RECEIPT_TIME_INVALID')
    except (ValueError,TypeError):raise CompatibilityError('RECEIPT_TIME_INVALID') from None
    validate_dependencies(db)
    return dict(status='already_applied',version=VERSION,stage2_ledger_preserved=True)

def apply(path, *, packet, expected_packet_sha256, stage2_evidence, checkpoint,
          authorized=False, writers_fenced=False, offline=False, fail_at=None):
    need(authorized is True and writers_fenced is True,'EXPLICIT_AUTHORIZATION_AND_WRITER_FENCE_REQUIRED')
    need(digest(packet)==expected_packet_sha256 and packet['implementation']==implementation(),'PACKET_MISMATCH')
    need(packet['contract']==CONTRACT and packet['version']==VERSION,'CONTRACT_MISMATCH')
    need(evidence_binding(stage2_evidence)==packet['evidence'],'STAGE2_EVIDENCE_MISMATCH')
    need(not packet['offline_rehearsal_only'] or offline is True,'OFFLINE_ONLY')
    path=_path(path);private_directory(path.parent)
    need(not packet['offline_rehearsal_only'] or path.is_relative_to(Path(tempfile.gettempdir()).resolve()),'OFFLINE_TEMP_TARGET_REQUIRED')
    with closing(connection(path,True)) as db:
        try:
            db.execute('BEGIN IMMEDIATE')
            if VERSION in [r[0] for r in ledger(db)]:
                result=verify_applied(db,packet,expected_packet_sha256);db.rollback();return result
            need(digest(schema(db))==digest(packet['before_schema']),'BASE_SCHEMA_DRIFT')
            verify_state(db,**dict(stage2_evidence,require_completed=True))
            need(fingerprints(db)==packet['preserved_rows'] and digest(ledger(db))==digest(packet['prior_ledger']),'BASE_DATA_DRIFT')
            checkpoint=Path(checkpoint)
            need(checkpoint.is_absolute() and not checkpoint.exists(),'NEW_ABSOLUTE_CHECKPOINT_REQUIRED')
            private_directory(checkpoint.parent)
            need(not any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() or (p/'.git').exists() for p in checkpoint.parents),'UNSAFE_CHECKPOINT_PARENT')
            backup_database(path,checkpoint)
            _restrict_new_windows_artifact(checkpoint,directory=False)
            with closing(connection(checkpoint)) as saved:
                need(digest(schema(saved))==digest(packet['before_schema']) and
                     fingerprints(saved)==packet['preserved_rows'] and digest(ledger(saved))==digest(packet['prior_ledger']),'CHECKPOINT_MISMATCH')
                verify_state(saved,**dict(stage2_evidence,require_completed=True))
            rebuild(db,fail_at)
            need(digest(schema(db))==digest(packet['after_schema']),'POST_SCHEMA_DRIFT')
            db.execute('INSERT INTO schema_migrations VALUES(?,?,?,?,?)',
                (VERSION,expected_packet_sha256,datetime.now(timezone.utc).isoformat(),digest(packet['before_schema']),digest(packet['after_schema'])))
            verify_applied(db,packet,expected_packet_sha256)
            db.commit()
        except BaseException:
            db.rollback();raise
        finally:
            db.execute('PRAGMA foreign_keys=ON')
            need(db.execute('PRAGMA foreign_keys').fetchone()==(1,),'FOREIGN_KEYS_NOT_RESTORED')
    return dict(status='applied',version=VERSION,rows_preserved=True,stage2_ledger_preserved=True,checkpoint=str(checkpoint))
