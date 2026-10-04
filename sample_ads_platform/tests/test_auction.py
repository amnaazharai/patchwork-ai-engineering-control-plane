import pytest

from ads_platform.auction import run_auction


def test_no_candidates():
    assert run_auction([]) is None


def test_single_bidder_pays_reserve(make_campaign):
    result = run_auction([make_campaign(bid_cpm=3.0)], reserve_cpm=0.5)
    assert result.clearing_price_cpm == pytest.approx(0.5)


def test_winner_pays_second_price(make_campaign):
    a = make_campaign(id="a", bid_cpm=5.0)
    b = make_campaign(id="b", bid_cpm=3.0)
    result = run_auction([b, a])
    assert result.winner.id == "a"
    assert result.clearing_price_cpm == pytest.approx(3.0)


def test_bids_below_reserve_are_dropped(make_campaign):
    assert run_auction([make_campaign(bid_cpm=0.1)], reserve_cpm=0.5) is None


def test_ties_broken_by_id(make_campaign):
    result = run_auction([make_campaign(id="z", bid_cpm=2.0), make_campaign(id="a", bid_cpm=2.0)])
    assert result.winner.id == "a"
