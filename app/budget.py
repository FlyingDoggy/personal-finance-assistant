"""Exact, side-effect-free living-budget calculations.

All money is a canonical base-10 string in CNY minor units.  This module does
not infer actual transactions, cash movements, evidence, or account balances.
The caller supplies confirmed, uniquely allocated facts.  An empty ``usage``
list means no supplied facts, not proof that actual spending is zero.

Payment allocations must already be split from their source bill by the
caller.  Fixed outstanding amounts compare each calendar month's plan with
that month's positive fulfillment, without inferring a new obligation from
a refund; occurrence matching and early payments belong to the plan/actual
linking layer.  Refunds require their original positive
fact in this same annual payload, so unsupported cross-year refunds fail
explicitly instead of releasing an unverified amount.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import re
from typing import Any


MAX_MINOR = 9_223_372_036_854_775_807
MAX_LINES = 2_000
MAX_USAGE = 100_000
MAX_MONTHS = 1_200
_MONEY = re.compile(r"(?:0|[1-9][0-9]*|-[1-9][0-9]*)\Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


class BudgetError(ValueError):
    """A rejected budget input or an out-of-range calculated amount."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _fail(code: str, message: str) -> None:
    raise BudgetError(code, message)


def _object(value: Any, path: str, required: set[str], optional: set[str] | None = None) -> dict:
    if not isinstance(value, dict):
        _fail("invalid_object", f"{path} 必须是对象。")
    missing = required - value.keys()
    if missing:
        _fail("missing_field", f"{path} 缺少字段：{', '.join(sorted(missing))}。")
    extra = value.keys() - required - (optional or set())
    if extra:
        _fail("unknown_field", f"{path} 包含不支持的字段：{', '.join(sorted(map(str, extra)))}。")
    return value


def _money(value: Any, path: str, *, nonnegative: bool = False) -> int:
    if not isinstance(value, str) or len(value) > 20 or not _MONEY.fullmatch(value):
        _fail("invalid_money", f"{path} 必须是规范十进制整数金额字符串，不能是浮点数、空值或带前导零。")
    amount = int(value)
    _bounded(amount, path)
    if nonnegative and amount < 0:
        _fail("negative_amount", f"{path} 不能为负数。")
    return amount


def _bounded(value: int, path: str) -> int:
    if abs(value) > MAX_MINOR:
        _fail("money_overflow", f"{path} 超出金额范围 ±{MAX_MINOR} 分。")
    return value


def _minor(value: int, path: str) -> str:
    return str(_bounded(value, path))


def _text(value: Any, path: str, *, maximum: int = 200) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        _fail("invalid_text", f"{path} 必须是无首尾空白或控制字符的非空文本，最多 {maximum} 个字符。")
    return value


def _choice(value: Any, choices: set[str], path: str) -> str:
    if not isinstance(value, str) or value not in choices:
        _fail("invalid_choice", f"{path} 必须是 {' / '.join(sorted(choices))} 之一。")
    return value


def _month_count(months: Any) -> int:
    if type(months) is not int or not 1 <= months <= MAX_MONTHS:
        _fail("invalid_month_count", f"months 必须是 1 至 {MAX_MONTHS} 的整数。")
    return months


