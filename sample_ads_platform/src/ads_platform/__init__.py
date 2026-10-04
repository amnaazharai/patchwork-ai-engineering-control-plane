"""A minimal ad-serving platform used as a target for Patchwork agents."""

from ads_platform.auction import AuctionResult, run_auction
from ads_platform.budget import BudgetTracker
from ads_platform.models import AdRequest, Campaign, Creative, Impression
from ads_platform.serving import AdServer
from ads_platform.targeting import matches

__all__ = [
    "AdRequest",
    "AdServer",
    "AuctionResult",
    "BudgetTracker",
    "Campaign",
    "Creative",
    "Impression",
    "matches",
    "run_auction",
]
