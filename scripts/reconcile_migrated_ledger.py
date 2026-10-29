"""Read-only reconciliation of the migrated app against independently read Excel.

No Store is initialized and no database is changed. Expected monetary results
come from saved Excel caches/source rows, not a second call to app calculations.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import historical
from app.budget import compute_budget
from app.reports import report, effects, budget_projection
from app.storage import accounts, transactions


def read_json(path):
    return json.loads(Path(path).read_text())


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextmanager
def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    try:
        yield db
    finally:
        db.close()


def money(value):
    return f'{Decimal(str(value))/100:,.2f}'


def reconcile(data_dir: Path, work: Path, output: Path, markdown: Path):
    actuals = read_json(work / 'actuals_excel_baseline.json')
    budget_baseline = read_json(work / 'budget_reconciliation.json')
    budget_input = read_json(work / 'budget_payload.json')
    mapping = read_json(work / 'budget_mapping.json')
    bundle = read_json(work / 'ledger_bundle.json')
    checks = []
    representation_mappings = []

    def check(name, actual, expected, source=None):
        item = {'name': name, 'actual': actual, 'expected': expected, 'matched': actual == expected}
        if source is not None:
            item['source'] = source
        checks.append(item)

    def numeric(name, actual, expected, source=None):
        check(name, int(actual) if actual is not None else None,
              int(expected) if expected is not None else None, source)

    source_hashes = {**actuals['source_hashes'], **mapping['source_hashes']}
    source_hashes[str(Path(bundle['source']['path']).resolve())] = bundle['source']['sha256']
    for filename, sha in source_hashes.items():
        path = Path(filename)
        path = path if path.is_absolute() else ROOT / path
        check('unchanged source: ' + path.name, file_hash(path), sha)

    db_path = data_dir / 'finance.sqlite3'
    database_hash_before = file_hash(db_path)
    with readonly(db_path) as db:
        table_names = [r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        table_counts = {name: db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in table_names}
        check('SQLite integrity', db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        txs = transactions(db)
        acc = accounts(db)
        manifests = [json.loads(r['manifest']) for r in db.execute('SELECT manifest FROM migration_runs ORDER BY created_at')]
        check('one migration manifest', len(manifests), 1)
        check('account count', len(acc), len(bundle['accounts']))
        check('historical source rows count', table_counts['source_rows'], len(bundle['sources']))
        check('transaction count', len(txs), actuals['transaction_statistics']['source_row_count'])
        check('history version count', table_counts['transaction_versions'], len(txs))
        check('source identity count', table_counts['source_identity'], len(txs))
        bundle_accounts_by_tx = {t['id']: t['account_id'] for t in bundle['transactions']}
        expected_counts = {'accounts': len(bundle['accounts']), 'artifacts': len(bundle['files'])+6,
                           'imports': len({(s['file_id'], bundle_accounts_by_tx[s['transaction_id']]) for s in bundle['sources']}),
                           'source_rows': len(bundle['sources']), 'transactions': len(bundle['transactions']),
                           'transaction_versions': len(bundle['transactions']), 'source_identity': len(bundle['transactions']),
                           'operations': 0, 'budget_versions': 1, 'migration_runs': 1,
                           'historical_observations': len(bundle['observations']), 'budget_provenance': 1}
        for table, count in expected_counts.items():
            check('application table count ' + table, table_counts.get(table), count)
        for expected_account in bundle['accounts']:
            current_account = acc.get(expected_account['id'])
            expected_fields = {k: expected_account[k] for k in ['name','kind','currency']}
            expected_fields.update(opening_balance=None, opening_date=None)
            check('account identity and unknown opening ' + expected_account['id'],
                  {k: current_account.get(k) for k in expected_fields} if current_account else None, expected_fields)
        check('transaction ID set', sorted(txs), sorted(t['id'] for t in actuals['transaction_statistics']['source_rows']))
        check('scope count', sum(historical.scoped(t) for t in txs.values()), actuals['transaction_statistics']['2026_scope_count'])

        for source in actuals['transaction_statistics']['source_rows']:
            tid = source['id']
            if tid not in txs:
                continue
            current = txs[tid]
            h = current.get('historical', {})
            expected = {k: source[k] for k in ['code', 'raw_amount_minor', 'income_minor', 'expense_minor', 'cash_minor', 'cash_included', 'in_scope']}
            expected['month'] = source['month'] or None
            if source['month'] == '未知':
                expected['month'] = None
                representation_mappings.append({'source': f'交易加工!C{source["excel_row"]}',
                                                'excel': '未知', 'application': None,
                                                'meaning': '未确认日期，继续排除期间统计；不猜日期。'})
            observed = {k: int(h[k]) if h.get(k) is not None else None for k in ['raw_amount_minor', 'income_minor', 'expense_minor', 'cash_minor']}
            observed.update(code=current.get('category_code'), cash_included=int(bool(h.get('cash_included'))),
                            in_scope=int(bool(h.get('in_scope'))), month=current.get('date', '')[:7] if current.get('date') else None)
            check('historical transaction ' + tid, observed, expected, f'交易加工!A{source["excel_row"]}:R{source["excel_row"]}')
            active = bool(source['in_scope'])
            cash = source['cash_minor'] if active and source['cash_included'] else 0
            exp_effect = {'income_minor': source['income_minor'] if active else 0,
                          'expense_minor': source['expense_minor'] if active else 0,
                          'cash_inflow_minor': max(cash, 0), 'cash_outflow_minor': max(-cash, 0)}
            effect = effects(current, acc)
            check('app effects ' + tid, {k: int(effect[k]) for k in exp_effect}, exp_effect)

        reports = {month: report(db, month) for month in actuals['scope_months']}
        for line in actuals['income_expense']['by_category']:
            group = 'income_lines' if line['metric'] == 'income_minor' else 'expense_lines'
            annual = 0
            for month in line['monthly']:
                rows = {r['code']: r for r in reports[month['month']]['income_expense'][group]}
                value = rows.get(line['code'], {}).get('amount_minor', '0')
                numeric(f'income/expense {line["code"]} {month["month"]}', value, month['amount_minor'], month['source'])
                annual += int(value)
            numeric(f'income/expense {line["code"]} annual', annual, line['annual_minor'], line['annual_source'])

        for line in actuals['income_expense']['totals']:
            total = 0
            for month in line['monthly']:
                r = reports[month['month']]
                if line['metric'] == 'mortgage_unsplit_cash_outflow_minor':
                    value = -sum(int(x['amount_minor']) for x in r['cash_flow']['lines'] if x['name'] == '房贷付款（本息未拆）')
                else:
                    value = r['income_expense'][line['metric']]
                numeric(f'actual total {line["metric"]} {month["month"]}', value, month['amount_minor'], month['source'])
                total += int(value)
            numeric(f'actual total {line["metric"]} annual', total, line['annual_minor'], line['annual_source'])

        for line in actuals['cash_flow']['by_type']:
            total = 0
            for month in line['monthly']:
                values = [int(x['amount_minor']) for x in reports[month['month']]['cash_flow']['lines'] if x['name'] == line['name']]
                value = sum(values)
                numeric(f'cash type {line["name"]} {month["month"]}', value, month['amount_minor'], month['source'])
                total += value
            numeric(f'cash type {line["name"]} annual', total, line['annual_minor'], line['annual_source'])
        for month, expected in actuals['cash_flow']['monthly_gross_from_source_rows'].items():
            cash = reports[month]['cash_flow']
            for field in ['inflow_minor', 'outflow_minor', 'net_minor']:
                numeric(f'cash {field} {month}', cash[field], expected[field], '交易加工 O/Q')
        for line in actuals['cash_flow']['net']:
            for month in line['monthly']:
                if line['name'] == '银行现金净变动（含未解释）':
                    value = reports[month['month']]['cash_flow']['net_minor']
                else:
                    value = sum(int(t['historical'].get('cash_minor') or '0') for t in txs.values()
                                if t.get('date', '') and t['date'][:7] == month['month'] and historical.scoped(t) and not t['historical']['cash_included'])
                numeric(f'cash net row {line["name"]} {month["month"]}', value, month['amount_minor'], month['source'])

        observations = historical.observations(db)
        check('observation count', len(observations), len(actuals['balance_observations']))
        indexed = {(o['account'], o['kind']): o for o in observations}
        for expected in actuals['balance_observations']:
            key = (expected['account'], expected['kind'])
            observed = indexed.get(key)
            check('observation present ' + expected['account'], observed is not None, True)
            if observed is None:
                continue
            actual_values = {k: observed.get(k) for k in ['kind', 'account', 'ledger_balance', 'basis', 'limitation']}
            actual_values.update(code=observed.get('code') or None, evidence_date=observed.get('date'), observed_minor=int(observed['amount_minor']) if observed.get('amount_minor') is not None else None)
            expected_values = {k: expected[k] for k in actual_values}
            check('observation unchanged ' + expected['account'], actual_values, expected_values, f'资产负债!A{expected["source_row"]}:H{expected["source_row"]}')
        for month, r in reports.items():
            for field in ['assets_minor', 'liabilities_minor', 'scope_net_minor']:
                check(f'unknown full balance remains unknown {month} {field}', r['balance_sheet'][field], None)

        budget_row = db.execute('SELECT year,version,payload FROM budget_versions WHERE year=2027 ORDER BY version DESC LIMIT 1').fetchone()
        check('budget exists', budget_row is not None, True)
        if budget_row is None:
            raise ValueError('2027 adopted budget missing')
        stored_budget = json.loads(budget_row['payload'])
        check('adopted budget equals Excel-derived payload', stored_budget, budget_input)
        # One compute call produces actual application amounts. Every expected
        # value below is already captured from Excel/legacy source evidence.
        computed = compute_budget(stored_budget)
        computed_months = {m['month']: m for m in computed['months']}
        computed_lines = {l['id']: l for l in computed['line_results']}
        inputs_by_name = {l['name']: l for l in stored_budget['lines']}
        provenance = historical.budget_provenance(db, 2027, budget_row['version'])
        check('budget provenance exists', provenance is not None, True)
        check('budget funding assumptions preserved', provenance['funding_components'], mapping['income_sources'])
        check('budget line notes preserved', provenance['line_provenance'], mapping['lines'])
        check('unmeasured budget items preserved', provenance['unmeasured'], mapping['unmeasured'])
        annual_alias = {'annual_income': 'funding_minor', 'annual_expense_budget': 'planned_minor', 'annual_pool_contribution': 'planned_retention_minor', 'pool_goal': 'target_minor', 'shortfall': 'target_gap_minor'}
        monthly_alias = {'income': 'funding_minor', 'expense_budget': 'planned_minor', 'pool_contribution': 'planned_retention_minor'}
        for baseline in budget_baseline['checks']:
            name = baseline['name']
            if name.startswith('line annual: '):
                line = inputs_by_name[name[len('line annual: '):]]
                actual = computed_lines[line['id']]['annual_planned_minor']
            elif name.startswith('annual '):
                tail = name[len('annual '):]
                key = annual_alias[tail.split(' ', 1)[1]] if tail.startswith(('JSON ', 'DB ')) else tail
                actual = computed['annual'][key]
            elif re.match(r'^2027-\d{2} ', name):
                month, tail = name.split(' ', 1)
                if tail == 'source split':
                    actual = sum(int(s['monthly_minor'][int(month[-2:])-1]) for s in provenance['funding_components'])
                elif tail == 'cumulative':
                    actual = sum(int(m['planned_retention_minor']) for m in computed['months'] if m['month'] <= month)
                elif tail == 'fixed scope':
                    actual = computed_months[month]['fixed_minor']
                else:
                    key = monthly_alias[tail[5:]] if tail.startswith('JSON ') else tail
                    actual = computed_months[month][key]
            else:
                match = re.match(r'^(.*) month (\d+)$', name)
                if match:
                    group, index = match.group(1), int(match.group(2))-1
                    actual = sum(int(l['monthly_minor'][index]) for l in stored_budget['lines'] if l['group'] == group)
                elif name.endswith(' annual'):
                    group = name[:-len(' annual')]
                    actual = sum(int(computed_lines[l['id']]['annual_planned_minor']) for l in stored_budget['lines'] if l['group'] == group)
                else:
                    raise ValueError('Unknown independent budget check: ' + name)
            numeric('budget Excel: ' + name, actual, baseline['expected_minor'], baseline.get('source_cell'))
        _, projected_meta = budget_projection(db, stored_budget)
        check('2027 actuals not manufactured', projected_meta['actuals_connected'], False)
        check('no 2027 actual transactions from budget', sum(bool(t.get('date')) and t['date'].startswith('2027-') for t in txs.values()), 0)

        artifact_rows = {r['sha256']: dict(r) for r in db.execute('SELECT * FROM artifacts')}
        expected_originals = {f['sha256']: f for f in bundle['files']}
        check('40 original evidence files in bundle', len(expected_originals), 40)
        for sha, item in expected_originals.items():
            check('original registered ' + item['filename'], sha in artifact_rows, True)
        for sha, item in artifact_rows.items():
            path = data_dir / 'originals' / sha
            check('archive exists ' + item['filename'], path.is_file(), True)
            if path.is_file():
                check('archive SHA256 ' + item['filename'], file_hash(path), sha)
                check('archive byte size ' + item['filename'], path.stat().st_size, item['size'])
        for path in [Path(actuals['source_workbook']), Path(mapping['source_workbook']), Path(bundle['source']['path']), work/'ledger_bundle.json']:
            check('supporting archive ' + path.name, file_hash(path) in artifact_rows, True)

        archived_db_path = data_dir / 'originals' / bundle['source']['sha256']
        with readonly(archived_db_path) as archive:
            for name, expected in bundle['source']['table_counts'].items():
                count = archive.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                check('full legacy archive table ' + name, count, expected)
        stored_sources = {r['id']: dict(r) for r in db.execute('SELECT * FROM source_rows')}
        imported_files = {r['id']: r['sha256'] for r in db.execute('SELECT id,sha256 FROM imports')}
        bundle_files = {f['file_id']: f for f in bundle['files']}
        for source in bundle['sources']:
            record = stored_sources.get(source['id'])
            check('source evidence present ' + source['id'], record is not None, True)
            if record is None:
                continue
            check('source transaction relation ' + source['id'], record['transaction_id'], source['transaction_id'])
            raw = json.loads(record['raw'])
            check('original evidence fields ' + source['id'], {k: raw.get(k) for k in source['raw']}, source['raw'])
            check('source physical archive ' + source['id'], imported_files[record['import_id']], bundle_files[source['file_id']]['sha256'])

        headline = {
            'actuals_scope': '2026-01至2026-10已捕获范围；10月不完整',
            'actuals': {key: str(sum(int(r['income_expense'][key]) for r in reports.values())) for key in ['income_minor', 'expense_minor', 'surplus_minor']},
            'cash_net_minor': str(sum(int(r['cash_flow']['net_minor']) for r in reports.values())),
            'actual_months': [{'month': month, **{k: r['income_expense'][k] for k in ['income_minor', 'expense_minor', 'surplus_minor']}, 'cash_net_minor': r['cash_flow']['net_minor']} for month, r in reports.items()],
            'budget_2027': computed['annual'], 'budget_months': computed['months'],
            'source_transaction_count': len(txs), 'scoped_transaction_count': sum(historical.scoped(t) for t in txs.values()),
            'source_evidence_count': len(stored_sources), 'original_file_count': len(expected_originals),
            'artifact_count': len(artifact_rows), 'observation_count': len(observations), 'account_count': len(acc),
            'budget_line_count': len(stored_budget['lines']), 'budget_source_version': mapping['source_version'],
        }
        revision = db.execute("SELECT value FROM metadata WHERE key='data_revision'").fetchone()[0]

    check('read-only app database unchanged', file_hash(db_path), database_hash_before)
    for filename, sha in source_hashes.items():
        path = Path(filename)
        path = path if path.is_absolute() else ROOT / path
        check('source still unchanged after comparison: ' + path.name, file_hash(path), sha)
    # Preserve full mismatches, compact large matched identities/records.
    for item in checks:
        if item['matched'] and isinstance(item['actual'], (dict, list)):
            encoded = json.dumps(item['actual'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if len(encoded) > 600:
                item['actual'] = item['expected'] = {'entry_count': len(item['actual']), 'sha256': hashlib.sha256(encoded.encode()).hexdigest()}
    result = {
        'status': 'matched' if all(c['matched'] for c in checks) else 'differences',
        'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'database': str(db_path.resolve()), 'data_revision': revision,
        'read_only': True, 'check_count': len(checks), 'headline': headline,
        'source_workbooks': {'actuals': actuals['source_workbook'], 'budget': mapping['source_workbook']},
        'source_hashes': source_hashes, 'table_counts': table_counts,
        'representation_mappings': representation_mappings,
        'differences': [c for c in checks if not c['matched']], 'checks': checks,
        'limitations': [
            '核对沿用已整理的收入、费用和现金解释，未重新判断历史用途或证明资料完整。应用支持人工修改历史解释并保留版本；本脚本只读核对迁移基线。',
            '2026年资料仍为部分覆盖，10月不完整；收支结余不等于可存款额。',
            '12项外部余额观察日期不同，未知账面余额保持未知，不汇总家庭净资产。',
            '2027年为预算计划，尚无实际支出；工资和可提取公积金假设保持分列。',
            '此次只读核对未修改原Excel、旧数据库或应用数据库。',
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    write_markdown(result, markdown)
    return result


def write_markdown(result, path):
    h = result['headline']
    status = '一致' if result['status'] == 'matched' else '存在差异'
    lines = [
        '# 账本迁移核对报告', '',
        f'核对时间：{result["checked_at"]}。核对结果：**{status}**。', '',
        f'对应用数据库的 {result["check_count"]:,} 项核对中，发现 {len(result["differences"])} 项差异。金额均按人民币分比较，Excel 的二进制浮点尾差不作为金额变化。', '',
        f'核对数据库：`{result["database"]}`。本报告对应数据修订 {result["data_revision"]}。', '',
        '## 迁移范围', '',
        f'- {h["account_count"]} 个账户、{h["source_transaction_count"]:,} 条来源交易；其中 {h["scoped_transaction_count"]:,} 条纳入 2026 年范围，其余作为背景与待补日期证据保留。',
        f'- {h["source_evidence_count"]:,} 条来源证据、{h["original_file_count"]} 份原件，以及完整旧数据库和核对工作簿，共 {h["artifact_count"]} 份归档文件；逐份核对 SHA-256。',
        f'- {h["observation_count"]} 项账户／负债时点观察，均保留原证据日期与限制。',
        f'- 2027 年预算 {h["budget_line_count"]} 个项目、12 个月；采用 `{h["budget_source_version"]}`。', '',
        '## 2026 年实际汇总', '',
        '范围为已捕获的 1—10 月；10 月不完整。', '',
        '| 指标 | 应用金额 | 与 Excel 差额 |', '|---|---:|---:|',
    ]
    for label, key in [('已识别收入', 'income_minor'), ('已识别费用', 'expense_minor'), ('已识别收支结余', 'surplus_minor')]:
        lines.append(f'| {label} | {money(h["actuals"][key])} | {"0.00" if result["status"] == "matched" else "见差异清单"} |')
    lines.append(f'| 银行现金净变动（含未解释） | {money(h["cash_net_minor"])} | {"0.00" if result["status"] == "matched" else "见差异清单"} |')
    lines += ['', '| 月份 | 收入 | 费用 | 收支结余 | 银行现金净变动 |', '|---|---:|---:|---:|---:|']
    for m in h['actual_months']:
        lines.append(f'| {m["month"]} | {money(m["income_minor"])} | {money(m["expense_minor"])} | {money(m["surplus_minor"])} | {money(m["cash_net_minor"])} |')
    lines += ['', '逐月、逐科目、现金业务类型及逐笔金额作用均与 Excel 比较。原始金额保留来源符号，不直接汇总为收入或费用。还款、本人互转、证券划转、代管和过桥资金继续沿用已确认口径。', '', '## 2027 年预算', '', '| 指标 | 金额 |', '|---|---:|']
    for label, key in [('全年可用资金', 'funding_minor'), ('全年预算支出', 'planned_minor'), ('预计留存', 'planned_retention_minor'), ('留存挑战目标', 'target_minor'), ('距挑战目标差额', 'target_gap_minor')]:
        lines.append(f'| {label} | {money(h["budget_2027"][key])} |')
    lines += ['', '| 月份 | 可用资金 | 预算支出 | 预计留存 |', '|---|---:|---:|---:|']
    for m in h['budget_months']:
        lines.append(f'| {m["month"]} | {money(m["funding_minor"])} | {money(m["planned_minor"])} | {money(m["planned_retention_minor"])} |')
    lines += ['', '预算逐行、逐月、用途小计及年度汇总沿用最终 Excel，未重新预测或修改额度。可用资金包含税后工资及可提取公积金假设；不把公积金称为工资或已到账收入。2027 年暂无实际交易，不能把预算额度显示成已经花费或已核实的零消费。', '', '## 保留的限制', '']
    lines += ['- ' + x for x in result['limitations']]
    lines += ['', '## 来源', '', f'- 实际账本：[{Path(result["source_workbooks"]["actuals"]).name}](<{result["source_workbooks"]["actuals"]}>)。', f'- 最终预算：[{Path(result["source_workbooks"]["budget"]).name}](<{result["source_workbooks"]["budget"]}>)。', '', '完整逐项检查及差异保存在本次迁移的 JSON 核对记录中。']
    if result['differences']:
        lines += ['', '## 差异清单', '']
        for diff in result['differences']:
            lines.append(f'- {diff["name"]}：应用 {diff["actual"]}；源值 {diff["expected"]}。')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='只读核对迁移后的应用账本与已保存的 Excel 来源基线')
    parser.add_argument('--data-dir', type=Path, default=ROOT/'data/app')
    parser.add_argument('--work', type=Path, default=ROOT/'work/migration-20261029')
    parser.add_argument('--output', type=Path, default=ROOT/'work/migration-20261029/migration_reconciliation.json')
    parser.add_argument('--report', type=Path, default=ROOT/'outputs/01a17707-cdd1-73f3-904b-f6ba88bd485e/账本迁移核对报告_2026-10-29.md')
    args = parser.parse_args()
    result = reconcile(args.data_dir.resolve(), args.work.resolve(), args.output.resolve(), args.report.resolve())
    print(json.dumps({'status': result['status'], 'check_count': result['check_count'], 'difference_count': len(result['differences']), 'output': str(args.output.resolve()), 'report': str(args.report.resolve())}, ensure_ascii=False))
    sys.exit(0 if result['status'] == 'matched' else 1)
