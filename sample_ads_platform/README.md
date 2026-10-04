# Sample Ads Platform

A deliberately small ad-serving codebase used as the target repository for
Patchwork demos and tests. It is self-contained (standard library only) and
has its own test suite.

```
src/ads_platform/
  models.py      # Campaign, Creative, AdRequest, Impression
  targeting.py   # does a campaign match a request?
  budget.py      # daily budget tracking and pacing
  auction.py     # second-price auction
  serving.py     # AdServer: targeting -> pacing -> auction -> record spend
tests/           # pytest suite
docs/            # architecture notes and roadmap
```

Run the tests from this directory:

```bash
python -m pytest -q
```
