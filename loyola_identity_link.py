"""One-time private operator linking of existing Stage 2 Loyola placeholders.

No Flask/Auth0 imports, network, account discovery, schema initialization or claims
endpoint. Approval is an independently reviewed packet hash, not a file boolean.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import base64
import time
from urllib.parse import urlsplit
import uuid

from foundation_backfill_operator import path as checked_path, private_directory, strict_json
from foundation_backfill_rehearsal import _restrict_new_windows_artifact
from foundation_migrations import (check_integrity, digest, quoted, row_manifest,
                                   schema, table_columns)

CONTRACT='loyola-existing-identity-links-v1'
CONFIRM='LINK_EXACT_REVIEWED_LOYOLA_IDENTITIES'
HEX=re.compile(r'[0-9a-f]{64}')

class LinkRefused(ValueError): pass

def need(ok,code):
    if not ok:raise LinkRefused(code)

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def private_file(value):
    p=checked_path(value);private_directory(p.parent)
    if os.name=='nt':
        literal="'"+str(p).replace("'","''")+"'"
        code="$ErrorActionPreference='Stop'; $sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; $a=[System.IO.File]::GetAccessControl("+literal+"); if($a.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $sid){throw 'owner'}; $r=@($a.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])); if($r.Count -ne 3){throw 'count'}; foreach($x in $r){if($x.IdentityReference.Value -notin @($sid,'S-1-5-18','S-1-5-32-544') -or $x.AccessControlType -ne 'Allow' -or $x.FileSystemRights -ne 'FullControl'){throw 'grant'}}; 'PRIVATE'"
        r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(code.encode('utf-16le')).decode()],capture_output=True,timeout=15)
        need(r.returncode==0 and r.stdout.strip()==b'PRIVATE','PRIVATE_FILE_ACL_REQUIRED')
    else:
        need(p.stat().st_uid==os.getuid() and p.stat().st_mode & 0o077==0,'PRIVATE_FILE_MODE_REQUIRED')
    return p

def identity(p):
    st=p.stat();return dict(path=str(p),device=st.st_dev,inode=st.st_ino)

def write_private(value,document):
    p=Path(value);private_directory(p.parent)
    with p.open('xb') as f:
        if os.name=='nt':_restrict_new_windows_artifact(p)
        else:os.fchmod(f.fileno(),0o600)
        f.write(json.dumps(document,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode())
        f.flush();os.fsync(f.fileno())
    private_file(p)
    return p

def load(value,expected):
    p=private_file(value)
    need(type(expected) is str and HEX.fullmatch(expected) and p.stat().st_size<=2*1024*1024,'EXACT_PRIVATE_ARTIFACT_REQUIRED')
    raw=p.read_bytes();need(hashlib.sha256(raw).hexdigest()==expected,'ARTIFACT_HASH_MISMATCH')
    return strict_json(raw)

def fields(value,keys):need(type(value) is dict and set(value)==set(keys),'UNEXPECTED_FIELDS')

def mapping(value):
    fields(value,{'contract','issuer','owner_decision','links'})
    need(value['contract']==CONTRACT,'MAPPING_VERSION_REQUIRED')
    issuer=value['issuer'];need(type(issuer) is str,'ISSUER_REQUIRED')
    u=urlsplit(issuer)
    need(u.scheme=='https' and u.hostname==u.netloc and '.' in u.netloc and u.path=='/' and not u.query and not u.fragment and '%' not in issuer and '\\' not in issuer,'EXACT_HTTPS_ISSUER_REQUIRED')
    links=value['links'];need(type(links) is list and 1<=len(links)<=6,'EXPLICIT_LINKS_REQUIRED')
    refs=[value['owner_decision']]
    for link in links:
        fields(link,{'membership_id','user_id','subject','identity_evidence','membership_evidence'})
        need(all(type(link[k]) is int and 0<link[k]<2**63 for k in ('membership_id','user_id')),'EXACT_IDS_REQUIRED')
        need(type(link['subject']) is str and 0<len(link['subject'])<=512 and link['subject']==link['subject'].strip() and not any(ord(c)<32 for c in link['subject']),'EXACT_SUBJECT_REQUIRED')
        refs.extend([link['identity_evidence'],link['membership_evidence']])
    for key in ('membership_id','user_id','subject'):
        need(len({l[key] for l in links})==len(links),'DUPLICATE_MAPPING')
    seen=set()
    for ref in refs:
        fields(ref,{'path','sha256'})
        need(type(ref['path']) is str and type(ref['sha256']) is str and HEX.fullmatch(ref['sha256']),'REVIEW_EVIDENCE_REQUIRED')
        if (ref['path'],ref['sha256']) not in seen:
            p=private_file(ref['path']);need(sha(p)==ref['sha256'],'REVIEW_EVIDENCE_HASH_MISMATCH')
            seen.add((ref['path'],ref['sha256']))
    return value

def connect(p,writable=False):
    db=sqlite3.connect(p.as_uri()+('?mode=rw' if writable else '?mode=ro'),uri=True,timeout=2,isolation_level=None)
    db.enable_load_extension(False)
    db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA trusted_schema=OFF');db.execute('PRAGMA temp_store=MEMORY')
    if not writable:db.execute('PRAGMA query_only=ON')
    return db

def fingerprint(db):return dict(schema_sha256=digest(schema(db)),data_sha256=digest(row_manifest(db,table_columns(db))))

def protected(db,links):
    # Project only explicitly allowed cells and newly allocated identity rows.
    members={l['membership_id'] for l in links};users={l['user_id'] for l in links}
    identities={l['identity_id'] for l in links};parts={}
    for name,columns in table_columns(db).items():
        rows=[]
        for values in db.execute('SELECT '+','.join(map(quoted,columns))+' FROM '+quoted(name)):
            row=dict(zip(columns,values))
            if name=='user_identities' and row['id'] in identities:continue
            if name=='users' and row['id'] in users:row['status']=None;row['updated_at']=None
            if name=='players' and row['id'] in members:row['status']=None;row['role']=None
            rows.append({k:({'blob':v.hex()} if isinstance(v,bytes) else v) for k,v in row.items()})
        parts[name]=digest(sorted((digest(r) for r in rows)))
    return digest(parts)

def baseline(db,doc):
    check_integrity(db)
    columns=table_columns(db)
    need(set(columns.get('user_identities',()))=={'id','user_id','issuer','subject','created_at','last_authenticated_at'},'IDENTITY_SCHEMA_REVIEW_REQUIRED')
    rows=db.execute("SELECT id,slug,correspondent_enabled FROM leagues WHERE id=1").fetchall()
    need(rows==[(1,'loyola-pick-em',0)],'LOYOLA_NO_SEND_BASELINE_REQUIRED')
    need(db.execute('SELECT COUNT(*) FROM players WHERE league_id=1').fetchone()[0]==6,'EXACT_SIX_PLAYER_ROSTER_REQUIRED')
    need(db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='private_beta_002_backfill'").fetchone()[0]==1,'STAGE2_RECEIPT_REQUIRED')
    need(db.execute('SELECT COUNT(*) FROM league_notification_recipients WHERE league_id=1').fetchone()[0]==0,'NO_SEND_RECIPIENT_BASELINE_REQUIRED')
    next_id=db.execute('SELECT COALESCE(MAX(id),0)+1 FROM user_identities').fetchone()[0]
    result=[]
    for i,link in enumerate(sorted(doc['links'],key=lambda l:l['membership_id'])):
        member=db.execute('SELECT user_id,league_id,role,status,left_at,recap_opt_in FROM players WHERE id=?',(link['membership_id'],)).fetchone()
        need(member==(link['user_id'],1,None,None,None,None),'PLACEHOLDER_MEMBERSHIP_MISMATCH')
        user=db.execute('SELECT status,is_platform_admin,email,email_normalized,email_verified_at FROM users WHERE id=?',(link['user_id'],)).fetchone()
        need(user==('unclaimed',0,None,None,None),'UNCLAIMED_NONPRIVILEGED_USER_REQUIRED')
        need(db.execute('SELECT COUNT(*) FROM players WHERE user_id=?',(link['user_id'],)).fetchone()[0]==1,'UNEXPECTED_MEMBERSHIP_ASSOCIATION')
        need(db.execute('SELECT COUNT(*) FROM user_identities WHERE user_id=? OR (issuer=? AND subject=?)',(link['user_id'],doc['issuer'],link['subject'])).fetchone()[0]==0,'EXISTING_IDENTITY_ASSOCIATION')
        result.append(dict(membership_id=link['membership_id'],user_id=link['user_id'],identity_id=next_id+i,subject=link['subject']))
    return result

def prepare(*,database,mapping_path,mapping_sha256,expected_issuer,packet_path):
    target=private_file(database);doc=mapping(load(mapping_path,mapping_sha256))
    need(doc['issuer']==expected_issuer,'PINNED_ISSUER_MISMATCH')
    with closing(connect(target)) as db:
        db.execute('BEGIN');links=baseline(db,doc)
        packet=dict(contract=CONTRACT,operation_id=uuid.uuid4().hex,target=identity(target),source=fingerprint(db),
                    protected_sha256=protected(db,links),mapping_path=str(private_file(mapping_path)),mapping_sha256=mapping_sha256,
                    issuer=expected_issuer,links=links,created_at=datetime.now(timezone.utc).isoformat())
    write_private(packet_path,packet)
    return dict(status='prepared_owner_review_required',packet_sha256=sha(Path(packet_path)),links=len(links),execution_authorized=False)

def inputs(packet_path,expected):
    packet=load(packet_path,expected)
    fields(packet,{'contract','operation_id','target','source','protected_sha256','mapping_path','mapping_sha256','issuer','links','created_at'})
    need(packet['contract']==CONTRACT and re.fullmatch('[0-9a-f]{32}',packet['operation_id']),'PACKET_VERSION_REQUIRED')
    target=private_file(packet['target']['path']);need(identity(target)==packet['target'],'TARGET_IDENTITY_MISMATCH')
    doc=mapping(load(packet['mapping_path'],packet['mapping_sha256']))
    need(doc['issuer']==packet['issuer'],'PINNED_ISSUER_MISMATCH')
    return packet,target,doc

def state_matches(db,packet,stamp):
    need(protected(db,packet['links'])==packet['protected_sha256'],'PROTECTED_STATE_CHANGED')
    for l in packet['links']:
        need(db.execute('SELECT status,updated_at FROM users WHERE id=?',(l['user_id'],)).fetchone()==('active',stamp),'ACTIVATION_STATE_MISMATCH')
        need(db.execute('SELECT user_id,league_id,role,status FROM players WHERE id=?',(l['membership_id'],)).fetchone()==(l['user_id'],1,'member','active'),'MEMBERSHIP_STATE_MISMATCH')
        need(db.execute('SELECT user_id,issuer,subject,created_at,last_authenticated_at FROM user_identities WHERE id=?',(l['identity_id'],)).fetchone()==(l['user_id'],packet['issuer'],l['subject'],stamp,None),'IDENTITY_STATE_MISMATCH')

def audit_paths(packet,root):
    root=private_directory(root)
    return {name:root/(packet['operation_id']+'.'+name+'.json') for name in ('attempt','intent','receipt')}

def verify(*,packet_path,packet_sha256,audit_directory):
    packet,target,_=inputs(packet_path,packet_sha256);paths=audit_paths(packet,audit_directory)
    attempt=load(paths['attempt'],sha(paths['attempt']))
    fields(attempt,{'packet_sha256','applied_at','target','automatic_retry'})
    need(attempt['packet_sha256']==packet_sha256 and attempt['target']==packet['target'] and attempt['automatic_retry'] is False,'AUDIT_BINDING_MISMATCH')
    need(paths['intent'].exists(),'ATTEMPT_REQUIRES_INDEPENDENT_REVIEW')
    intent=load(paths['intent'],sha(paths['intent']))
    fields(intent,{'packet_sha256','after','protected_sha256','applied_at'})
    need(intent['packet_sha256']==packet_sha256 and intent['applied_at']==attempt['applied_at'] and intent['protected_sha256']==packet['protected_sha256'] and intent['after']['schema_sha256']==packet['source']['schema_sha256'],'AUDIT_BINDING_MISMATCH')
    with closing(connect(target)) as db:
        db.execute('BEGIN');check_integrity(db)
        need(fingerprint(db)==intent['after'],'OUTCOME_OR_STATE_DRIFT_REQUIRES_REVIEW')
        state_matches(db,packet,attempt['applied_at'])
    present=paths['receipt'].exists() or paths['receipt'].is_symlink()
    if present:
        recorded=load(paths['receipt'],sha(private_file(paths['receipt'])))
        expected=dict(status='applied',packet_sha256=packet_sha256,mapping_sha256=packet['mapping_sha256'],before=packet['source'],after=intent['after'],protected_sha256=packet['protected_sha256'],applied_at=attempt['applied_at'],links=len(packet['links']),writes=3*len(packet['links']),email_or_notification_changes=False,privilege_grants=False)
        need(recorded==expected,'AUDIT_RECEIPT_MISMATCH')
    return dict(status='already_applied' if present else 'applied_receipt_missing_review_required',writes=0,receipt_present=present,packet_sha256=packet_sha256)

def apply(*,packet_path,packet_sha256,audit_directory,confirmation,writers_fenced,fail_at=None):
    need(confirmation==CONFIRM and writers_fenced is True,'EXPLICIT_OWNER_APPROVAL_AND_WRITER_FENCE_REQUIRED')
    packet,target,doc=inputs(packet_path,packet_sha256);paths=audit_paths(packet,audit_directory)
    if paths['attempt'].exists():return verify(packet_path=packet_path,packet_sha256=packet_sha256,audit_directory=audit_directory)
    need(not any(p.exists() or p.is_symlink() for p in paths.values()),'EXISTING_AUDIT_ARTIFACT')
    stamp=datetime.now(timezone.utc).isoformat()
    write_private(paths['attempt'],dict(packet_sha256=packet_sha256,applied_at=stamp,target=packet['target'],automatic_retry=False))
    def authorizer(action,first,second,database,trigger):
        if trigger is not None or database not in (None,'main'):return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_TRANSACTION):return sqlite3.SQLITE_OK
        if action==sqlite3.SQLITE_FUNCTION and (second or '').lower() in {'count','max','coalesce','trim','lower','like'}:return sqlite3.SQLITE_OK
        if action==sqlite3.SQLITE_PRAGMA and first.lower() in {'table_info','integrity_check','foreign_key_check'}:return sqlite3.SQLITE_OK
        if action==sqlite3.SQLITE_UPDATE and (first,second) in {('users','status'),('users','updated_at'),('players','role'),('players','status')}:return sqlite3.SQLITE_OK
        if action==sqlite3.SQLITE_INSERT and first=='user_identities':return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    with closing(connect(target,True)) as db:
        deadline=time.monotonic()+30
        db.set_authorizer(authorizer);db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        try:
            db.execute('BEGIN IMMEDIATE')
            need(fingerprint(db)==packet['source'],'STALE_SOURCE_FINGERPRINT')
            need(baseline(db,doc)==packet['links'],'REVIEWED_LINKS_MISMATCH')
            changes=db.total_changes
            for l in packet['links']:
                db.execute('INSERT INTO user_identities(id,user_id,issuer,subject,created_at,last_authenticated_at) VALUES(?,?,?,?,?,NULL)',(l['identity_id'],l['user_id'],packet['issuer'],l['subject'],stamp))
                need(db.execute("UPDATE users SET status='active',updated_at=? WHERE id=? AND status='unclaimed' AND is_platform_admin=0",(stamp,l['user_id'])).rowcount==1,'USER_COMPARE_AND_SET_FAILED')
                if fail_at=='after_user':raise LinkRefused('INJECTED_FAILURE')
                need(db.execute("UPDATE players SET role='member',status='active' WHERE id=? AND user_id=? AND league_id=1 AND role IS NULL AND status IS NULL",(l['membership_id'],l['user_id'])).rowcount==1,'MEMBERSHIP_COMPARE_AND_SET_FAILED')
            need(db.total_changes-changes==3*len(packet['links']),'UNEXPECTED_WRITE_COUNT')
            check_integrity(db);state_matches(db,packet,stamp)
            after=fingerprint(db)
            need(after['schema_sha256']==packet['source']['schema_sha256'],'SCHEMA_CHANGED')
            write_private(paths['intent'],dict(packet_sha256=packet_sha256,after=after,protected_sha256=packet['protected_sha256'],applied_at=stamp))
            if fail_at=='before_commit':raise LinkRefused('INJECTED_FAILURE')
            # Hash revalidation does not reinterpret evidence or call a provider.
            for ref in [doc['owner_decision'],*[r[k] for r in doc['links'] for k in ('identity_evidence','membership_evidence')]]:
                need(sha(checked_path(ref['path']))==ref['sha256'],'REVIEW_EVIDENCE_CHANGED')
            need(sha(checked_path(packet_path))==packet_sha256 and sha(checked_path(packet['mapping_path']))==packet['mapping_sha256'],'APPROVED_ARTIFACT_CHANGED')
            need(identity(checked_path(target))==packet['target'],'TARGET_IDENTITY_MISMATCH')
            need(time.monotonic()<=deadline,'TRANSACTION_DEADLINE_EXCEEDED')
            db.execute('COMMIT')
        except BaseException:
            db.set_progress_handler(None,0)
            if db.in_transaction:db.execute('ROLLBACK')
            raise
    # A post-commit receipt failure is uncertain, never an invitation to retry.
    receipt=dict(status='applied',packet_sha256=packet_sha256,mapping_sha256=packet['mapping_sha256'],before=packet['source'],after=after,protected_sha256=packet['protected_sha256'],applied_at=stamp,links=len(packet['links']),writes=3*len(packet['links']),email_or_notification_changes=False,privilege_grants=False)
    write_private(paths['receipt'],receipt)
    return dict(status='applied',links=len(packet['links']),writes=receipt['writes'],receipt_sha256=sha(paths['receipt']))

def main():
    parser=argparse.ArgumentParser(description='Private owner-controlled existing Loyola identity links; no automatic claiming.')
    parser.add_argument('mode',choices=('prepare','apply','verify'))
    for name in ('database','mapping','mapping_sha256','issuer','packet','packet_sha256','audit_directory'):parser.add_argument('--'+name.replace('_','-'))
    parser.add_argument('--confirmation');parser.add_argument('--writers-fenced',action='store_true')
    args=parser.parse_args()
    try:
        if args.mode=='prepare':result=prepare(database=args.database,mapping_path=args.mapping,mapping_sha256=args.mapping_sha256,expected_issuer=args.issuer,packet_path=args.packet)
        elif args.mode=='apply':result=apply(packet_path=args.packet,packet_sha256=args.packet_sha256,audit_directory=args.audit_directory,confirmation=args.confirmation,writers_fenced=args.writers_fenced)
        else:result=verify(packet_path=args.packet,packet_sha256=args.packet_sha256,audit_directory=args.audit_directory)
        print(json.dumps(result,sort_keys=True));return 0
    except Exception:
        print(json.dumps(dict(status='refused_or_outcome_uncertain',automatic_retry=False,independent_review_required=True)));return 1

if __name__=='__main__':raise SystemExit(main())
