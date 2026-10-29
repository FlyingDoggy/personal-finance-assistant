"""A consistent logical JSON export; attachment binaries remain in originals/."""
import json

from .storage import now, revision


def export_document(db):
    tables = {}
    # Names come from SQLite's own schema, never from request parameters.
    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        name = row['name']
        identifier = '"' + name.replace('"', '""') + '"'
        tables[name] = [dict(item) for item in db.execute('SELECT * FROM ' + identifier)]
    return {
        'format': 'personal-finance-logical-export-v1',
        'created_at': now(),
        'data_revision': revision(db),
        'schema_version': db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0],
        'includes_original_files': False,
        'note': '当前数据库的一致性结构化副本，含原件清单但不含原始附件；不是完整备份包，不能单独恢复原文件。',
        'tables': tables,
    }


def export_bytes(db):
    return json.dumps(export_document(db), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
