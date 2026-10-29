"""Consistent, portable backups and isolated restoration; never replaces a live ledger."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import File, UploadFile
from fastapi.responses import FileResponse

from .domain import DomainError
from .storage import SCHEMA_VERSION, encode, now

FORMAT = 'personal-finance-backup-v1'
MAX_PACKAGE = 512 * 1024 * 1024
CONFIG_FILES = ('settings.json', 'rules.json', 'config.json')


def _hash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _inspect(path):
    with closing(sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True)) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise DomainError('备份数据库完整性检查未通过。', 'invalid_backup', 409)
        meta = dict(db.execute('SELECT key,value FROM metadata'))
        if meta.get('schema_version') not in {1, 2, SCHEMA_VERSION}:
            raise DomainError('此备份结构版本不受当前程序支持。', 'unsupported_schema', 409)
        counts = {table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                  for table in ('transactions', 'transaction_versions', 'accounts', 'budget_versions', 'artifacts')}
        artifacts = [row[0] for row in db.execute('SELECT sha256 FROM artifacts')]
        return {'schema_version': meta['schema_version'], 'data_revision': meta['data_revision'],
                'counts': counts, 'artifacts': artifacts}


def create_backup(store):
    folder = store.directory / 'backups'
    folder.mkdir(exist_ok=True, mode=0o700)
    backup_id = uuid4().hex
    target = folder / f'{backup_id}.zip'
    with tempfile.TemporaryDirectory(prefix='.backup-', dir=folder) as scratch:
        stage = Path(scratch)
        snapshot = stage / 'finance.sqlite3'
        with closing(store.connect()) as source, closing(sqlite3.connect(snapshot)) as dest:
            source.backup(dest)
        summary = _inspect(snapshot)
        files = {'finance.sqlite3': snapshot}
        for sha in summary.pop('artifacts'):
            if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
                raise DomainError('原件清单含无效哈希，未创建备份。', 'evidence_corrupt', 409)
            original = store.evidence / sha
            if original.is_symlink() or not original.is_file() or _hash(original) != sha:
                raise DomainError('原件缺失或哈希不符，未创建不完整备份。', 'evidence_corrupt', 409)
            files[f'originals/{sha}'] = original
        for name in CONFIG_FILES:
            source = store.directory / name
            if source.exists():
                if source.is_symlink() or not source.is_file():
                    raise DomainError('配置文件类型不受支持。', 'invalid_backup', 409)
                dest = stage / name
                shutil.copyfile(source, dest)
                files[f'config/{name}'] = dest
        manifest = {'format': FORMAT, 'id': backup_id, 'created_at': now(), **summary,
                    'files': [{'path': name, 'size': file.stat().st_size, 'sha256': _hash(file)} for name, file in files.items()],
                    'note': '含一致性数据库、其引用原件及应用配置；不含机器访问令牌、Codex认证或本设备显示偏好。'}
        manifest_bytes = encode(manifest).encode('utf-8')
        if sum(row['size'] for row in manifest['files']) + len(manifest_bytes) > MAX_PACKAGE:
            raise DomainError('完整备份超过本版可恢复的 512 MiB 限制，未创建备份包。', 'backup_too_large', 413)
        temporary = stage / 'package.zip'
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as package:
            for name, file in files.items():
                package.write(file, name)
            package.writestr('manifest.json', manifest_bytes)
        if temporary.stat().st_size > MAX_PACKAGE:
            raise DomainError('备份包超过本版可恢复的 512 MiB 限制，未创建备份包。', 'backup_too_large', 413)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        index = folder / f'{backup_id}.json'
        index.write_text(encode({k: v for k, v in manifest.items() if k != 'files'}), encoding='utf-8')
        os.chmod(index, 0o600)
    return {**{k: v for k, v in manifest.items() if k != 'files'}, 'size': target.stat().st_size,
            'file_count': len(manifest['files']), 'download_url': f'/api/v1/maintenance/backups/{backup_id}/download'}


def list_backups(store):
    items = []
    for path in (store.directory / 'backups').glob('*.json'):
        try:
            item = json.loads(path.read_text())
            package = path.with_suffix('.zip')
            if not package.is_file():
                continue
            items.append({**item, 'size': package.stat().st_size,
                          'download_url': f"/api/v1/maintenance/backups/{item['id']}/download"})
        except (OSError, ValueError, KeyError):
            continue
    return sorted(items, key=lambda item: item['created_at'], reverse=True)


def _safe_member(name):
    p = PurePosixPath(name)
    if '\\' in name or p.is_absolute() or '..' in p.parts or str(p) != name:
        return False
    if name in {'manifest.json', 'finance.sqlite3'}:
        return True
    if len(p.parts) == 2 and p.parts[0] == 'originals':
        return len(p.parts[1]) == 64 and all(c in '0123456789abcdef' for c in p.parts[1])
    return len(p.parts) == 2 and p.parts[0] == 'config' and p.parts[1] in CONFIG_FILES


def restore_backup(package_path, destination):
    """Validate and restore into a nonexistent directory, preserving committed operations."""
    package_path, destination = Path(package_path), Path(destination)
    if os.path.lexists(destination):
        raise DomainError('恢复目标必须是尚不存在的新目录，不能覆盖正在使用的账本。', 'restore_target_exists', 409)
    if package_path.stat().st_size > MAX_PACKAGE:
        raise DomainError('备份包超过本版 512 MiB 限制。', 'upload_too_large', 413)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.restore-', dir=destination.parent) as scratch:
        stage = Path(scratch) / 'app'
        stage.mkdir(mode=0o700)
        try:
            with zipfile.ZipFile(package_path) as archive:
                entries = archive.infolist()
                names = [i.filename for i in entries]
                if len(names) != len(set(names)) or len(names) > 100000 or sum(i.file_size for i in entries) > MAX_PACKAGE:
                    raise DomainError('备份包重复成员或解压大小超限。', 'invalid_backup', 422)
                for info in entries:
                    if not _safe_member(info.filename) or info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                        raise DomainError('备份包包含不允许的路径或文件类型。', 'invalid_backup', 422)
                manifest_info = archive.getinfo('manifest.json')
                if manifest_info.file_size > 16 * 1024 * 1024:
                    raise DomainError('备份清单过大。', 'invalid_backup', 422)
                manifest = json.loads(archive.read(manifest_info))
                if (not isinstance(manifest, dict) or manifest.get('format') != FORMAT
                    or not isinstance(manifest.get('files'), list)
                    or not isinstance(manifest.get('id'), str) or len(manifest['id']) != 32
                    or any(c not in '0123456789abcdef' for c in manifest['id'])):
                    raise DomainError('备份格式不受支持。', 'invalid_backup', 422)
                records = manifest['files']
                if len({r['path'] for r in records}) != len(records) or {r['path'] for r in records} | {'manifest.json'} != set(names):
                    raise DomainError('备份文件与清单不一致。', 'invalid_backup', 422)
                for row in records:
                    name = row['path']
                    if name == 'manifest.json' or archive.getinfo(name).file_size != row['size']:
                        raise DomainError('备份成员大小不符。', 'invalid_backup', 422)
                    target = stage / (name.removeprefix('config/') if name.startswith('config/') else name)
                    target.parent.mkdir(exist_ok=True, mode=0o700)
                    with archive.open(name) as src, target.open('xb') as out:
                        shutil.copyfileobj(src, out, 1024 * 1024)
                    os.chmod(target, 0o600)
                    if _hash(target) != row['sha256']:
                        raise DomainError('备份哈希校验失败。', 'invalid_backup', 409)
                summary = _inspect(stage / 'finance.sqlite3')
                for sha in summary.pop('artifacts'):
                    path = stage / 'originals' / sha
                    if not path.is_file() or _hash(path) != sha:
                        raise DomainError('备份缺少数据库引用的原件。', 'invalid_backup', 409)
                for key in ('schema_version', 'data_revision', 'counts'):
                    if manifest.get(key) != summary[key]:
                        raise DomainError('备份清单和数据库状态不一致。', 'invalid_backup', 409)
                (stage / 'originals').mkdir(exist_ok=True, mode=0o700)
                # Retain committed operations, invalidate execution leases only.
                with closing(sqlite3.connect(stage / 'finance.sqlite3')) as db:
                    db.row_factory = sqlite3.Row
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name='work_items'").fetchone():
                        from . import ai_work
                        if hasattr(ai_work, 'invalidate_leases_after_restore'):
                            ai_work.invalidate_leases_after_restore(db)
                    db.commit()
                # Reserve the target with exclusive mkdir, so even an empty
                # directory or dangling symlink created during validation is
                # never replaced. The restored directory is offline until done.
                try:
                    destination.mkdir(mode=0o700)
                except FileExistsError as exc:
                    raise DomainError('恢复目标已存在，未覆盖该目录。', 'restore_target_exists', 409) from exc
                try:
                    for child in stage.iterdir():
                        os.rename(child, destination / child.name)
                except Exception:
                    shutil.rmtree(destination)
                    raise
        except DomainError:
            raise
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, sqlite3.Error, RuntimeError) as exc:
            raise DomainError('备份包无法校验或恢复；当前账本未改变。', 'invalid_backup', 422) from exc
    return {'status': 'restored_to_new_directory', 'destination': str(destination.resolve()), **summary,
            'source_backup_id': manifest['id'], 'current_ledger_unchanged': True}


def register_routes(app, store):
    @app.get('/api/v1/maintenance/backups')
    def backup_list():
        return {'items': list_backups(store)}

    @app.post('/api/v1/maintenance/backups')
    def backup_create():
        return {'backup': create_backup(store)}

    @app.get('/api/v1/maintenance/backups/{backup_id}/download')
    def backup_download(backup_id: str):
        if len(backup_id) != 32 or any(c not in '0123456789abcdef' for c in backup_id):
            raise DomainError('备份不存在。', 'not_found', 404)
        path = store.directory / 'backups' / f'{backup_id}.zip'
        if not path.is_file():
            raise DomainError('备份不存在。', 'not_found', 404)
        return FileResponse(path, filename=f'finance-backup-{backup_id}.zip', media_type='application/zip')

    @app.post('/api/v1/maintenance/restore-check')
    async def backup_restore_check(file: UploadFile = File(...)):
        folder = store.directory / 'restore-checks'
        folder.mkdir(exist_ok=True, mode=0o700)
        destination = folder / uuid4().hex
        fd, temporary = tempfile.mkstemp(prefix='.upload-', dir=folder)
        try:
            total = 0
            with os.fdopen(fd, 'wb') as stream:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_PACKAGE:
                        raise DomainError('备份包超过本版 512 MiB 限制。', 'upload_too_large', 413)
                    stream.write(chunk)
            return {'restore': restore_backup(temporary, destination)}
        finally:
            Path(temporary).unlink(missing_ok=True)
