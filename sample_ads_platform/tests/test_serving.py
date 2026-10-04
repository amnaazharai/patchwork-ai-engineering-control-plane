from ads_platform.serving import AdServer


def test_serves_highest_bidder(make_campaign, make_request):
    server = AdServer([make_campaign(id="low", bid_cpm=1.0), make_campaign(id="high", bid_cpm=4.0)])
    imp = server.serve(make_request())
    assert imp.campaign_id == "high"
    assert imp.price_cpm == 1.0


def test_records_spend(make_campaign, make_request):
    server = AdServer([make_campaign(id="c1", bid_cpm=2.0)])
    server.serve(make_request())
    assert server.budget.spent("c1") > 0


def test_stops_serving_when_budget_exhausted(make_campaign, make_request):
    # bid 2.0 CPM -> worst case 0.002 per impression; budget allows 5.
    server = AdServer([make_campaign(id="c1", bid_cpm=2.0, daily_budget=0.01)])
    served = [server.serve(make_request()) for _ in range(50)]
    assert sum(1 for s in served if s) <= 20
    assert server.budget.spent("c1") <= 0.01 + 1e-9


def test_no_match_returns_none(make_campaign, make_request):
    server = AdServer([make_campaign(countries={"US"})])
    assert server.serve(make_request(country="JP")) is None
