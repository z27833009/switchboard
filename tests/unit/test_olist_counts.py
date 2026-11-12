"""Row count comparison is exact and names every mismatch."""

from switchboard.data.olist import compare_counts


def test_matching_counts_report_nothing() -> None:
    assert compare_counts({"orders": 3, "sellers": 1}, {"orders": 3, "sellers": 1}) == {}


def test_mismatch_and_missing_table_are_reported() -> None:
    assert compare_counts({"orders": 4}, {"orders": 3, "sellers": 1}) == {
        "orders": (3, 4),
        "sellers": (1, -1),
    }
