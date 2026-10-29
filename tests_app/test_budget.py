"""Hand-calculable, synthetic cases for the pure living-budget engine."""

from copy import deepcopy

import pytest

from app.budget import BudgetError, MAX_MINOR, allocate_annual, compute_budget, normalize_reimbursements


def budget(*lines, funding="2000000", target="0", usage=None):
    return {
        "year": 2027,
        "currency": "CNY",
        "funding_minor": [funding] * 12,
        "target_minor": target,
        "lines": list(lines),
        "usage": [] if usage is None else usage,
    }


def line(identifier="daily", monthly="200000", allocation="daily", execution="consumption", actual=None):
    return {
        "id": identifier,
        "name": identifier,
        "group": "合成生活用途",
        "allocation": allocation,
        "execution_basis": execution,
        "actual_basis": actual or ("payment" if execution == "scheduled_payment" else "consumption"),
        "monthly_minor": [monthly] * 12,
    }


def usage(identifier, line_id, amount, date="2027-01-15", kind="consumption", **extra):
    return {"id": identifier, "line_id": line_id, "date": date, "amount_minor": amount, "kind": kind, **extra}


def expect_error(code, payload):
    with pytest.raises(BudgetError) as caught:
        compute_budget(payload)
    assert isinstance(caught.value, ValueError)
    assert caught.value.code == code
    assert caught.value.message


def test_ac50_financing_keeps_full_purchase_out_of_living_budget():
    payload = budget(
        line("financing", "105000", "fixed", "scheduled_payment"),
        usage=[
            usage("purchase", "financing", "1200000"),
            usage("first-payment", "financing", "105000", kind="payment"),
        ],
    )
    result = compute_budget(payload)
    assert result["months"][0]["usage_minor"] == "105000"
    assert result["months"][0]["fixed_outstanding_minor"] == "0"
    assert sum(int(month["fixed_outstanding_minor"]) for month in result["months"][1:]) == 1155000
    assert result["line_results"][0]["annual_used_minor"] == "105000"
    assert result["usage"][0]["amount_minor"] == "1200000"

    # A payment schedule alone does not manufacture interest or actual use.
    payload["usage"] = []
    estimated = compute_budget(payload)
    assert estimated["line_results"][0]["annual_used_minor"] == "0"
    assert estimated["line_results"][0]["annual_planned_minor"] == "1260000"


def test_ac50_refund_releases_only_confirmed_returned_payment():
    payload = budget(
        line("financing", "105000", "fixed", "scheduled_payment"),
        usage=[
            usage("purchase", "financing", "1200000"),
            usage("payment", "financing", "105000", kind="payment"),
            usage("goods-refund", "financing", "-1200000", "2027-02-01", refund_of="purchase"),
            usage("cash-return", "financing", "-100000", "2027-02-01", "payment", refund_of="payment"),
        ],
    )
    result = compute_budget(payload)
    assert result["line_results"][0]["annual_used_minor"] == "5000"
    assert result["months"][1]["usage_minor"] == "-100000"
    payload["usage"][-1]["amount_minor"] = "-1200000"
    expect_error("refund_exceeds_original", payload)


def test_ac51_annual_purchase_does_not_consume_daily_remainder_twice():
    payload = budget(
        line("annual", "10000", execution="annual_envelope"),
        line("daily", "200000"),
        usage=[
            usage("annual-purchase", "annual", "120000", "2027-07-05"),
            usage("annual-payment", "annual", "120000", "2027-07-05", "payment"),
            usage("daily-purchase", "daily", "50000", "2027-07-06"),
        ],
    )
    result = compute_budget(payload)
    july = result["months"][6]
    assert july["planned_minor"] == "210000"
    assert july["annual_allocation_minor"] == "10000"
    assert july["usage_minor"] == "170000"
    assert july["daily_usage_minor"] == "50000"
    assert july["schedule_variance_minor"] == "40000"
    assert july["daily_remaining_minor"] == "150000"
    assert result["line_results"][0]["annual_remaining_minor"] == "0"
    assert result["months"][5]["usage_minor"] == "0"
    assert result["months"][7]["usage_minor"] == "0"


