"""Daily budget tracking."""

from __future__ import annotations

from collections import defaultdict


class BudgetTracker:
    """Tracks spend per campaign for the current day."""

    def __init__(self) -> None:
        self._spent: dict[str, float] = defaultdict(float)

    def spent(self, campaign_id: str) -> float:
        return self._spent[campaign_id]

    def remaining(self, campaign_id: str, daily_budget: float) -> float:
        return max(0.0, daily_budget - self._spent[campaign_id])

    def can_afford(self, campaign_id: str, daily_budget: float, cost: float) -> bool:
        return self._spent[campaign_id] + cost <= daily_budget + 1e-9

    def record(self, campaign_id: str, cost: float) -> None:
        if cost < 0:
            raise ValueError("cost must be non-negative")
        self._spent[campaign_id] += cost

    def reset(self) -> None:
        self._spent.clear()
