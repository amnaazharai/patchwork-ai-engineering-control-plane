import pytest

from ads_platform.budget import BudgetTracker


def test_record_and_remaining():
    tracker = BudgetTracker()
    tracker.record("c1", 3.0)
    assert tracker.spent("c1") == 3.0
    assert tracker.remaining("c1", 10.0) == 7.0


def test_cannot_overspend():
    tracker = BudgetTracker()
    tracker.record("c1", 9.5)
    assert tracker.can_afford("c1", 10.0, 0.5)
    assert not tracker.can_afford("c1", 10.0, 0.6)


def test_negative_cost_rejected():
    with pytest.raises(ValueError):
        BudgetTracker().record("c1", -1)


def test_reset():
    tracker = BudgetTracker()
    tracker.record("c1", 1.0)
    tracker.reset()
    assert tracker.spent("c1") == 0.0
