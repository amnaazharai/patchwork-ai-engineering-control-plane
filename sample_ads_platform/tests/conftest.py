import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ads_platform.models import AdRequest, Campaign, Creative  # noqa: E402


@pytest.fixture
def make_campaign():
    def _make(id="c1", bid_cpm=2.0, daily_budget=10.0, **kwargs):
        creatives = kwargs.pop("creatives", [Creative(f"{id}-cr1", "Buy now", "https://example.com")])
        return Campaign(
            id=id,
            advertiser=kwargs.pop("advertiser", "acme"),
            bid_cpm=bid_cpm,
            daily_budget=daily_budget,
            creatives=creatives,
            **kwargs,
        )

    return _make


@pytest.fixture
def make_request():
    counter = {"n": 0}

    def _make(user_id="u1", country="US", device="mobile", keywords=()):
        counter["n"] += 1
        return AdRequest(
            request_id=f"r{counter['n']}",
            user_id=user_id,
            country=country,
            device=device,
            keywords=frozenset(keywords),
        )

    return _make
