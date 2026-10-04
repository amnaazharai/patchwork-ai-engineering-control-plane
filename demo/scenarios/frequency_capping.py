"""Recorded agent responses for the frequency-capping task.

The script plays out a realistic run: the builder's first attempt counts
impressions per user instead of per (campaign, user), a test it wrote catches
that, and the second attempt fixes it and gets approved.
"""

import json

from patchwork.models.schemas import EngineeringTask

TASK = EngineeringTask(
    id="ADS-142",
    title="Add per-user frequency capping",
    description=(
        "Implement frequency capping as described in docs/roadmap.md. Campaigns get an optional "
        "frequency_cap (impressions per user per day). AdServer must skip a campaign once a user "
        "has reached its cap so the next best campaign can win."
    ),
    acceptance_criteria=[
        "Campaign.frequency_cap defaults to None (uncapped) and rejects values < 1",
        "A capped campaign is not served to a user who reached the cap",
        "Other campaigns can still win for that user",
        "Caps are tracked per (campaign, user)",
        "Existing tests keep passing",
    ],
    protected_paths=["tests/conftest.py"],
)

PLAN = {
    "summary": (
        "Add a FrequencyCapper module, a frequency_cap field on Campaign, "
        "and an eligibility check in AdServer._candidates."
    ),
    "steps": [
        {
            "description": "Add optional frequency_cap to Campaign with validation",
            "files": ["src/ads_platform/models.py"],
            "rationale": "Roadmap specifies an optional per-campaign cap; None means uncapped.",
        },
        {
            "description": "Create FrequencyCapper tracking impressions per (campaign, user)",
            "files": ["src/ads_platform/frequency.py"],
            "rationale": "Architecture doc asks for each eligibility rule in its own module.",
        },
        {
            "description": "Skip capped campaigns in AdServer._candidates and record served impressions",
            "files": ["src/ads_platform/serving.py"],
            "rationale": "Filtering before the auction lets the next best campaign win.",
        },
        {
            "description": "Unit tests for the capper and the serving behaviour",
            "files": ["tests/test_frequency.py"],
            "rationale": "Cover every acceptance criterion.",
        },
    ],
    "risks": ["Counting per user instead of per (campaign, user) would cap unrelated campaigns"],
    "assumptions": ["Caps reset daily together with budgets; the reset job is out of scope"],
}

_MODELS_FIELD_OLD = "    active: bool = True\n\n    def __post_init__(self) -> None:\n"
_MODELS_FIELD_NEW = (
    "    active: bool = True\n"
    "    frequency_cap: int | None = None  # impressions per user per day\n\n"
    "    def __post_init__(self) -> None:\n"
    "        if self.frequency_cap is not None and self.frequency_cap < 1:\n"
    '            raise ValueError("frequency_cap must be at least 1")\n'
)

FREQUENCY_BUGGY = '''\
"""Per-user frequency capping."""

from __future__ import annotations

from collections import defaultdict


class FrequencyCapper:
    def __init__(self) -> None:
        self._seen: dict[str, int] = defaultdict(int)

    def count(self, campaign_id: str, user_id: str) -> int:
        return self._seen[user_id]

    def allows(self, campaign_id: str, user_id: str, cap: int | None) -> bool:
        return cap is None or self.count(campaign_id, user_id) < cap

    def record(self, campaign_id: str, user_id: str) -> None:
        self._seen[user_id] += 1

    def reset(self) -> None:
        self._seen.clear()
'''

FREQUENCY_FIXED = '''\
"""Per-user frequency capping."""

from __future__ import annotations

from collections import defaultdict


class FrequencyCapper:
    """Counts impressions per (campaign, user) for the current day."""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], int] = defaultdict(int)

    def count(self, campaign_id: str, user_id: str) -> int:
        return self._seen[(campaign_id, user_id)]

    def allows(self, campaign_id: str, user_id: str, cap: int | None) -> bool:
        return cap is None or self.count(campaign_id, user_id) < cap

    def record(self, campaign_id: str, user_id: str) -> None:
        self._seen[(campaign_id, user_id)] += 1

    def reset(self) -> None:
        self._seen.clear()
'''

