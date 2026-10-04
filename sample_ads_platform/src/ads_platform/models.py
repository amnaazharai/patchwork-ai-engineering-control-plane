"""Core domain objects."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Creative:
    id: str
    headline: str
    landing_url: str


@dataclass
class Campaign:
    id: str
    advertiser: str
    bid_cpm: float
    daily_budget: float
    creatives: list[Creative]
    countries: set[str] = field(default_factory=set)
    devices: set[str] = field(default_factory=set)
    keywords: set[str] = field(default_factory=set)
    active: bool = True

    def __post_init__(self) -> None:
        if self.bid_cpm <= 0:
            raise ValueError("bid_cpm must be positive")
        if self.daily_budget <= 0:
            raise ValueError("daily_budget must be positive")
        if not self.creatives:
            raise ValueError("a campaign needs at least one creative")


@dataclass(frozen=True)
class AdRequest:
    request_id: str
    user_id: str
    country: str
    device: str
    keywords: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Impression:
    request_id: str
    user_id: str
    campaign_id: str
    creative_id: str
    price_cpm: float

    @property
    def cost(self) -> float:
        """Cost of this single impression (CPM is price per 1000)."""
        return self.price_cpm / 1000.0
