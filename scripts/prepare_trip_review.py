"""Build travel review sheet specifications from already reviewed event IDs.

This module performs no file or network I/O.  It does not select transactions,
infer their purpose, or change their accounting.  The caller supplies the review
decisions and appends the returned sheets/checks to the existing workbook payload.
All amounts in ``stats`` come from signed integer ``expense_minor`` facts; the
workbook retrieves the corresponding RMB amounts from 交易加工 column N.
"""

from collections.abc import Mapping
from datetime import date, datetime
from math import ceil
from unicodedata import east_asian_width

if __package__:
    from .process_finance import COA
else:
    from process_finance import COA


MONEY = '#,##0.00;(#,##0.00);"–"'
INTEGER = '#,##0;(#,##0);"–"'
DATE = 'yyyy-mm-dd'
FIRST_DATA_ROW = 5
DETAIL_NAME = '旅行明细'
SUMMARY_NAME = '旅行汇总'


def _date(value, field, *, allow_missing=False):
    if value is None or value == '':
        if allow_missing:
            return None
        raise ValueError(f'{field} is required')
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f'{field} must be an ISO date')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f'{field} must be an ISO date') from None


def _serial(value):
    return (value - date(1899, 12, 30)).days if value is not None else None


def _text(value, field, *, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ValueError(f'{field} must be a nonempty string' if nonempty
                         else f'{field} must be a string')
    return value


def _formula(expression):
    return {'formula': expression}


def _exact_criterion(cell):
    # SUMIFS/COUNTIFS treat *, ? and ~ as pattern syntax, even in a cell value.
    # Escaping them keeps user-supplied IDs and group names exact match keys.
    escaped = f'SUBSTITUTE(SUBSTITUTE(SUBSTITUTE({cell},"~","~~"),"*","~*"),"?","~?")'
    return f'"="&{escaped}'


def _height(text, width, minimum=38):
    lines = 0
    for line in text.split('\n'):
        units = sum(2 if east_asian_width(char) in ('W', 'F') else 1 for char in line)
        lines += max(1, ceil(units / max(1, width - 2)))
    return max(minimum, lines * 15 + 10)


def build_trip_review(events, config, txn_last_row):
    """Return ``(sheets, checks, stats)`` without accessing private files.

    ``events`` contains the existing processed transaction dictionaries. Each ID
    must be unique. ``config`` requires title, start, end_inclusive, entries, and
    notes. Entries are ``{id, included: bool, group, status, basis}``; ``False``
    means a pending candidate, not a rejected transaction. Only listed IDs are
    displayed. Dates never filter these IDs, so prepayments and later refunds
    remain attached to the review. ``txn_last_row`` is the final populated Excel
    row of the existing 交易加工 sheet, whose data begins on row 5.

    Stats include signed minor-unit controls alongside RMB values. Verification
    checks cover every detail amount, each included/pending group, both totals,
    inclusive trip days, and the average per trip day. No input is mutated.
    """
    if not isinstance(config, Mapping):
        raise ValueError('config must be a mapping')
    title = _text(config.get('title'), 'title', nonempty=True)
    start = _date(config.get('start'), 'start')
    end = _date(config.get('end_inclusive'), 'end_inclusive')
    if end < start:
        raise ValueError('end_inclusive must not precede start')
    if isinstance(txn_last_row, bool) or not isinstance(txn_last_row, int) or not 4 <= txn_last_row <= 1048576:
        raise ValueError('txn_last_row must be a valid last Excel row')
    entries = config.get('entries')
    notes = config.get('notes')
    if not isinstance(entries, (list, tuple)):
        raise ValueError('entries must be a list')
    if not isinstance(notes, (list, tuple)):
        raise ValueError('notes must be a list of strings')
    for note in notes:
        _text(note, 'note')

    by_id = {}
    for event in events:
        if not isinstance(event, Mapping):
            raise ValueError('events must contain mappings')
        event_id = _text(event.get('id'), 'event id', nonempty=True)
        if event_id in by_id:
            raise ValueError('event IDs must be unique')
        by_id[event_id] = event
    if entries and txn_last_row < FIRST_DATA_ROW:
        raise ValueError('review entries require populated transaction rows')

    selected = []
    seen = set()
    groups = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError('entries must contain mappings')
        event_id = _text(entry.get('id'), 'entry id', nonempty=True)
        if event_id in seen:
            raise ValueError('review entry IDs must be unique')
        if event_id not in by_id:
            raise ValueError('review entry ID was not found in events')
        seen.add(event_id)
        if not isinstance(entry.get('included'), bool):
            raise ValueError('included must be a boolean')
        group = _text(entry.get('group'), 'group', nonempty=True)
        status = _text(entry.get('status'), 'status', nonempty=True)
        basis = _text(entry.get('basis'), 'basis', nonempty=True)
        event = by_id[event_id]
        amount = event.get('expense_minor')
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise ValueError('expense_minor must be signed integer cents')
        event_date = _date(event.get('date'), 'event date', allow_missing=True)
        description = _text(event.get('description'), 'event description')
        selected.append((event_date, event_id, event, entry, description, status, basis))
        control = groups.setdefault(group, {
            'included_count': 0, 'included_minor': 0,
            'pending_count': 0, 'pending_minor': 0,
        })
        kind = 'included' if entry['included'] else 'pending'
        control[f'{kind}_count'] += 1
        control[f'{kind}_minor'] += amount

    # Unknown dates sort last. No itinerary cutoff removes reviewed prepayments.
    selected.sort(key=lambda item: (item[0] or date.max, item[1]))
    detail_rows = []
    checks = []
    detail_heights = {}
    txn_end = max(FIRST_DATA_ROW, txn_last_row)
    amount_range = f"'交易加工'!$N$5:$N${txn_end}"
    id_range = f"'交易加工'!$A$5:$A${txn_end}"
    for index, (event_date, event_id, event, entry, description, status, basis) in enumerate(selected, FIRST_DATA_ROW):
        code = event.get('code')
        account = COA.get(code, str(code) if code is not None else '')
        detail_rows.append([
            _serial(event_date), description,
            _formula(f'=SUMIFS({amount_range},{id_range},{_exact_criterion(f"$I{index}")})'),
            entry['group'], '是' if entry['included'] else '待核', status,
            basis, account, event_id,
        ])
        detail_heights[str(index)] = max(
            _height(description, 54), _height(entry['group'], 21),
            _height(status, 25), _height(basis, 68), _height(account, 24),
        )
        checks.append({'sheet': DETAIL_NAME, 'cell': f'C{index}',
                       'expectedNumber': event['expense_minor'] / 100, 'tolerance': 0.000001})

    detail = {
        'name': DETAIL_NAME,
        'title': f'{title}明细',
        'subtitle': '金额为人民币银行实扣口径，按业务ID引用“交易加工”N列费用；负数保留退款方向。按原交易日期排序，保留行程前预付款；待核候选不计入合计。',
        'headers': ['日期', '商户/摘要', '金额（人民币）', '旅行分组', '是否纳入', '依据状态', '判断依据', '科目', '业务ID'],
        'rows': detail_rows,
        'role': 'output', 'table': True, 'freezeRows': 4, 'freezeColumns': 2,
        'columnWidths': [15, 54, 19, 21, 13, 25, 68, 24, 28],
        'numberFormats': {'A': DATE, 'C': MONEY},
        'textColumns': ['B', 'D', 'E', 'F', 'G', 'H', 'I'],
        'wrapColumns': ['B', 'D', 'F', 'G', 'H'],
        'rowHeights': detail_heights, 'headerHeight': 36, 'subtitleHeight': 42,
        'renderRange': f'A1:H{min(max(len(detail_rows) + 4, 5), 18)}',
    }

    days = (end - start).days + 1
    stats = {
        'included_count': sum(group['included_count'] for group in groups.values()),
        'included_minor': sum(group['included_minor'] for group in groups.values()),
        'pending_count': sum(group['pending_count'] for group in groups.values()),
        'pending_minor': sum(group['pending_minor'] for group in groups.values()),
        'days': days,
        'groups': groups,
    }
    for control in [stats, *groups.values()]:
        control['included_amount'] = control['included_minor'] / 100
        control['pending_amount'] = control['pending_minor'] / 100
    stats['average_daily_amount'] = stats['included_amount'] / days

    detail_end = max(FIRST_DATA_ROW, len(detail_rows) + 4)
    included_range = f"'{DETAIL_NAME}'!$E$5:$E${detail_end}"
    group_range = f"'{DETAIL_NAME}'!$D$5:$D${detail_end}"
    sum_range = f"'{DETAIL_NAME}'!$C$5:$C${detail_end}"

    def total_formula(kind):
        return _formula(f'=SUMIFS({sum_range},{included_range},"{kind}")')

    def count_formula(kind):
        return _formula(f'=COUNTIFS({included_range},"{kind}")')

    summary_rows = [
        ['已纳入旅行花费合计', total_formula('是'), '元', count_formula('是'),
         '只汇总明细“是否纳入”为“是”的记录；负数退款已按原方向扣减。'],
        ['平均每日', _formula('=B5/B10'), '元/天', None,
         '已纳入旅行花费合计÷行程天数；包含已纳入的机票、购物和预付款，不代表每日日常消费。'],
        ['待核候选金额', total_formula('待核'), '元', count_formula('待核'),
         '单独展示，未计入已纳入合计和平均每日；是否属于本次旅行仍需确认。'],
        ['行程开始', _serial(start), '日期', None, '按提供的行程起始日。'],
        ['行程结束', _serial(end), '日期', None, '按提供的行程结束日，首尾两天均计入。'],
        ['行程天数', _formula('=B9-B8+1'), '天', None, '结束日期－开始日期＋1。'],
        ['已纳入旅行分组', None, None, None, '各分组只汇总明细“是否纳入”为“是”的记录。'],
    ]
    styles = {'5': 'total', '6': 'ratio', '7': 'subtotal', '11': 'section'}
    cell_formats = {'B8': DATE, 'B9': DATE, 'B10': INTEGER}

    def append_group(group, kind, control_key):
        row = len(summary_rows) + FIRST_DATA_ROW
        criterion = _exact_criterion(f'$A{row}')
        summary_rows.append([
            group,
            _formula(f'=SUMIFS({sum_range},{group_range},{criterion},{included_range},"{kind}")'),
            '元',
            _formula(f'=COUNTIFS({group_range},{criterion},{included_range},"{kind}")'),
            '已纳入旅行花费。' if kind == '是' else '待核候选，未计入旅行花费合计。',
        ])
        checks.extend([
            {'sheet': SUMMARY_NAME, 'cell': f'B{row}',
             'expectedNumber': groups[group][f'{control_key}_amount'], 'tolerance': 0.000001},
            {'sheet': SUMMARY_NAME, 'cell': f'D{row}',
             'expectedNumber': groups[group][f'{control_key}_count'], 'tolerance': 0},
        ])

    for group, control in groups.items():
        if control['included_count']:
            append_group(group, '是', 'included')
    if not stats['included_count']:
        summary_rows.append(['暂无已纳入记录', None, None, None, '已纳入合计为0；待核候选仍单列。'])

    section_row = len(summary_rows) + FIRST_DATA_ROW
    styles[str(section_row)] = 'section'
    summary_rows.append(['待核候选分组', None, None, None, '待核候选另列，不计入上方已纳入旅行花费。'])
    for group, control in groups.items():
        if control['pending_count']:
            append_group(group, '待核', 'pending')
    if not stats['pending_count']:
        summary_rows.append(['暂无待核候选', None, None, None, '本次提供的审核记录中没有待核候选。'])

    section_row = len(summary_rows) + FIRST_DATA_ROW
    styles[str(section_row)] = 'section'
    summary_rows.append(['统计口径与说明', None, None, None, '以下边界适用于合计与平均每日。'])
    explanations = [
        ('金额口径', '按原记录中的人民币银行实扣费用汇总；直接引用“交易加工”N列，不换算原币，也不重复计算信用卡还款。'),
        ('分摊口径', '这是已纳入记录的旅行花费及按行程天数计算的日均，不是人均；未扣除他人分摊。'),
        ('数据覆盖', '仅包含已提供并列入本次审核的交易；未捕获的现金、线下支付及其他缺失记录不在合计内。'),
        ('交易日期', '保留原交易日期；行程前预付款仍可纳入，是否计入以逐笔审核结论为准。'),
    ]
    explanations.extend((f'补充说明{index}', note) for index, note in enumerate(notes, 1))
    for label, explanation in explanations:
        styles[str(len(summary_rows) + FIRST_DATA_ROW)] = 'note'
        summary_rows.append([label, None, None, None, explanation])

    summary_heights = {
        str(index): max(_height(row[0], 32), _height(row[4] or '', 100))
        for index, row in enumerate(summary_rows, FIRST_DATA_ROW)
    }
    summary = {
        'name': SUMMARY_NAME, 'title': f'{title}花费汇总',
        'subtitle': f'行程：{start.isoformat()}至{end.isoformat()}，含首尾两天。金额单位为人民币元；合计、待核候选和平均每日均由公式计算。',
        'headers': ['指标/旅行分组', '金额或数值', '单位', '笔数', '说明与限制'],
        'rows': summary_rows, 'role': 'output', 'table': False, 'freezeRows': 4,
        'columnWidths': [32, 22, 12, 12, 100],
        'numberFormats': {'B': MONEY, 'D': INTEGER}, 'cellFormats': cell_formats,
        'textColumns': ['A', 'C', 'E'], 'wrapColumns': ['A', 'E'],
        'rowStyles': styles, 'rowHeights': summary_heights,
        'headerHeight': 34, 'subtitleHeight': 36,
        'renderRange': f'A1:E{len(summary_rows) + 4}',
    }
    checks.extend([
        {'sheet': SUMMARY_NAME, 'cell': 'B5', 'expectedNumber': stats['included_amount'], 'tolerance': 0.000001},
        {'sheet': SUMMARY_NAME, 'cell': 'D5', 'expectedNumber': stats['included_count'], 'tolerance': 0},
        {'sheet': SUMMARY_NAME, 'cell': 'B6', 'expectedNumber': stats['average_daily_amount'], 'tolerance': 0.000001},
        {'sheet': SUMMARY_NAME, 'cell': 'B7', 'expectedNumber': stats['pending_amount'], 'tolerance': 0.000001},
        {'sheet': SUMMARY_NAME, 'cell': 'D7', 'expectedNumber': stats['pending_count'], 'tolerance': 0},
        {'sheet': SUMMARY_NAME, 'cell': 'B10', 'expectedNumber': days, 'tolerance': 0},
    ])
    return [summary, detail], checks, stats