def _monthly(value: Any, path: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 12:
        _fail("invalid_monthly_amounts", f"{path} 必须恰好包含 12 个已知金额；未设置不能用零替代。")
    return [_money(amount, f"{path}[{index}]", nonnegative=True) for index, amount in enumerate(value)]


def allocate_annual(minor: str, months: int = 12) -> list[str]:
    """Split exactly; all remainder goes to the final allocation month.

    Negative amounts use the same rule symmetrically.  No float or rounding
    of an input amount occurs.  ``months`` is bounded to prevent unbounded
    allocations and can also represent a historical observation window.
    """
    amount = _money(minor, "minor")
    count = _month_count(months)
    quotient = abs(amount) // count
    regular = quotient if amount >= 0 else -quotient
    return [str(regular)] * (count - 1) + [str(amount - regular * (count - 1))]


def normalize_reimbursements(
    income: str, expense: str, reimbursement: str, months: int
) -> dict[str, Any]:
    """Subtract same-window received reimbursement from both historical sides.

    The caller is responsible for matching the three observation windows and
    preserving coverage metadata.  The returned monthly series is a precise
    smoothing allocation of that window's R, not promised monthly cash.
    Negative adjusted totals remain visible with warnings.  Annualization is
    exact rational arithmetic rounded half up to the nearest minor unit.
    """
    original_income = _money(income, "income")
    original_expense = _money(expense, "expense")
    received = _money(reimbursement, "reimbursement", nonnegative=True)
    count = _month_count(months)
    adjusted_income = original_income - received
    adjusted_expense = original_expense - received
    annualized, remainder = divmod(received * 12, count)
    if remainder * 2 >= count:
        annualized += 1
    warnings = []
    if adjusted_income < 0:
        warnings.append({"code": "negative_adjusted_income", "message": "同窗报销大于收入，调整后收入为负；请核查窗口及覆盖。"})
    if adjusted_expense < 0:
        warnings.append({"code": "negative_adjusted_expense", "message": "同窗报销大于费用，调整后费用为负；不据此认定消费节约。"})
    return {
        "adjusted_income_minor": _minor(adjusted_income, "adjusted_income_minor"),
        "adjusted_expense_minor": _minor(adjusted_expense, "adjusted_expense_minor"),
        "surplus_minor": _minor(original_income - original_expense, "surplus_minor"),
        "monthly_reimbursement_minor": allocate_annual(reimbursement, count),
        "annualized_reimbursement_minor": _minor(annualized, "annualized_reimbursement_minor"),
        "warnings": warnings,
    }


def compute_budget(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and calculate one annual CNY living-budget snapshot.

    ``usage.id`` identifies one allocated fact and is globally unique in this
    snapshot. Duplicate IDs are rejected, not silently dropped. Every usage
    references a known line, including excluded salary deductions and facts
    ignored under the line's explicit execution basis. All negative usage
    requires ``refund_of`` and cannot cumulatively exceed its original fact.
    """
    _object(payload, "budget", {"year", "currency", "funding_minor", "target_minor", "lines", "usage"})
    year = payload["year"]
    if type(year) is not int or not 1 <= year <= 9999:
        _fail("invalid_year", "year 必须是 1 至 9999 的整数。")
    if payload["currency"] != "CNY":
        _fail("unsupported_currency", "预算当前只支持 CNY，金额以分表示。")
    funding = _monthly(payload["funding_minor"], "funding_minor")
    target = _money(payload["target_minor"], "target_minor", nonnegative=True)
    lines = payload["lines"]
    usages = payload["usage"]
    if not isinstance(lines, list) or len(lines) > MAX_LINES:
        _fail("invalid_lines", f"lines 必须是数组，且不超过 {MAX_LINES} 行。")
    if not isinstance(usages, list) or len(usages) > MAX_USAGE:
        _fail("invalid_usage", f"usage 必须是数组，且不超过 {MAX_USAGE} 项。")

    by_line: dict[str, dict[str, Any]] = {}
    for index, line in enumerate(lines):
        path = f"lines[{index}]"
        _object(line, path, {"id", "name", "group", "allocation", "execution_basis", "actual_basis", "monthly_minor"})
        line_id = _text(line["id"], f"{path}.id")
        if line_id in by_line:
            _fail("duplicate_line_id", f"预算行 ID 重复：{line_id}。")
        _text(line["name"], f"{path}.name")
        _text(line["group"], f"{path}.group")
        allocation = _choice(line["allocation"], {"fixed", "daily"}, f"{path}.allocation")
        execution = _choice(line["execution_basis"], {"consumption", "scheduled_payment", "annual_envelope"}, f"{path}.execution_basis")
        actual_basis = _choice(line["actual_basis"], {"consumption", "payment"}, f"{path}.actual_basis")
        if execution == "annual_envelope" and allocation != "daily":
            _fail("invalid_allocation", f"{path} 年度额度的月分配必须归入 daily。")
        required_basis = {"consumption": "consumption", "scheduled_payment": "payment"}.get(execution)
        if required_basis is not None and actual_basis != required_basis:
            _fail("inconsistent_basis", f"{path} 的 actual_basis 必须是 {required_basis}。")
        by_line[line_id] = {
            "line": line,
            "planned": _monthly(line["monthly_minor"], f"{path}.monthly_minor"),
            "used": [0] * 12,
            "positive_fulfillment": [0] * 12,
        }

    by_usage: dict[str, dict[str, Any]] = {}
    for index, usage in enumerate(usages):
        path = f"usage[{index}]"
        _object(usage, path, {"id", "line_id", "date", "amount_minor", "kind"}, {"source_already_netted", "refund_of"})
        usage_id = _text(usage["id"], f"{path}.id")
        if usage_id in by_usage:
            _fail("duplicate_usage_id", f"实际占用 ID 重复：{usage_id}；请先核查事实分配。")
        line_id = _text(usage["line_id"], f"{path}.line_id")
        if line_id not in by_line:
            _fail("unknown_budget_line", f"实际占用 {usage_id} 无法映射预算行 {line_id}，需先处理待分配占用。")
        raw_date = usage["date"]
        if not isinstance(raw_date, str) or not _DATE.fullmatch(raw_date):
            _fail("invalid_date", f"{path}.date 必须是有效的 YYYY-MM-DD 日期。")
        try:
            actual_date = date.fromisoformat(raw_date)
        except ValueError:
            _fail("invalid_date", f"{path}.date 不是有效日期。")
        if actual_date.year != year:
            _fail("date_out_of_year", f"实际占用 {usage_id} 不在预算年度 {year}；不能静默忽略。")
        amount = _money(usage["amount_minor"], f"{path}.amount_minor")
        _choice(usage["kind"], {"consumption", "payment"}, f"{path}.kind")
        netted = usage.get("source_already_netted", False)
        if type(netted) is not bool:
            _fail("invalid_netted_flag", f"{path}.source_already_netted 必须是布尔值。")
        if amount < 0:
            if "refund_of" not in usage:
                _fail("unmatched_refund", f"负占用 {usage_id} 必须通过 refund_of 关联已确认的原占用。")
            _text(usage["refund_of"], f"{path}.refund_of")
        elif "refund_of" in usage:
            _fail("invalid_refund", f"{usage_id} 的 refund_of 只能用于负占用。")
        by_usage[usage_id] = {"usage": usage, "amount": amount, "date": actual_date, "netted": netted}

    refunded: dict[str, int] = {}
    for usage_id, item in by_usage.items():
        usage = item["usage"]
        amount = item["amount"]
        if amount < 0:
            original_id = usage["refund_of"]
            original = by_usage.get(original_id)
            if original is None:
                _fail("unmatched_refund", f"退款 {usage_id} 的原占用 {original_id} 不在本年度快照；请先核查关联及跨年证据。")
            if (
                original["amount"] <= 0
                or original["usage"]["line_id"] != usage["line_id"]
                or original["usage"]["kind"] != usage["kind"]
                or original["netted"] != item["netted"]
                or original["date"] > item["date"]
            ):
                _fail("invalid_refund", f"退款 {usage_id} 必须关联同一预算行、同一口径、扣缴标记一致且不晚于退款的正额原事实。")
            refunded[original_id] = refunded.get(original_id, 0) - amount
            if refunded[original_id] > original["amount"]:
                _fail("refund_exceeds_original", f"原占用 {original_id} 的累计退款超过已确认金额。")
        line_data = by_line[usage["line_id"]]
        if not item["netted"] and usage["kind"] == line_data["line"]["actual_basis"]:
            line_data["used"][item["date"].month - 1] += amount
            line_data["positive_fulfillment"][item["date"].month - 1] += max(0, amount)

    targets = [int(value) for value in allocate_annual(str(target))]
    months = []
    for month_index in range(12):
        fixed = daily = annual_allocation = used = daily_used = fixed_outstanding = 0
        for line_data in by_line.values():
            line = line_data["line"]
            planned_amount = line_data["planned"][month_index]
            used_amount = line_data["used"][month_index]
            used += used_amount
            if line["allocation"] == "fixed":
                fixed += planned_amount
                # This is a same-month plan/positive-fulfillment comparison,
                # not an occurrence-level payable balance. A refund releases
                # usage but alone is no evidence of a new obligation to pay.
                fulfilled = line_data["positive_fulfillment"][month_index]
                fixed_outstanding += max(0, planned_amount - fulfilled)
            else:
                daily += planned_amount
                if line["execution_basis"] == "annual_envelope":
                    annual_allocation += planned_amount
                else:
                    daily_used += used_amount
        planned = fixed + daily
        retention = funding[month_index] - planned
        monthly_values = {
            "funding_minor": funding[month_index],
            "fixed_minor": fixed,
            "daily_minor": daily,
            "annual_allocation_minor": annual_allocation,
            "planned_minor": planned,
            "usage_minor": used,
            "daily_usage_minor": daily_used,
            "daily_remaining_minor": daily - annual_allocation - daily_used,
            "fixed_outstanding_minor": fixed_outstanding,
            "schedule_variance_minor": planned - used,
            "planned_retention_minor": retention,
            "target_minor": targets[month_index],
            "target_gap_minor": max(0, targets[month_index] - retention),
            "daily_limit_minor": funding[month_index] - fixed - targets[month_index],
        }
        months.append({
            "month": f"{year:04d}-{month_index + 1:02d}",
            **{key: _minor(value, f"months[{month_index}].{key}") for key, value in monthly_values.items()},
        })

    annual_funding = sum(funding)
    annual_planned = sum(sum(item["planned"]) for item in by_line.values())
    annual_retention = annual_funding - annual_planned
    annual_values = {
        "funding_minor": annual_funding,
        "planned_minor": annual_planned,
        "planned_retention_minor": annual_retention,
        "target_minor": target,
        "target_gap_minor": max(0, target - annual_retention),
    }
    line_results = []
    for line_id, line_data in by_line.items():
        planned = sum(line_data["planned"])
        used = sum(line_data["used"])
        line_results.append({
            "id": line_id,
            "annual_planned_minor": _minor(planned, f"line_results[{line_id}].annual_planned_minor"),
            "annual_used_minor": _minor(used, f"line_results[{line_id}].annual_used_minor"),
            "annual_remaining_minor": _minor(planned - used, f"line_results[{line_id}].annual_remaining_minor"),
            "monthly_usage_minor": [_minor(value, f"line_results[{line_id}].monthly_usage_minor") for value in line_data["used"]],
        })
    return {
        **deepcopy(payload),
        "months": months,
        "annual": {key: _minor(value, f"annual.{key}") for key, value in annual_values.items()},
        "line_results": line_results,
    }