TESTS = """\
import pytest

from ads_platform.frequency import FrequencyCapper
from ads_platform.serving import AdServer


def test_uncapped_by_default(make_campaign):
    assert make_campaign().frequency_cap is None


def test_cap_must_be_positive(make_campaign):
    with pytest.raises(ValueError):
        make_campaign(frequency_cap=0)


def test_capper_counts_per_campaign_and_user():
    capper = FrequencyCapper()
    capper.record("a", "u1")
    capper.record("a", "u1")
    assert capper.count("a", "u1") == 2
    assert capper.count("b", "u1") == 0
    assert capper.count("a", "u2") == 0


def test_capped_campaign_stops_for_that_user(make_campaign, make_request):
    server = AdServer([make_campaign(id="c1", frequency_cap=2)])
    served = [server.serve(make_request(user_id="u1")) for _ in range(4)]
    assert [s is not None for s in served] == [True, True, False, False]
    assert server.serve(make_request(user_id="u2")) is not None


def test_next_best_campaign_wins_after_cap(make_campaign, make_request):
    server = AdServer([
        make_campaign(id="high", bid_cpm=5.0, frequency_cap=1),
        make_campaign(id="low", bid_cpm=1.0, frequency_cap=1),
    ])
    first = server.serve(make_request(user_id="u1"))
    second = server.serve(make_request(user_id="u1"))
    assert (first.campaign_id, second.campaign_id) == ("high", "low")
"""


def _serving(original: str) -> str:
    s = original.replace(
        "from ads_platform.budget import BudgetTracker\n",
        "from ads_platform.budget import BudgetTracker\nfrom ads_platform.frequency import FrequencyCapper\n",
    )
    s = s.replace(
        "    def __init__(self, campaigns: list[Campaign], budget: BudgetTracker | None = None) -> None:\n"
        "        self.campaigns = {c.id: c for c in campaigns}\n"
        "        self.budget = budget or BudgetTracker()\n",
        "    def __init__(\n"
        "        self,\n"
        "        campaigns: list[Campaign],\n"
        "        budget: BudgetTracker | None = None,\n"
        "        frequency: FrequencyCapper | None = None,\n"
        "    ) -> None:\n"
        "        self.campaigns = {c.id: c for c in campaigns}\n"
        "        self.budget = budget or BudgetTracker()\n"
        "        self.frequency = frequency or FrequencyCapper()\n",
    )
    s = s.replace(
        "            if not matches(campaign, request):\n                continue\n",
        "            if not matches(campaign, request):\n                continue\n"
        "            if not self.frequency.allows(campaign.id, request.user_id, campaign.frequency_cap):\n"
        "                continue\n",
    )
    s = s.replace(
        "        self.budget.record(campaign.id, impression.cost)\n",
        "        self.budget.record(campaign.id, impression.cost)\n"
        "        self.frequency.record(campaign.id, request.user_id)\n",
    )
    return s


def _read(prompt: str, path: str) -> str:
    """Pull a file's current content out of the builder prompt."""
    marker = f"### {path}\n```\n"
    start = prompt.index(marker) + len(marker)
    return prompt[start : prompt.index("\n```\n", start)]


def _build(frequency_source: str, summary: str):
    def respond(system: str, prompt: str) -> str:
        models = _read(prompt, "src/ads_platform/models.py").replace(_MODELS_FIELD_OLD, _MODELS_FIELD_NEW)
        serving = _serving(_read(prompt, "src/ads_platform/serving.py"))
        return json.dumps(
            {
                "summary": summary,
                "edits": [
                    {"path": "src/ads_platform/models.py", "content": models},
                    {"path": "src/ads_platform/frequency.py", "content": frequency_source},
                    {"path": "src/ads_platform/serving.py", "content": serving},
                    {"path": "tests/test_frequency.py", "content": TESTS},
                ],
            }
        )

    return respond


REVIEW = {
    "approved": True,
    "summary": "Caps are tracked per (campaign, user) and enforced before the auction, so the next best campaign wins. Tests cover the acceptance criteria.",
    "findings": [
        {
            "severity": "minor",
            "category": "design",
            "message": "FrequencyCapper is never reset; a daily reset job will be needed alongside BudgetTracker.reset().",
            "path": "src/ads_platform/frequency.py",
        }
    ],
}

SCRIPT = {
    "planner": [json.dumps(PLAN)],
    "builder": [
        _build(FREQUENCY_BUGGY, "Add FrequencyCapper and enforce Campaign.frequency_cap in AdServer."),
        _build(FREQUENCY_FIXED, "Key frequency counts by (campaign, user) instead of user only."),
    ],
    "reviewer": [json.dumps(REVIEW)],
}