def test_ac51_final_month_takes_the_entire_remainder():
    amounts = allocate_annual("100000")
    assert amounts == ["8333"] * 11 + ["8337"]
    assert sum(map(int, amounts)) == 100000
    assert allocate_annual("5") == ["0"] * 11 + ["5"]
    assert allocate_annual("-100000") == ["-8333"] * 11 + ["-8337"]
    assert allocate_annual("100000", 1) == ["100000"]
    annual_line = line("annual", execution="annual_envelope")
    annual_line["monthly_minor"] = amounts
    assert compute_budget(budget(annual_line))["line_results"][0]["annual_planned_minor"] == "100000"


def test_ac52_bill_allocations_preserve_all_consumption_without_recounting_repayment():
    # Two legitimate purchases share one later bill: neither purchase is lost.
    payload = budget(
        line("ordinary"),
        line("financing", "105000", "fixed", "scheduled_payment"),
        usage=[
            usage("purchase-a", "ordinary", "100000"),
            usage("purchase-b", "ordinary", "200000"),
            usage("bill:ordinary-allocation", "ordinary", "300000", "2027-02-15", "payment"),
            usage("bill:financing-allocation", "financing", "105000", "2027-02-15", "payment"),
        ],
    )
    result = compute_budget(payload)
    assert result["months"][0]["usage_minor"] == "300000"
    assert result["months"][1]["usage_minor"] == "105000"
    assert sum(int(month["usage_minor"]) for month in result["months"]) == 405000
    # Cash is deliberately not inferred from budget allocations.
    assert "cash_minor" not in result["annual"]


def test_ac53_goal_change_does_not_change_planned_retention():
    payload = budget(
        line("fixed", "1000000", "fixed", "scheduled_payment"),
        line("daily", "600000"),
        target="6000000",
    )
    result = compute_budget(payload)
    assert result["annual"]["funding_minor"] == "24000000"
    assert result["annual"]["planned_minor"] == "19200000"
    assert result["annual"]["planned_retention_minor"] == "4800000"
    assert result["annual"]["target_gap_minor"] == "1200000"
    assert result["months"][0]["daily_limit_minor"] == "500000"
    assert result["months"][0]["target_gap_minor"] == "100000"
    payload["target_minor"] = "7200000"
    changed = compute_budget(payload)
    assert changed["annual"]["planned_retention_minor"] == "4800000"
    assert changed["annual"]["target_gap_minor"] == "2400000"
    assert changed["months"][0]["daily_limit_minor"] == "400000"
    assert changed["lines"] == result["lines"]


def test_ac53_net_salary_bridge_excludes_only_marked_facts():
    payload = budget(line("living"), line("tax"), funding="245000", usage=[usage("living", "living", "30000")])
    before = compute_budget(payload)
    payload["usage"].extend([
        usage("withheld-tax", "tax", "15000", source_already_netted=True),
        usage("withheld-fund", "tax", "40000", source_already_netted=True),
    ])
    after = compute_budget(payload)
    assert before["months"][0]["usage_minor"] == after["months"][0]["usage_minor"] == "30000"
    assert after["funding_minor"][0] == "245000"
    assert 245000 - int(after["months"][0]["usage_minor"]) == 215000
    payload["usage"].append(usage("separate-tax", "tax", "5000"))
    separate = compute_budget(payload)
    assert separate["months"][0]["usage_minor"] == "35000"
    assert 245000 - int(separate["months"][0]["usage_minor"]) == 210000


def test_annual_payment_basis_uses_payment_and_confirmed_payment_refund():
    payload = budget(line("annual", "10000", execution="annual_envelope", actual="payment"), usage=[
        usage("purchase", "annual", "120000"),
        usage("paid", "annual", "100000", kind="payment"),
        usage("returned", "annual", "-20000", "2027-02-01", "payment", refund_of="paid"),
    ])
    result = compute_budget(payload)
    assert result["line_results"][0]["annual_used_minor"] == "80000"
    assert result["line_results"][0]["annual_remaining_minor"] == "40000"
    assert result["months"][0]["daily_remaining_minor"] == "0"


