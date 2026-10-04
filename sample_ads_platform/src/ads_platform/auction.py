"""Second-price (Vickrey) auction."""

from __future__ import annotations

from dataclasses import dataclass

from ads_platform.models import Campaign

RESERVE_PRICE_CPM = 0.50


@dataclass(frozen=True)
class AuctionResult:
    winner: Campaign
    clearing_price_cpm: float


def run_auction(
    candidates: list[Campaign], reserve_cpm: float = RESERVE_PRICE_CPM
) -> AuctionResult | None:
    """Highest bid wins and pays max(second-highest bid, reserve).

    Ties are broken by campaign id so results are deterministic.
    """
    eligible = [c for c in candidates if c.bid_cpm >= reserve_cpm]
    if not eligible:
        return None
    ranked = sorted(eligible, key=lambda c: (-c.bid_cpm, c.id))
    winner = ranked[0]
    second = ranked[1].bid_cpm if len(ranked) > 1 else reserve_cpm
    return AuctionResult(winner=winner, clearing_price_cpm=max(second, reserve_cpm))
