from ads_platform.targeting import matches


def test_untargeted_campaign_matches_everything(make_campaign, make_request):
    assert matches(make_campaign(), make_request(country="DE", device="desktop"))


def test_country_targeting(make_campaign, make_request):
    campaign = make_campaign(countries={"US"})
    assert matches(campaign, make_request(country="US"))
    assert not matches(campaign, make_request(country="FR"))


def test_device_targeting(make_campaign, make_request):
    campaign = make_campaign(devices={"desktop"})
    assert not matches(campaign, make_request(device="mobile"))


def test_keyword_targeting_needs_overlap(make_campaign, make_request):
    campaign = make_campaign(keywords={"shoes", "running"})
    assert matches(campaign, make_request(keywords={"running"}))
    assert not matches(campaign, make_request(keywords={"cooking"}))


def test_inactive_campaign_never_matches(make_campaign, make_request):
    assert not matches(make_campaign(active=False), make_request())
