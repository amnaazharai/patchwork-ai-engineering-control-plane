import pytest

from ads_platform.models import Impression


def test_campaign_validates_bid(make_campaign):
    with pytest.raises(ValueError):
        make_campaign(bid_cpm=0)


def test_campaign_validates_budget(make_campaign):
    with pytest.raises(ValueError):
        make_campaign(daily_budget=-1)


def test_campaign_requires_creatives(make_campaign):
    with pytest.raises(ValueError):
        make_campaign(creatives=[])


def test_impression_cost_is_cpm_over_1000():
    imp = Impression("r1", "u1", "c1", "cr1", price_cpm=2.5)
    assert imp.cost == pytest.approx(0.0025)
