# Roadmap

## Frequency capping (not started)

Advertisers want to limit how often one user sees the same campaign.

- `Campaign` gets an optional `frequency_cap: int | None` (impressions per user
  per day). `None` means uncapped.
- A `FrequencyCapper` tracks impressions per `(campaign_id, user_id)`.
- `AdServer` must skip campaigns whose cap the user has reached, so the next
  best campaign can win.

## Budget pacing (later)

Spread spend evenly across the day instead of spending as fast as possible.
