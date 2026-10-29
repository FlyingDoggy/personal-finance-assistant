"""Build formula-linked spending review sheets without reading private files.

The caller supplies interpreted events, the active budget assumptions, and any
transaction annotations. These review sheets do not modify transactions or the
active budget. All amounts supplied in monthly caps and savings are in yuan;
transaction and reimbursement ``*_minor`` amounts are integer cents.
"""

from datetime import date
import math

from budget_policy import select_budget_window
from process_finance import COA, EXPENSES, rounded
from spend_analysis import large_expense_records


MONEY = '#,##0.00;(#,##0.00);"–"'


def _formula(expression):
    return {"formula": expression}


def _sheet(name, title, subtitle, headers, rows, **options):
    return {
        "name": name, "title": title, "subtitle": subtitle,
        "headers": headers, "rows": rows, "role": "output",
        "table": False, "freezeRows": 4, **options,
    }


def _amount(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TypeError(f"{name} must be a finite number")
    return value


def _interpretation(event, record, annotation):
    if record["net_minor"] == 0:
        judgment = "已全额退款，不计新增节省"
        treatment = "退款已冲减费用；不再计一笔节省"
        action = "保留原消费及退款证据，无需重复压减预算"
    elif event.get("reimb") == "用户确认可报销":
        judgment = "工作消费，不重复计算个人节省"
        treatment = "报销在预算汇总抵减，不能再逐笔扣除"
        action = "核对报销状态与回款时间，不把待报销金额当作节约"
    elif event.get("behavior") == "冲动消费" and event.get("base_minor") == 0:
        judgment = "已排除复购，已反映在当前预算"
        treatment = "已从可变消费基数剔除，新增节省为零"
        action = "避免再次购买；不把历史一次性金额再加到节省方案"
    elif event.get("event") == "车辆租赁付款":
        judgment = "固定承诺，按合同期限安排"
        treatment = "固定安排单列，不能再压减可变预算"
        action = "核实合同到期与后续出行方案后再比较替代成本"
    elif event.get("base_minor") == 0:
        judgment = "未进入当前可变基数，需结合安排判断"
        treatment = "已从可变基数剔除；核对独立固定安排"
        action = "以仍在未来预算中的费用为节省上限"
    else:
        judgment = "可变消费，是否可减少需结合用途确认"
        treatment = "已进入当前可变预算，建议仅计算相对草案的差额"
        action = "结合使用频率和替代方案决定后续额度"

    # User-supplied explanations remain visible; a full refund must still lead
    # with its zero additional-saving treatment even when an annotation exists.
    if annotation.get("judgment"):
        judgment = (
            judgment + "；" + annotation["judgment"]
            if record["net_minor"] == 0 else annotation["judgment"]
        )
    return judgment, treatment, annotation.get("action") or action


def build_spend_review(events, config, txn_last_row, months, current_savings, cash_growth):
    """Return ``(sheet_specs, numeric_checks, stats)`` for three appended sheets.

    Required config: start, end_exclusive and budget_multiplier. Optional keys:
    threshold_minor, latest_observed_date, annotations, proposal_monthly_caps,
    reimbursement_minor and savings_target. The optional target adds independent
    numerical checks for the target-gap rows; those rows always link to the
    existing target sheet. Existing category rows are mapped by EXPENSES order.
    """
    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 12:
        raise ValueError("months must be an integer from 1 to 12")
    if isinstance(txn_last_row, bool) or not isinstance(txn_last_row, int) or txn_last_row < 4:
        raise ValueError("txn_last_row must be an integer of at least 4")
    multiplier = _amount(config["budget_multiplier"], "budget_multiplier")
    if multiplier < 0:
        raise ValueError("budget_multiplier must be nonnegative")
    _amount(current_savings, "current_savings")
    _amount(cash_growth, "cash_growth")
    events = list(events)
    start, end = config["start"], config["end_exclusive"]
    threshold = config.get("threshold_minor", 200000)
    large = large_expense_records(events, start, end, threshold)
    window = select_budget_window(events, start, end)
    annotations = config.get("annotations", {})
    caps = config.get("proposal_monthly_caps", {})
    unknown_caps = set(caps) - {str(code) for code in EXPENSES}
    if unknown_caps:
        raise ValueError("proposal_monthly_caps contains an unknown expense code")
    for cap in caps.values():
        if _amount(cap, "monthly cap") < 0:
            raise ValueError("monthly caps must be nonnegative")
    reimbursement = config.get(
        "reimbursement_minor",
        sum(e.get("income_minor", 0) for e in window if e.get("event") == "报销回款"),
    )
    if isinstance(reimbursement, bool) or not isinstance(reimbursement, int) or reimbursement < 0:
        raise ValueError("reimbursement_minor must be a nonnegative integer")

    last = max(5, txn_last_row)
    txn = lambda column: f"'交易加工'!${column}$5:${column}${last}"
    start_excel = ",".join(str(int(part)) for part in start.split("-"))
    end_excel = ",".join(str(int(part)) for part in end.split("-"))
    dates = f',{txn("B")},">="&DATE({start_excel}),{txn("B")},"<"&DATE({end_excel})'
    latest = config.get("latest_observed_date") or "未指定"
    scope = f"{start}至{end}（不含终日）；观察截止{latest}，月均采用{months}个月共同分母"
    checks = []

    def check(sheet, cell, value, tolerance=.0001):
        checks.append({"sheet": sheet, "cell": cell, "expectedNumber": value, "tolerance": tolerance})

    large_rows = []
    for index, record in enumerate(large):
        row = index + 5
        event = record["event"]
        annotation = annotations.get(event["id"], {})
        judgment, treatment, action = _interpretation(event, record, annotation)
        source = "；".join(str(value) for value in (
            annotation.get("basis"), event.get("source"), event.get("locator"),
            event.get("category_evidence"),
        ) if value)
        large_rows.append([
            index + 1, (date.fromisoformat(event["date"]) - date(1899, 12, 30)).days,
            annotation.get("display_name") or event.get("product_description") or event.get("description") or event["id"],
            _formula(f'=SUMIFS({txn("N")},{txn("A")},$K{row})'),
            _formula(f'=-SUMIFS({txn("N")},{txn("T")},$K{row},{txn("U")},"退款对应原消费",{txn("N")},"<0"{dates})'),
            _formula(f"=D{row}-E{row}"), COA.get(event.get("code"), "未分类支出"),
            judgment, treatment, action, event["id"], "、".join(record["refund_ids"]), source,
        ])
        for column, key in (("D", "gross_minor"), ("E", "refund_minor"), ("F", "net_minor")):
            check("大额消费", f"{column}{row}", record[key] / 100)
    large_last = len(large_rows) + 4
    large_sheet = _sheet(
        "大额消费", f"单笔消费复核｜原消费不少于{threshold / 100:,.2f}元",
        scope + "。按原消费金额筛选；仅扣窗口内已关联退款，全退仍保留。退款、还款和本金不是新消费。",
        ["序号", "日期", "项目", "原消费", "关联退款", "净费用", "科目", "节省判断", "预算处理", "建议", "业务ID", "退款IDs", "来源说明"],
        large_rows, table=True, freezeColumns=3,
        columnWidths=[9, 14, 48, 17, 17, 17, 23, 50, 56, 66, 27, 42, 76],
        numberFormats={"B": "yyyy-mm-dd", "D": MONEY, "E": MONEY, "F": MONEY},
        wrapColumns=["C", "H", "I", "J", "L", "M"], sourceColumns=["K", "L", "M"],
        rowHeights={str(row): 76 for row in range(5, large_last + 1)},
        renderRange=f"A1:J{min(large_last, 12)}", subtitleHeight=55,
    )

    budget_row = {code: index + 5 for index, code in enumerate(EXPENSES)}
    historical = {code: sum(e.get("expense_minor", 0) for e in window if e.get("code") == code) for code in EXPENSES}
    bases = {code: sum(e.get("base_minor", 0) for e in window if e.get("code") == code) for code in EXPENSES}
    current_monthly = {
        code: rounded(bases[code] / 100 / months * 12 * multiplier) / 12 for code in EXPENSES
    }
    reimbursement_monthly = -rounded(reimbursement / 100 / months * 12 * multiplier) / 12
    analysis_rows = []
    for index, code in enumerate(sorted(EXPENSES, key=lambda value: (-bases[value], value))):
        row = index + 5
        analysis_rows.append([
            str(code), EXPENSES[code],
            _formula(f'=SUMIFS({txn("N")},{txn("H")},$A{row}{dates})'),
            _formula(f'=SUMIFS({txn("X")},{txn("H")},$A{row}{dates})'),
            _formula(f"='2027预算'!F{budget_row[code]}/12"),
            "类别含工作消费；报销仅在汇总行抵减，未分摊至类别",
        ])
        for column, value in (("C", historical[code] / 100), ("D", bases[code] / 100), ("E", current_monthly[code])):
            check("消费分析", f"{column}{row}", value)
    analysis_rows += [
        ["", "费用类别小计", _formula("=SUM(C5:C20)"), _formula("=SUM(D5:D20)"), _formula("=SUM(E5:E20)"), "费用类别含融资利息等其他费用，大额清单仅列消费及车辆租赁付款"],
        ["调整", "报销估计调整", _formula(f'=-SUMIFS({txn("M")},{txn("G")},"报销回款"{dates})'), _formula("=C22"), _formula("='2027预算'!F21/12"), "同一窗口已到账报销仅调整一次；不代表逐笔工作费用已经精确剔除"],
        ["", "调整后合计 / 净月均", _formula("=SUM(C21:C22)"), _formula("=SUM(D21:D22)"), _formula("=SUM(E21:E22)"), "月均只反映当前可变预算；固定费用另见固定安排"],
        [None] * 6,
        ["", "大额消费概览", None, None, None, "原额门槛与净额门槛分列，全退不隐藏"],
    ]
    metrics = [
        ("原消费笔数", f"=COUNT('大额消费'!A5:A{large_last})", len(large)),
        ("原消费总额", f"=SUM('大额消费'!D5:D{large_last})", sum(r["gross_minor"] for r in large) / 100),
        ("窗口内关联退款", f"=SUM('大额消费'!E5:E{large_last})", sum(r["refund_minor"] for r in large) / 100),
        ("退款后净费用", f"=SUM('大额消费'!F5:F{large_last})", sum(r["net_minor"] for r in large) / 100),
        ("全额退款笔数", f'=COUNTIF(\'大额消费\'!F5:F{large_last},0)', sum(r["net_minor"] == 0 for r in large)),
        ("净额仍达门槛笔数", f'=COUNTIF(\'大额消费\'!F5:F{large_last},">="&{threshold / 100})', sum(r["net_minor"] >= threshold for r in large)),
    ]
    for row, (label, formula, expected) in enumerate(metrics, 26):
        analysis_rows.append(["", label, _formula(formula if large else "=0"), None, None, "仅对本表大额清单汇总，不作为额外费用或节省入账"])
        check("消费分析", f"C{row}", expected)
    analysis_rows.append(["", "观察范围", scope, None, None, "未捕获交易不视为零；累计净费用仍以原交易发生日期统计"])
    for column, values in (("C", historical), ("D", bases)):
        total = sum(values.values()) / 100
        check("消费分析", f"{column}21", total)
        check("消费分析", f"{column}22", -reimbursement / 100)
        check("消费分析", f"{column}23", total - reimbursement / 100)
    current_gross_monthly = sum(current_monthly.values())
    current_net_monthly = current_gross_monthly + reimbursement_monthly
    for row, value in ((21, current_gross_monthly), (22, reimbursement_monthly), (23, current_net_monthly)):
        check("消费分析", f"E{row}", value)
    analysis_sheet = _sheet(
        "消费分析", "消费大头与当前预算", scope + "。按可变基数降序；历史净费保留真实退款，类别含工作消费，报销在独立行抵减。",
        ["编码", "费用类别 / 指标", "历史净费 / 指标值", "可变消费基数", "当前月均预算", "口径说明"], analysis_rows,
        columnWidths=[11, 31, 27, 23, 23, 96], numberFormats={"C": MONEY, "D": MONEY, "E": MONEY},
        cellFormats={"C26": "0", "C30": "0", "C31": "0"}, wrapColumns=["B", "C", "F"],
        rowStyles={"21": "subtotal", "22": "note", "23": "total", "25": "section"},
        rowHeights={str(row): 43 for row in range(5, 33)}, renderRange="A1:F23", subtitleHeight=55,
    )

    proposal_rows = []
    proposal_monthly = {}
    editable_cells = []
    for index, code in enumerate(EXPENSES):
        row = index + 5
        cap = caps.get(str(code))
        proposed = current_monthly[code] if cap is None else cap
        proposal_monthly[code] = proposed
        if cap is not None:
            editable_cells.append(f"D{row}")
        proposal_rows.append([
            str(code), EXPENSES[code], _formula(f"='2027预算'!F{budget_row[code]}/12"),
            _formula(f"=C{row}") if cap is None else cap,
            _formula(f"=C{row}-D{row}"), _formula(f"=E{row}*12"),
            "建议月限额，可编辑；须结合用途确认执行" if cap is not None else "沿用当前草案，不额外压减；固定安排和已剔除一次性费用另列",
        ])
        for column, value in (("C", current_monthly[code]), ("D", proposed), ("E", current_monthly[code] - proposed), ("F", (current_monthly[code] - proposed) * 12)):
            check("节省方案", f"{column}{row}", value)
    proposal_rows += [
        ["", "类别月均小计", _formula("=SUM(C5:C20)"), _formula("=SUM(D5:D20)"), _formula("=C21-D21"), _formula("=E21*12"), "只比较仍在当前预算中的可变费用"],
        ["调整", "报销估计调整", _formula("='2027预算'!F21/12"), _formula("=C22"), _formula("=C22-D22"), _formula("=E22*12"), "两方案使用同一报销估计，不把报销变化计为节省"],
        ["", "净可变月均合计", _formula("=SUM(C21:C22)"), _formula("=SUM(D21:D22)"), _formula("=C23-D23"), _formula("=E23*12"), "年度增加储蓄为建议相对当前草案的增量，未额外扣历史酒店或一次性消费"],
        [None] * 7,
        ["", "方案对目标的影响", None, None, None, None, "仅为条件方案，是否执行仍需确认"],
        ["", "预计年度净储蓄", _formula("='目标测算'!B17"), _formula("=C26+F23"), None, None, "固定安排、工资和报销假设保持与当前草案一致"],
        ["", "预计年度现金增减", _formula("='目标测算'!B18"), _formula("=C27+F23"), None, None, "沿用当前付款时点假设，不保证实际逐月现金余额"],
        ["", "年度储蓄目标", _formula("='目标测算'!B5"), None, None, None, "链接既有目标，不改目标参数"],
        ["", "与目标差额", _formula("=C26-C28"), _formula("=D26-C28"), None, None, "正数表示超过目标，负数表示仍有缺口"],
        ["", "建议状态", None, None, None, None, "建议未采用。本页改动只影响本页方案，不修改实际交易、活动预算或既有固定安排。"],
    ]
    proposal_gross_monthly = sum(proposal_monthly.values())
    proposal_net_monthly = proposal_gross_monthly + reimbursement_monthly
    annual_saving = (current_net_monthly - proposal_net_monthly) * 12
    for row, current, proposed in (
        (21, current_gross_monthly, proposal_gross_monthly),
        (22, reimbursement_monthly, reimbursement_monthly),
        (23, current_net_monthly, proposal_net_monthly),
    ):
        for column, value in (("C", current), ("D", proposed), ("E", current - proposed), ("F", (current - proposed) * 12)):
            check("节省方案", f"{column}{row}", value)
    for row, value in ((26, current_savings), (27, cash_growth)):
        check("节省方案", f"C{row}", value)
        check("节省方案", f"D{row}", value + annual_saving)
    if "savings_target" in config:
        target = _amount(config["savings_target"], "savings_target")
        check("节省方案", "C28", target)
        check("节省方案", "C29", current_savings - target)
        check("节省方案", "D29", current_savings + annual_saving - target)
    proposal_sheet = _sheet(
        "节省方案", "可执行额度方案｜相对当前草案的新增节省",
        "蓝色建议限额可调整。当前月均链接活动草案；仅指定类别设建议限额，其余沿用。报销调整相同，已排除一次性消费不再计算节省。",
        ["编码", "费用类别 / 指标", "当前月均 / 当前值", "建议月均 / 建议值", "月度减少", "年度增加储蓄", "依据与说明"],
        proposal_rows, role="input", editableCells=editable_cells,
        columnWidths=[11, 31, 24, 24, 20, 22, 99], numberFormats={column: MONEY for column in "CDEF"},
        wrapColumns=["B", "G"], rowStyles={"21": "subtotal", "22": "note", "23": "total", "25": "section", "26": "total", "29": "subtotal", "30": "note"},
        rowHeights={str(row): 46 for row in range(5, 31)}, renderRange="A1:G30", subtitleHeight=55,
    )
    stats = {
        "large_count": len(large),
        "large_gross_minor": sum(r["gross_minor"] for r in large),
        "large_refund_minor": sum(r["refund_minor"] for r in large),
        "large_net_minor": sum(r["net_minor"] for r in large),
        "fully_refunded_count": sum(r["net_minor"] == 0 for r in large),
        "net_above_threshold_count": sum(r["net_minor"] >= threshold for r in large),
        "current_monthly_by_code": {str(code): value for code, value in current_monthly.items()},
        "proposal_monthly_by_code": {str(code): value for code, value in proposal_monthly.items()},
        "reimbursement_monthly": reimbursement_monthly,
        "current_net_monthly": current_net_monthly,
        "proposal_net_monthly": proposal_net_monthly,
        "annual_additional_savings": annual_saving,
        "proposed_savings": current_savings + annual_saving,
        "proposed_cash_growth": cash_growth + annual_saving,
    }
    return [large_sheet, analysis_sheet, proposal_sheet], checks, stats