def test_fixed_outstanding_is_per_line_and_never_negative():
    payload = budget(
        line("a", "10000", "fixed", "scheduled_payment"),
        line("b", "10000", "fixed", "scheduled_payment"),
        usage=[usage("a-pay", "a", "15000", kind="payment"), usage("b-pay", "b", "3000", kind="payment")],
    )
    january = compute_budget(payload)["months"][0]
    assert january["fixed_outstanding_minor"] == "7000"
    assert january["schedule_variance_minor"] == "2000"


def test_fixed_refund_does_not_create_a_new_payable_in_the_refund_month():
    row = line("fixed", "0", "fixed", "consumption")
    row["monthly_minor"][0] = "10000"
    payload = budget(row, usage=[
        usage("purchase", "fixed", "10000"),
        usage("refund", "fixed", "-10000", "2027-02-01", refund_of="purchase"),
    ])
    result = compute_budget(payload)
    assert result["months"][0]["fixed_outstanding_minor"] == "0"
    assert result["months"][1]["usage_minor"] == "-10000"
    assert result["months"][1]["fixed_outstanding_minor"] == "0"
    assert result["line_results"][0]["annual_used_minor"] == "0"


def test_previous_period_refund_does_not_undo_current_period_positive_fulfillment():
    payload = budget(line("fixed", "10000", "fixed", "scheduled_payment"), usage=[
        usage("january", "fixed", "10000", kind="payment"),
        usage("february", "fixed", "10000", "2027-02-01", "payment"),
        usage("refund-january", "fixed", "-10000", "2027-02-02", "payment", refund_of="january"),
    ])
    february = compute_budget(payload)["months"][1]
    assert february["usage_minor"] == "0"
    assert february["fixed_outstanding_minor"] == "0"


def test_negative_daily_limit_and_negative_remaining_are_not_clamped():
    payload = budget(line(monthly="100"), line("fixed", "100", "fixed", "scheduled_payment"), funding="50", target="120", usage=[usage("large", "daily", "150")])
    january = compute_budget(payload)["months"][0]
    assert january["daily_limit_minor"] == "-60"
    assert january["daily_remaining_minor"] == "-50"
    assert january["planned_retention_minor"] == "-150"


def test_goal_allocation_conserves_cents():
    result = compute_budget(budget(target="100000"))
    assert [month["target_minor"] for month in result["months"]] == ["8333"] * 11 + ["8337"]
    assert sum(int(month["target_minor"]) for month in result["months"]) == 100000


def test_ac35_same_window_normalization_preserves_surplus():
    normalized = normalize_reimbursements("192000", "120000", "12000", 12)
    assert normalized == {
        "adjusted_income_minor": "180000",
        "adjusted_expense_minor": "108000",
        "surplus_minor": "72000",
        "monthly_reimbursement_minor": ["1000"] * 12,
        "annualized_reimbursement_minor": "12000",
        "warnings": [],
    }
    # Only received R is removed. Family support included in I stays in I*.
    family = normalize_reimbursements("135000", "90000", "15000", 10)
    assert family["adjusted_income_minor"] == "120000"
    assert family["adjusted_expense_minor"] == "75000"
    assert family["surplus_minor"] == "45000"


def test_normalization_negative_net_expense_is_disclosed_not_clamped():
    normalized = normalize_reimbursements("10000", "1000", "1500", 10)
    assert normalized["adjusted_expense_minor"] == "-500"
    assert normalized["surplus_minor"] == "9000"
    assert normalized["warnings"][0]["code"] == "negative_adjusted_expense"


def test_normalization_rounds_rational_annualization_half_up():
    normalized = normalize_reimbursements("100", "100", "1", 8)
    assert normalized["annualized_reimbursement_minor"] == "2"  # 12/8 = 1.5 cents.
    assert normalized["monthly_reimbursement_minor"] == ["0"] * 7 + ["1"]
    normalized = normalize_reimbursements("100", "100", "1", 10)
    assert normalized["annualized_reimbursement_minor"] == "1"


@pytest.mark.parametrize("value", [1, 1.0, True, None, "", " 1", "1 ", "+1", "01", "-0", "1.0", "1e2", "１２", "NaN"])
def test_ac54_money_requires_canonical_integer_strings(value):
    payload = budget()
    payload["target_minor"] = value
    expect_error("invalid_money", payload)


