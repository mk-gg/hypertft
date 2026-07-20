# HyperTFT

Data-driven Teamfight Tactics analytics — patch-by-patch tier lists, a comp
builder with live suggestions, and item recommendations, computed from ranked
matches collected across six regions (NA, EUW, KR, SEA).

**Live:** https://hypertft.mkgg.dev

## How it works

```
Riot API ──► collector ──► PostgreSQL ──► aggregator ──► API ──► frontend
             (slims at     match_slim     (incremental    FastAPI   Astro/React
              ingest)      + ledger        fold + deep    + Redis   on Cloudflare
                                           pass)          cache     Pages
```

- **Collector** pulls ranked matches daily (rotating region groups), extracts a
  compact per-match record at ingest, and discards the raw ~20 KB payload —
  storage stays flat as volume grows.
- **Aggregator** runs in two tiers: a cheap incremental fold of new matches
  into running placement stats every day, and a periodic deep pass that
  recomputes comp similarity (Jaccard), unit swap/addition deltas, and
  per-unit item stats over a rolling patch window.
- **API** serves pre-aggregated stats with GIN-indexed array queries for
  comp lookups and a Redis cache-aside layer; old patches stay browsable
  forever.

## Stack

Python · FastAPI · PostgreSQL (Supabase) · Redis · Astro + React + Tailwind ·
GitHub Actions · Cloudflare Pages

## Repository layout

| Path | What |
|---|---|
| [`backend/`](backend/README.md) | Collector, aggregator, API — setup, schema, and pipeline docs |
| [`frontend/`](frontend/) | Astro/React site (tier list, comp builder) |
| [`backend/deploy/gcp/`](backend/deploy/gcp/README.md) | Optional self-hosted deployment kit |
| `.github/workflows/` | Daily data-pipeline cron |

See [backend/README.md](backend/README.md) for full setup and architecture
details.
