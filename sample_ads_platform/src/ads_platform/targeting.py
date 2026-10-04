"""Targeting rules. An empty targeting set means "match everything"."""

from __future__ import annotations

from ads_platform.models import AdRequest, Campaign


def matches(campaign: Campaign, request: AdRequest) -> bool:
    if not campaign.active:
        return False
    if campaign.countries and request.country not in campaign.countries:
        return False
    if campaign.devices and request.device not in campaign.devices:
        return False
    if campaign.keywords and not (campaign.keywords & request.keywords):
        return False
    return True
