"""Pure, exhaustive partitioning of signed expenses in a selected date window.

Amounts are integer minor currency units. No accounting events are changed,
netted, removed, or created; refunds retain their own posting dates and signs.
This module performs no file or database access.
"""

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Literal, TypedDict


Bucket = Literal["trip", "durable", "fixed", "daily"]
BUCKETS: tuple[Bucket, ...] = ("trip", "durable", "fixed", "daily")
REFUND_LINK = "退款对应原消费"


class ExpensePartition(TypedDict):
    event_buckets: dict[str, Bucket]
    bucket_totals_minor: dict[Bucket, int]
    monthly_bucket_totals_minor: dict[str, dict[Bucket, int]]
    monthly_totals_minor: dict[str, int]
    total_expense_minor: int
    unresolved_refund_ids: tuple[str, ...]


def _calendar_date(value: str, name: str) -> date:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a YYYY-MM-DD date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid YYYY-MM-DD date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be a valid YYYY-MM-DD date")
    return parsed


def _explicit_ids(values: Iterable[str], name: str) -> set[str]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be a collection of event IDs")
    result = set()
    for event_id in values:
        if not isinstance(event_id, str) or not event_id:
            raise ValueError(f"{name} must contain nonempty string event IDs")
        result.add(event_id)
    return result


def _empty_totals() -> dict[Bucket, int]:
    return {bucket: 0 for bucket in BUCKETS}


def _window_months(start: date, end_exclusive: date) -> list[str]:
    year, month = start.year, start.month
    result = []
    while (year, month) < (end_exclusive.year, end_exclusive.month) or (
        (year, month) == (end_exclusive.year, end_exclusive.month)
        and end_exclusive.day > 1
    ):
        result.append(f"{year:04}-{month:02}")
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return result


def partition_expenses(
    events: Iterable[Mapping],
    *,
    start: str,
    end_exclusive: str,
    trip_ids: Iterable[str],
    durable_ids: Iterable[str],
    fixed_ids: Iterable[str],
) -> ExpensePartition:
    """Assign every nonzero expense in ``[start, end_exclusive)`` once.

    The explicit buckets are considered in trip, durable, fixed order, with
    daily as the fallback. Explicit sets must be disjoint, and every explicit
    ID must name a nonzero expense in the full supplied event collection.
    Behavior labels, including ``冲动``, do not affect classification.

    A negative expense with ``link_type == '退款对应原消费'`` inherits the
    original expense's bucket, including when the original falls outside the
    selected window. A conflicting explicit refund classification raises.
    A missing or non-expense original leaves the refund in daily and reports
    its ID in ``unresolved_refund_ids``; explicitly assigning that unresolved
    refund to another bucket raises instead of silently ignoring the choice.
    Non-refund relationships do not inherit categories. Cyclic refund links
    are invalid. All IDs, expense amounts, and nonzero expense dates are
    validated across the full collection before selecting the window.

    ``event_buckets`` and unresolved IDs preserve input order and include only
    the window's nonzero expenses. All four buckets are present in each totals
    mapping. Monthly mappings include every calendar month intersecting the
    window, with zero for months without expenses; this makes no assertion
    about source coverage. The input and its nested values are never modified.
    """
    start_day = _calendar_date(start, "start")
    end_day = _calendar_date(end_exclusive, "end_exclusive")
    if start_day >= end_day:
        raise ValueError("start must precede end_exclusive")

    explicit: dict[str, Bucket] = {}
    for bucket, ids in (
        ("trip", _explicit_ids(trip_ids, "trip_ids")),
        ("durable", _explicit_ids(durable_ids, "durable_ids")),
        ("fixed", _explicit_ids(fixed_ids, "fixed_ids")),
    ):
        for event_id in sorted(ids):
            if event_id in explicit:
                raise ValueError(f"Event ID occurs in multiple explicit buckets: {event_id}")
            explicit[event_id] = bucket

    by_id: dict[str, Mapping] = {}
    amounts: dict[str, int] = {}
    dates: dict[str, date] = {}
    for event in events:
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Every event must have a nonempty string ID")
        if event_id in by_id:
            raise ValueError(f"Duplicate event ID: {event_id}")
        amount = event.get("expense_minor", 0)
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise TypeError(f"expense_minor must be integer minor units: {event_id}")
        by_id[event_id] = event
        amounts[event_id] = amount
        if amount:
            dates[event_id] = _calendar_date(event.get("date"), f"date for {event_id}")

    for event_id in explicit:
        if event_id not in by_id:
            raise ValueError(f"Explicit bucket references missing event ID: {event_id}")
        if amounts[event_id] == 0:
            raise ValueError(f"Explicit bucket references a non-expense event: {event_id}")

    resolved: dict[str, Bucket] = {}
    resolving: set[str] = set()
    unresolved: set[str] = set()

    def resolve(event_id: str) -> Bucket:
        if event_id in resolved:
            return resolved[event_id]
        if event_id in resolving:
            raise ValueError(f"Cyclic refund relationship: {event_id}")
        resolving.add(event_id)
        event = by_id[event_id]
        bucket = explicit.get(event_id, "daily")
        if amounts[event_id] < 0 and event.get("link_type") == REFUND_LINK:
            original_id = event.get("related_id")
            if (
                isinstance(original_id, str)
                and original_id in by_id
                and amounts[original_id] != 0
            ):
                bucket = resolve(original_id)
                if original_id in unresolved:
                    unresolved.add(event_id)
            else:
                bucket = "daily"
                unresolved.add(event_id)
            if event_id in explicit and explicit[event_id] != bucket:
                raise ValueError(
                    f"Explicit refund bucket conflicts with original expense: {event_id}"
                )
        resolving.remove(event_id)
        resolved[event_id] = bucket
        return bucket

    # Validate explicit refund choices even outside the requested window.
    for event_id, amount in amounts.items():
        if amount:
            resolve(event_id)

    bucket_totals = _empty_totals()
    monthly_buckets = {
        month: _empty_totals() for month in _window_months(start_day, end_day)
    }
    event_buckets: dict[str, Bucket] = {}
    for event_id, event_day in dates.items():
        if start_day <= event_day < end_day:
            bucket = resolved[event_id]
            event_buckets[event_id] = bucket
            bucket_totals[bucket] += amounts[event_id]
            monthly_buckets[event_day.isoformat()[:7]][bucket] += amounts[event_id]

    return {
        "event_buckets": event_buckets,
        "bucket_totals_minor": bucket_totals,
        "monthly_bucket_totals_minor": monthly_buckets,
        "monthly_totals_minor": {
            month: sum(totals.values()) for month, totals in monthly_buckets.items()
        },
        "total_expense_minor": sum(bucket_totals.values()),
        "unresolved_refund_ids": tuple(
            event_id for event_id in event_buckets if event_id in unresolved
        ),
    }
