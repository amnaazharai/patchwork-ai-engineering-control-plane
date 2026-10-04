"""Ad serving pipeline: targeting -> budget -> auction -> record."""

from __future__ import annotations

from ads_platform.auction import run_auction
from ads_platform.budget import BudgetTracker
from ads_platform.models import AdRequest, Campaign, Impression


class AdServer:
    def __init__(self, campaigns: list[Campaign], budget: BudgetTracker | None = None) -> None:
        self.campaigns = {c.id: c for c in campaigns}
        self.budget = budget or BudgetTracker()
        self.impressions: list[Impression] = []

    def _candidates(self, request: AdRequest) -> list[Campaign]:
        from ads_platform.targeting import matches

        out = []
        for campaign in self.campaigns.values():
            if not matches(campaign, request):
                continue
            # Worst case the campaign pays its own bid.
            if not self.budget.can_afford(
                campaign.id, campaign.daily_budget, campaign.bid_cpm / 1000.0
            ):
                continue
            out.append(campaign)
        return out

    def serve(self, request: AdRequest) -> Impression | None:
        result = run_auction(self._candidates(request))
        if result is None:
            return None
        campaign = result.winner
        creative = campaign.creatives[len(self.impressions) % len(campaign.creatives)]
        impression = Impression(
            request_id=request.request_id,
            user_id=request.user_id,
            campaign_id=campaign.id,
            creative_id=creative.id,
            price_cpm=result.clearing_price_cpm,
        )
        self.budget.record(campaign.id, impression.cost)
        self.impressions.append(impression)
        return impression