@pytest.mark.parametrize("value", [str(MAX_MINOR + 1), str(-MAX_MINOR - 1)])
def test_ac54_money_outside_declared_symmetric_range_is_rejected(value):
    with pytest.raises(BudgetError, match="金额范围") as caught:
        allocate_annual(value)
    assert caught.value.code == "money_overflow"


def test_money_maximum_remains_exact_and_computed_overflow_is_rejected():
    assert sum(map(int, allocate_annual(str(MAX_MINOR)))) == MAX_MINOR
    valid = budget(funding="0", target=str(MAX_MINOR))
    valid["funding_minor"][0] = str(MAX_MINOR)
    assert compute_budget(valid)["annual"]["funding_minor"] == str(MAX_MINOR)
    valid["funding_minor"][1] = "1"
    expect_error("money_overflow", valid)
    payload = budget(line(monthly="0"), funding="0", usage=[
        usage("large", "daily", str(MAX_MINOR)), usage("extra", "daily", "1"),
    ])
    expect_error("money_overflow", payload)


def test_money_target_gap_and_normalization_annualization_are_checked_for_overflow():
    row = line(monthly="0")
    row["monthly_minor"][0] = str(MAX_MINOR)
    expect_error("money_overflow", budget(row, funding="0", target="12"))
    with pytest.raises(BudgetError) as caught:
        normalize_reimbursements(str(MAX_MINOR), str(MAX_MINOR), str(MAX_MINOR), 1)
    assert caught.value.code == "money_overflow"


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "12", 1201])
def test_allocation_and_normalization_validate_month_count(invalid):
    for function in (lambda: allocate_annual("10", invalid), lambda: normalize_reimbursements("10", "10", "1", invalid)):
        with pytest.raises(BudgetError) as caught:
            function()
        assert caught.value.code == "invalid_month_count"


@pytest.mark.parametrize("bad_date", ["2027-02-29", "2027-13-01", "2027-00-01", "2027-1-01", "2027-01-32", "2027-01-01T00:00:00", 20270101, None])
def test_ac54_invalid_dates_are_rejected(bad_date):
    expect_error("invalid_date", budget(line(), usage=[usage("fact", "daily", "1", bad_date)]))


def test_leap_day_is_valid_in_the_correct_year():
    payload = budget(line(), usage=[usage("fact", "daily", "1", "2028-02-29")])
    payload["year"] = 2028
    assert compute_budget(payload)["months"][1]["usage_minor"] == "1"


def test_unknown_mapping_and_out_of_year_usage_cannot_silently_disappear():
    expect_error("unknown_budget_line", budget(line(), usage=[usage("unmapped", "missing", "8000")]))
    expect_error("date_out_of_year", budget(line(), usage=[usage("previous", "daily", "8000", "2026-12-31")]))
    expect_error("unknown_budget_line", budget(line(), usage=[usage("netted-but-unmapped", "missing", "8000", source_already_netted=True)]))


def test_ac37_unclassified_known_consumption_can_be_explicitly_allocated_once():
    payload = budget(line("unclassified", monthly="20000"), usage=[usage("known-consumption", "unclassified", "8000")])
    assert compute_budget(payload)["months"][0]["daily_remaining_minor"] == "12000"
    payload["usage"].append(deepcopy(payload["usage"][0]))
    expect_error("duplicate_usage_id", payload)
    # Unknown nature cannot be passed through as a known consumption or zero.
    payload["usage"] = [usage("unknown", "unclassified", "12000", kind="unknown")]
    expect_error("invalid_choice", payload)


def test_duplicate_ids_and_missing_usage_identity_are_rejected():
    expect_error("duplicate_line_id", budget(line(), line()))
    payload = budget(line("a"), line("b"), usage=[usage("same", "a", "100"), usage("same", "b", "100")])
    expect_error("duplicate_usage_id", payload)
    payload = budget(line(), usage=[usage("fact", "daily", "100")])
    del payload["usage"][0]["id"]
    expect_error("missing_field", payload)


