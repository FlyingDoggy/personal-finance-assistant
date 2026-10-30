"""Version-specific human edit groups, including mixed legacy/native refunds."""
from __future__ import annotations

import json
from copy import deepcopy

from . import historical
from .domain import DomainError, validate_transaction
from .storage import raw_payload, save_transaction


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS transaction_edit_members(
        group_id TEXT NOT NULL,transaction_id TEXT NOT NULL REFERENCES transactions(id),
        transaction_version INTEGER NOT NULL,PRIMARY KEY(transaction_id,transaction_version))''')


def save_plan(db, planned, group_id, reason):
    members=sorted(planned)
    for tid,payload in planned.items():
        p=deepcopy(payload)
        if historical.is_historical(p): p['historical']['edit_group']={'id':group_id,'members':members}
        version=save_transaction(db,tid,p,'edit',reason,actor='human')
        db.execute('INSERT INTO transaction_edit_members VALUES(?,?,?)',(group_id,tid,version))


def undo_plan(db, current, txs, acc):
    group=db.execute('SELECT group_id FROM transaction_edit_members WHERE transaction_id=? AND transaction_version=?',
                     (current['id'],current['version'])).fetchone()
    if not group:
        if historical.is_historical(current): return historical.undo_plan(db,current,txs)
        last=db.execute('SELECT * FROM transaction_versions WHERE transaction_id=? AND version=?', (current['id'],current['version'])).fetchone()
        if not last or last['action']!='edit' or not last['before_payload']:
            raise DomainError('当前版本没有可直接撤销的人工修改。','nothing_to_undo',409)
        result={current['id']:json.loads(last['before_payload'])}
    else:
        rows=db.execute('SELECT * FROM transaction_edit_members WHERE group_id=?',(group['group_id'],)).fetchall()
        result={}
        for row in rows:
            tid=row['transaction_id']; t=txs.get(tid)
            if not t or t['version']!=row['transaction_version']:
                raise DomainError('关联记录已有后续修改，请先核对，不能用旧组覆盖。','linked_version_conflict',409)
            last=db.execute('SELECT * FROM transaction_versions WHERE transaction_id=? AND version=?',(tid,t['version'])).fetchone()
            if last['action']!='edit' or not last['before_payload']:
                raise DomainError('关联记录没有可撤销的人工版本。','linked_version_conflict',409)
            result[tid]=json.loads(last['before_payload'])
    validated=historical.validate_graph(deepcopy(result),txs)
    if validated!=result:
        raise DomainError('撤销会覆盖后续退款分类，请先处理关联业务。','linked_amount_conflict',409)
    for tid,p in result.items():
        if not historical.is_historical(p):
            validate_transaction(p,acc,{**txs,**result},tid)
    return result
