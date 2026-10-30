"""Single SQLite writer and immutable evidence storage."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import tempfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .domain import DomainError

SCHEMA_VERSION = 4


def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return str(uuid4())
def encode(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value): return hashlib.sha256(encode(value).encode()).hexdigest()


class Store:
    def __init__(self, directory):
        self.directory=Path(directory).resolve()
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.evidence=self.directory/'originals'
        self.evidence.mkdir(mode=0o700,exist_ok=True)
        self.path=self.directory/'finance.sqlite3'
        self.lock=threading.RLock()
        with closing(self.connect()) as db:
            version_value=None
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'").fetchone():
                version=db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
                if not version or version[0] not in {1,2,3,SCHEMA_VERSION}:
                    raise DomainError('数据库结构版本不受当前程序支持；未执行迁移或写入。','unsupported_schema',409)
                version_value=version[0]
            backup_path=None
            if version_value is not None and version_value < SCHEMA_VERSION:
                backup_dir=self.directory/'schema-backups'
                backup_dir.mkdir(mode=0o700,exist_ok=True)
                backup_path=backup_dir/('before-v'+str(SCHEMA_VERSION)+'-'+uuid4().hex+'.sqlite3')
                # SQLite backup includes committed WAL pages. Evidence files are
                # immutable and this additive migration never modifies them.
                with closing(sqlite3.connect(backup_path)) as backup:
                    db.backup(backup)
                os.chmod(backup_path,0o600)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN IMMEDIATE')
            try:
                for statement in '''
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
            INSERT OR IGNORE INTO metadata VALUES('schema_version',1),('data_revision',0);
            CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,currency TEXT NOT NULL,opening_balance INTEGER,opening_date TEXT,evidence TEXT,version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artifacts(sha256 TEXT PRIMARY KEY,filename TEXT NOT NULL,size INTEGER NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,filename TEXT NOT NULL,account_id TEXT NOT NULL,parser_version TEXT NOT NULL,status TEXT NOT NULL,row_count INTEGER NOT NULL,imported_count INTEGER NOT NULL,duplicate_count INTEGER NOT NULL,rejected_count INTEGER NOT NULL,errors TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(sha256,account_id,parser_version));
            CREATE TABLE IF NOT EXISTS source_rows(id TEXT PRIMARY KEY,import_id TEXT NOT NULL,row_number INTEGER NOT NULL,source_id TEXT,raw TEXT NOT NULL,transaction_id TEXT,error TEXT,UNIQUE(import_id,row_number));
            CREATE TABLE IF NOT EXISTS transactions(id TEXT PRIMARY KEY,version INTEGER NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS transaction_versions(transaction_id TEXT NOT NULL,version INTEGER NOT NULL,action TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,before_payload TEXT,after_payload TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(transaction_id,version));
            CREATE TABLE IF NOT EXISTS source_identity(account_id TEXT NOT NULL,source_id TEXT NOT NULL,fingerprint TEXT NOT NULL,transaction_id TEXT NOT NULL,PRIMARY KEY(account_id,source_id));
            CREATE TABLE IF NOT EXISTS operations(request_id TEXT PRIMARY KEY,action TEXT NOT NULL,fingerprint TEXT NOT NULL,response TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS budget_versions(year INTEGER NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,data_revision INTEGER NOT NULL,PRIMARY KEY(year,version));
            CREATE TABLE IF NOT EXISTS migration_runs(id TEXT PRIMARY KEY,manifest TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS historical_observations(id INTEGER PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS budget_provenance(year INTEGER NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(year,version));
            CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,migrated_at TEXT NOT NULL,backup_path TEXT);
            CREATE INDEX IF NOT EXISTS source_transaction ON source_rows(transaction_id);
            '''.split(';'):
                    if statement.strip(): db.execute(statement)
                from . import catalog, vouchers, report_snapshots, coverage, ai_work, imports_v2, budget_plans, classification_rules, analysis
                budget_plans.initialize(db)
                catalog.initialize(db)
                vouchers.initialize(db)
                report_snapshots.initialize(db)
                coverage.initialize(db)
                analysis.initialize(db)
                classification_rules.initialize(db)
                ai_work.initialize(db)
                imports_v2.initialize(db)
                catalog.sync(db)
                vouchers.rebuild(db)
                db.execute("UPDATE metadata SET value=? WHERE key='schema_version'",(SCHEMA_VERSION,))
                db.execute('INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)',
                           (SCHEMA_VERSION,now(),str(backup_path) if backup_path else None))
                db.commit()
            except Exception:
                db.rollback()
                raise
        os.chmod(self.path,0o600)

    def connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @contextmanager
    def read(self):
        db=self.connect()
        try:
            db.execute('BEGIN')
            yield db
        finally: db.close()

    @contextmanager
    def write(self):
        with self.lock:
            db=self.connect()
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                # Some writers insert budget rows after calling advance(); the
                # complete committed state is only available at this boundary.
                from . import catalog, vouchers, ai_work, budget_plans
                budget_plans.reconcile_legacy(db)
                ai_work.reconcile_work_items(db)
                catalog.sync(db)
                vouchers.rebuild(db)
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally: db.close()

    def archive(self, content, filename):
        sha=hashlib.sha256(content).hexdigest()
        target=self.evidence/sha
        temporary=None
        try:
            fd,temporary=tempfile.mkstemp(prefix='.receiving-',dir=self.evidence)
            with os.fdopen(fd,'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary,0o400)
            try:
                os.link(temporary,target)
                directory_fd=os.open(self.evidence,os.O_RDONLY)
                try: os.fsync(directory_fd)
                finally: os.close(directory_fd)
            except FileExistsError:
                if hashlib.sha256(target.read_bytes()).hexdigest()!=sha:
                    raise DomainError('原件哈希校验失败，已停止写入。','evidence_corrupt',409)
        finally:
            if temporary: os.unlink(temporary)
        return sha


def revision(db): return db.execute("SELECT value FROM metadata WHERE key='data_revision'").fetchone()[0]
def advance(db):
    db.execute("UPDATE metadata SET value=value+1 WHERE key='data_revision'")
    return revision(db)

def accounts(db): return {r['id']:dict(r) for r in db.execute('SELECT * FROM accounts ORDER BY created_at,id')}
def transactions(db):
    return {r['id']:{**json.loads(r['payload']),'id':r['id'],'version':r['version']} for r in db.execute('SELECT * FROM transactions ORDER BY created_at,id')}
def raw_payload(p): return {k:v for k,v in p.items() if k not in {'id','version'}}


def operation(db, request_id, action, payload):
    if not isinstance(request_id,str) or not request_id.strip() or len(request_id)>160:
        raise DomainError('request_id 须为 1–160 字符的非空字符串。')
    fingerprint=digest(payload)
    found=db.execute('SELECT * FROM operations WHERE request_id=?',(request_id,)).fetchone()
    if found:
        if found['action']!=action or found['fingerprint']!=fingerprint:
            raise DomainError('同一 request_id 已用于不同内容。','idempotency_conflict',409)
        return fingerprint,json.loads(found['response'])
    return fingerprint,None


def finish_operation(db, request_id, action, fingerprint, response):
    db.execute('INSERT INTO operations VALUES(?,?,?,?,?)',(request_id,action,fingerprint,encode(response),now()))
    return response


def save_transaction(db, tid, payload, action, reason='', actor='human'):
    existing=db.execute('SELECT * FROM transactions WHERE id=?',(tid,)).fetchone()
    version=existing['version']+1 if existing else 1
    encoded=encode(payload)
    if existing: db.execute('UPDATE transactions SET version=?,payload=? WHERE id=?',(version,encoded,tid))
    else: db.execute('INSERT INTO transactions VALUES(?,?,?,?)',(tid,version,encoded,now()))
    db.execute('INSERT INTO transaction_versions VALUES(?,?,?,?,?,?,?,?)',(tid,version,action,actor,reason,existing['payload'] if existing else None,encoded,now()))
    return version
