"""Pure calculations for the v0.3 total-amount reimbursement policy.

All ``*_minor`` amounts are integer minor currency units. Callers must aggregate
income, expenses and all received reimbursements over the same observed window.
A complete window is preferred. With an explicit user choice, the window may
include a partially observed final month; ``covered_months`` then means the
selected number of calendar months, not a claim of complete source coverage.
The current Jan-Oct estimate uses ten months for all observed cumulative totals,
including October receipts, without scaling the partial month by elapsed days.
Complete zero-transaction months remain in the denominator; receipt counts do
not define it, and missing transactions must not be invented. Callers must retain
partial coverage, observation cutoffs and source gaps in their reports.
Receipts need not be allocated back to earlier expense months. Actual dates
remain unchanged. A partial month may understate expenses; cross-period mismatch
can bias adjusted consumption in either direction. These helpers neither access
finance files nor create accounting entries, and cannot validate source coverage.
"""

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import TypedDict


REIMBURSEMENT_EXCEEDS_EXPENSE = "reimbursement_exceeds_expense"


def select_budget_window(events, start_date: str, end_date_exclusive: str) -> list:
    """Select dated events in one common [start, end) observation window.

    Dates must be canonical YYYY-MM-DD calendar dates. Empty, missing, invalid
    or noncanonical event dates are excluded; invalid window boundaries raise
    an error. The result preserves event objects and their input order without
    modifying them. Selection cannot establish whether source coverage is full
    or partial and does not access files or infer dates for undated events.
    """
    for name, boundary in (
        ("start_date", start_date),
        ("end_date_exclusive", end_date_exclusive),
    ):
        if not isinstance(boundary, str):
            raise TypeError(f"{name} must be a YYYY-MM-DD date string")
        try:
            canonical = date.fromisoformat(boundary).isoformat()
        except ValueError as exc:
            raise ValueError(f"{name} must be a valid YYYY-MM-DD date") from exc
        if canonical != boundary:
            raise ValueError(f"{name} must be a valid YYYY-MM-DD date")
    if start_date >= end_date_exclusive:
        raise ValueError("start_date must precede end_date_exclusive")

    selected = []
    for event in events:
        observed_date = event.get("date")
        if not isinstance(observed_date, str):
            continue
        try:
            if date.fromisoformat(observed_date).isoformat() != observed_date:
                continue
        except ValueError:
            continue
        if start_date <= observed_date < end_date_exclusive:
            selected.append(event)
    return selected


class BudgetBasis(TypedDict):
    adjusted_income_minor: int
    adjusted_expense_minor: int
    surplus_minor: int
    monthly_reimbursement_minor: Decimal
    annualized_reimbursement_minor: Decimal
    covered_months: int
    warnings: tuple[str, ...]


def _integer_minor(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer number of minor currency units")
    return value


def _covered_months(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("covered_months must be an integer from 1 to 12")
    if not 1 <= value <= 12:
        raise ValueError("covered_months must be from 1 to 12")
    return value


def _reimbursement_minor(value: int) -> int:
    value = _integer_minor(value, "reimbursement_minor")
    if value < 0:
        raise ValueError("reimbursement_minor must be nonnegative")
    return value


def _decimal_multiplier(value: Decimal | int | str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise TypeError("multiplier must be Decimal, int or a decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("multiplier must be a finite nonnegative decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("multiplier must be a finite nonnegative decimal")
    return result


def normalize_budget_basis(
    gross_income_minor: int,
    gross_expense_minor: int,
    reimbursement_minor: int,
    covered_months: int,
) -> BudgetBasis:
    """Subtract received reimbursements from both actual totals exactly once.

    ``gross_income_minor`` already includes ``reimbursement_minor``. The same
    subtraction from income and expenses preserves actual surplus. This does
    not reconstruct personal consumption transaction by transaction, and it
    must not be applied to a future wage budget that already excludes reimbursements.

    ``covered_months`` is the common selected calendar-month count for the
    observed totals. A user-selected partial final month does not change the
    arithmetic or imply complete coverage; the caller must disclose that estimate.

    Reimbursement estimates are Decimal amounts in minor units, not posted
    transactions. Annualization is calculated directly from the window total,
    avoiding premature monthly rounding. An over-reimbursement leaves the
    negative expense basis intact and returns ``reimbursement_exceeds_expense``
    in ``warnings``: cross-period receipts can make that basis unsuitable for
    automatic future budgeting. No clipping or balancing entry is performed.
    """
    income = _integer_minor(gross_income_minor, "gross_income_minor")
    expense = _integer_minor(gross_expense_minor, "gross_expense_minor")
    reimbursement = _reimbursement_minor(reimbursement_minor)
    months = _covered_months(covered_months)
    with localcontext() as context:
        context.prec = max(28, len(str(reimbursement)) + 28)
        monthly_reimbursement = Decimal(reimbursement) / Decimal(months)
        annualized_reimbursement = Decimal(reimbursement * 12) / Decimal(months)
    return {
        "adjusted_income_minor": income - reimbursement,
        "adjusted_expense_minor": expense - reimbursement,
        "surplus_minor": income - expense,
        "monthly_reimbursement_minor": monthly_reimbursement,
        "annualized_reimbursement_minor": annualized_reimbursement,
        "covered_months": months,
        "warnings": (REIMBURSEMENT_EXCEEDS_EXPENSE,)
        if reimbursement > expense else (),
    }


def scaled_personal_expense_minor(
    gross_expense_minor: int,
    reimbursement_minor: int,
    covered_months: int,
    multiplier: Decimal | int | str,
) -> int:
    """Return annual personal expense cents: round((E - R) * 12 * k / m).

    Deduct the window's actual reimbursement total before applying the scenario
    multiplier. Round only the final annual result, using ROUND_HALF_UP, which
    also rounds a negative half away from zero. The result can be negative;
    callers should inspect ``normalize_budget_basis`` warnings before adoption.
    Use the same selected calendar-month count as for income and reimbursement
    estimates, and retain any user-selected partial-window disclosure.
    Float multipliers are rejected so binary approximation cannot alter cents.
    """
    expense = _integer_minor(gross_expense_minor, "gross_expense_minor")
    reimbursement = _reimbursement_minor(reimbursement_minor)
    months = _covered_months(covered_months)
    factor = _decimal_multiplier(multiplier)
    annual_numerator = (expense - reimbursement) * 12
    with localcontext() as context:
        # Keep the integer multiplication exact and retain ample fractional
        # precision before the sole final rounding, independent of caller prec.
        context.prec = max(
            28,
            len(str(abs(annual_numerator)))
            + len(factor.as_tuple().digits)
            + abs(factor.adjusted())
            + 28,
        )
        annual = Decimal(annual_numerator) * factor / Decimal(months)
        return int(annual.quantize(Decimal(1), rounding=ROUND_HALF_UP))
