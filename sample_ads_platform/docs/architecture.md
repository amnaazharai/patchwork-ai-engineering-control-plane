# Architecture

Every `AdRequest` flows through `AdServer.serve`:

1. **Targeting** (`targeting.matches`) filters campaigns by country, device and
   keywords. Empty sets mean "no restriction".
2. **Budget** (`BudgetTracker.can_afford`) drops campaigns that cannot afford
   one more impression at their own bid (the worst-case clearing price).
3. **Auction** (`auction.run_auction`) is a second-price auction with a reserve
   price. Ties break on campaign id for determinism.
4. **Record**: the winning impression's cost (`price_cpm / 1000`) is charged to
   the campaign's daily budget and appended to `AdServer.impressions`.

## Conventions

- Standard library only; keep it that way.
- Money is a `float` in currency units; CPM is price per 1000 impressions.
- New eligibility rules belong in `AdServer._candidates`, each in its own
  module with unit tests.
