"""Pure expense analysis over already interpreted transaction events.

These helpers never load finance files or change the accounting events. Refund
amounts below are positive deductions for display, while source expense amounts
retain their original signs and dates.
"""

from collections import defaultdict

from budget_policy import select_budget_window


CONSUMPTION_EVENTS = frozenset({"消费", "车辆租赁付款"})


def large_expense_records(
    events, start_date: str, end_date_exclusive: str, threshold_minor: int = 200000
) -> list[dict]:
    """Return qualifying purchases and their observed, linked refunds.

    A purchase qualifies by its original positive expense, before refunds, in
    the [start_date, end_date_exclusive) window. Only refunds in that same window
    with an explicit original-purchase relationship are deducted. Fully refunded
    purchases remain visible, and unresolved refunds are not guessed onto them.

    Each result contains the unchanged original object as ``event``, integer
    ``gross_minor``, positive ``refund_minor``, ``net_minor`` (gross minus refund),
    and ``refund_ids`` ordered by date then ID. Results are ordered by descending
    gross expense, then date and ID. IDs must be unique across the entire input,
    including events outside the selected window. This is a presentation view;
    its net amounts must not be added to the underlying signed expense ledger.
    """
    if isinstance(threshold_minor, bool) or not isinstance(threshold_minor, int):
        raise TypeError("threshold_minor must be an integer number of minor units")
    if threshold_minor < 0:
        raise ValueError("threshold_minor must be nonnegative")

    source_events = list(events)
    seen_ids = set()
    for event in source_events:
        event_id = event["id"]
        if event_id in seen_ids:
            raise ValueError(f"Duplicate event ID: {event_id}")
        seen_ids.add(event_id)

    window_events = select_budget_window(
        source_events, start_date, end_date_exclusive
    )
    refunds_by_original = defaultdict(list)
    for event in window_events:
        if (
            event.get("expense_minor", 0) < 0
            and event.get("link_type") == "退款对应原消费"
            and event.get("related_id")
        ):
            refunds_by_original[event["related_id"]].append(event)

    purchases = sorted(
        (
            event
            for event in window_events
            if event.get("event") in CONSUMPTION_EVENTS
            and event.get("expense_minor", 0) > 0
            and event["expense_minor"] >= threshold_minor
        ),
        key=lambda event: (-event["expense_minor"], event["date"], event["id"]),
    )
    records = []
    for event in purchases:
        refunds = sorted(
            refunds_by_original[event["id"]],
            key=lambda refund: (refund["date"], refund["id"]),
        )
        gross_minor = event["expense_minor"]
        refund_minor = -sum(refund["expense_minor"] for refund in refunds)
        records.append(
            {
                "event": event,
                "gross_minor": gross_minor,
                "refund_minor": refund_minor,
                "net_minor": gross_minor - refund_minor,
                "refund_ids": [refund["id"] for refund in refunds],
            }
        )
    return records