def test_invalid_execution_combinations_are_not_silently_normalized():
    expect_error("invalid_allocation", budget(line(execution="annual_envelope", allocation="fixed")))
    expect_error("inconsistent_basis", budget(line(execution="consumption", actual="payment")))
    expect_error("inconsistent_basis", budget(line(execution="scheduled_payment", actual="consumption")))


@pytest.mark.parametrize("field", ["funding_minor", "target_minor"])
def test_missing_and_negative_planned_amounts_are_not_zero(field):
    payload = budget()
    if field == "funding_minor":
        payload[field][0] = None
    else:
        payload[field] = None
    expect_error("invalid_money", payload)
    payload = budget()
    if field == "funding_minor":
        payload[field][0] = "-1"
    else:
        payload[field] = "-1"
    expect_error("negative_amount", payload)


def test_month_vectors_have_exactly_twelve_known_values():
    payload = budget()
    payload["funding_minor"] = ["1"] * 11
    expect_error("invalid_monthly_amounts", payload)
    row = line()
    row["monthly_minor"] = ["1"] * 13
    expect_error("invalid_monthly_amounts", budget(row))


@pytest.mark.parametrize("year", [True, 2027.0, "2027", 0, 10000])
def test_year_requires_valid_integer(year):
    payload = budget()
    payload["year"] = year
    expect_error("invalid_year", payload)


def test_currency_unknown_fields_and_flag_types_are_validated():
    payload = budget()
    payload["currency"] = "USD"
    expect_error("unsupported_currency", payload)
    payload = budget()
    payload["exponent"] = 3
    expect_error("unknown_field", payload)
    payload = budget(line(), usage=[usage("a", "daily", "100", source_already_netted="false")])
    expect_error("invalid_netted_flag", payload)


def test_refund_requires_a_positive_matching_original_and_cumulative_limit():
    original = usage("original", "daily", "10000")
    expect_error("unmatched_refund", budget(line(), usage=[usage("refund", "daily", "-100")]))
    expect_error("unmatched_refund", budget(line(), usage=[usage("refund", "daily", "-100", refund_of="old-year")]))
    payload = budget(line(), usage=[original,
        usage("refund-a", "daily", "-6000", "2027-02-01", refund_of="original"),
        usage("refund-b", "daily", "-5000", "2027-03-01", refund_of="original"),
    ])
    expect_error("refund_exceeds_original", payload)
    payload["usage"][2]["amount_minor"] = "-4000"
    assert compute_budget(payload)["line_results"][0]["annual_used_minor"] == "0"


@pytest.mark.parametrize("change", [
    {"kind": "payment"}, {"source_already_netted": True}, {"date": "2027-01-01"}, {"line_id": "other"},
])
def test_refund_mapping_must_match_original(change):
    refund = usage("refund", "daily", "-100", "2027-02-01", refund_of="original")
    refund.update(change)
    expect_error("invalid_refund", budget(line(), line("other"), usage=[usage("original", "daily", "10000"), refund]))


def test_refund_input_order_does_not_change_math():
    entries = [usage("original", "daily", "10000"), usage("refund", "daily", "-1000", "2027-02-01", refund_of="original")]
    forward = compute_budget(budget(line(), usage=entries))
    reverse = compute_budget(budget(line(), usage=list(reversed(entries))))
    assert forward["months"] == reverse["months"]
    assert forward["line_results"] == reverse["line_results"]


def test_pure_result_preserves_inputs_without_shared_mutable_objects():
    payload = budget(line(), usage=[usage("original", "daily", "10000")])
    saved = deepcopy(payload)
    result = compute_budget(payload)
    assert payload == saved
    assert {key: result[key] for key in saved} == saved
    result["lines"][0]["monthly_minor"][0] = "999"
    result["usage"][0]["amount_minor"] = "999"
    assert payload == saved


def test_every_output_money_field_is_a_string():
    result = compute_budget(budget(line(), usage=[usage("fact", "daily", "12345")]))

    def verify(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                if key.endswith("_minor") and not isinstance(nested, list):
                    assert isinstance(nested, str), key
                if key.endswith("_minor") and isinstance(nested, list):
                    assert all(isinstance(amount, str) for amount in nested), key
                verify(nested)
        elif isinstance(value, list):
            for item in value:
                verify(item)

    verify(result)
