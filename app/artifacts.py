"""Download immutable registered originals, never arbitrary local paths."""
from __future__ import annotations

import hashlib
import os
import re
import stat
from urllib.parse import quote

from fastapi.responses import Response

from .domain import DomainError

MAX_DOWNLOAD = 512 * 1024 * 1024


def download(store, sha):
    if not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha):
        raise DomainError('原件不存在。', 'not_found', 404)
    with store.read() as db:
        row = db.execute('SELECT filename,size FROM artifacts WHERE sha256=?', (sha,)).fetchone()
    if not row:
        raise DomainError('原件未登记，不能下载。', 'not_found', 404)
    if not 0 <= row['size'] <= MAX_DOWNLOAD:
        raise DomainError('原件大小超出可下载范围。', 'evidence_size_unsupported', 409)
    try:
        fd = os.open(store.evidence / sha, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError as exc:
        raise DomainError('归档原件缺失，请从完整备份恢复原件。', 'evidence_missing', 409) from exc
    except OSError as exc:
        raise DomainError('归档原件不可读取，请检查完整备份。', 'evidence_corrupt', 409) from exc
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != row['size']:
            raise DomainError('原件长度或类型校验失败。', 'evidence_corrupt', 409)
        content = stream.read(row['size'] + 1)
    if len(content) != row['size'] or hashlib.sha256(content).hexdigest() != sha:
        raise DomainError('原件哈希校验失败，请从完整备份恢复。', 'evidence_corrupt', 409)
    filename = row['filename'].replace('\\', '/').rsplit('/', 1)[-1]
    filename = ''.join(c for c in filename if ord(c) >= 32 and ord(c) != 127)[:240] or 'original'
    return Response(content, media_type='application/octet-stream', headers={
        'Content-Disposition': f'attachment; filename="original-{sha[:12]}"; filename*=UTF-8\'\'{quote(filename, safe="")}',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; sandbox",
    })


def register_routes(app, store):
    @app.get('/api/v1/artifacts/{sha}/download')
    def get_artifact(sha: str):
        return download(store, sha)
