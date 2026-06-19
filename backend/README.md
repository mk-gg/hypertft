# HyperTFT — Backend

## Project layout

```
hypertft-backend/
├── collector/
│   ├── config.py          ← env vars, tier list, platform→regional map
│   ├── rate_limiter.py    ← sliding-window rate limiter (20/s, 100/2min)
│   ├── riot_client.py     ← all Riot API calls (seeds, match IDs, match data)
│   ├── cdragon_client.py  ← Community Dragon patch/unit/trait/item data
│   ├── storage.py         ← Postgres writes (matches + patch metadata)
│   └── main.py            ← entry point, orchestrates all regions
├── aggregator/
│   ├── config.py          ← env vars, thresholds
│   ├── compute.py         ← pure Jaccard/comp/mutation/addition logic
│   ├── storage.py         ← reads matches, writes comp_stats per patch
│   └── main.py            ← entry point, groups by patch, runs aggregation
├── api/
│   ├── config.py          ← env vars, CORS origins
│   ├── dependencies.py    ← Postgres pool injection (FastAPI Depends)
│   ├── main.py            ← FastAPI app, startup, CORS
│   └── routers/
│       ├── comp.py        ← POST /comp, /comp/suggest, GET /comp/top, /comp/patches
│       └── meta.py        ← GET /meta, GET /meta/units
├── shared/
│   ├── db.py              ← Postgres connection pool + schema bootstrap
│   ├── cache.py           ← Redis cache-aside helper (+ invalidation)
│   ├── models.py          ← Pydantic models (request/response + data)
│   └── patch_map.py       ← internal version → TFT patch translation
├── .env.example
└── requirements.txt
```

---

## One-time setup

### 1. Python environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in:

```env
RIOT_API_KEY=RGAPI-your-key-here
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/hypertft
```

Everything else has sensible defaults. Full reference:

| Variable | Default | Description |
|---|---|---|
| `RIOT_API_KEY` | — | Riot Games API key (required) |
| `DATABASE_URL` | — | PostgreSQL connection string (required) |
| `REDIS_URL` | _(unset)_ | Redis connection string. Caching is disabled if unset. |
| `CACHE_TTL_SECONDS` | `3600` | TTL for cached API responses (safety net) |
| `TARGET_MATCHES_PER_RUN` | `500` | New matches added per collector run |
| `MATCHES_PER_PUUID` | `20` | Match history depth per player |
| `QUOTA_CHALLENGER` | `50` | PUUIDs from Challenger per region |
| `QUOTA_GRANDMASTER` | `50` | PUUIDs from Grandmaster per region |
| `QUOTA_MASTER` | `50` | PUUIDs from Master per region |
| `QUOTA_DIAMOND` | `75` | PUUIDs from Diamond I–IV per region |
| `QUOTA_EMERALD` | `75` | PUUIDs from Emerald I–IV per region |
| `QUOTA_PLATINUM_HIGH` | `75` | PUUIDs from Platinum I–II per region |
| `QUOTA_PLATINUM_LOW` | `75` | PUUIDs from Platinum III–IV per region |
| `SUPER_THRESHOLD` | `0.60` | Jaccard similarity for superset bucket |
| `MIN_N_COMP` | `3` | Min exact matches to include a comp |
| `API_HOST` | `0.0.0.0` | API bind host |
| `API_PORT` | `8000` | API bind port |

### 3. PostgreSQL

Point `DATABASE_URL` at any PostgreSQL 14+ instance — local, or a managed
provider (Railway, Supabase, Neon, RDS). All three services share one
connection string.

**Local (Docker):**

```bash
docker run -d --name hypertft-pg \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=hypertft \
  -p 5432:5432 postgres:16
```

**Managed:** create a Postgres instance and copy its connection URI into
`DATABASE_URL`.

The three tables and their indexes are created automatically on first run
(see `shared/db.py`) — no manual migration step:

| Table | Key | Notes |
|---|---|---|
| `matches` | `match_id` | Raw match JSON in a `JSONB` column; btree index on `tft_patch`. |
| `comp_stats` | `(patch, comp_key)` | Aggregated comps. `units_norm TEXT[]` carries a **GIN index** for array containment/overlap queries. |
| `meta` | `meta_key` | Key/value `JSONB` (patch roster, run + stats summaries). |

**GIN array index.** `comp_stats.units_norm` holds each comp's lowercased
unit list, indexed with `USING GIN`. This powers PostgreSQL array operators
— `@>` (contains) and `&&` (overlaps) — so the API filters comps by unit in
the database. `POST /comp/suggest` uses `units_norm && ARRAY[board…]` to fetch
only comps that share at least one unit with the board before computing exact
Jaccard similarity, instead of scanning every comp for the patch.

### 4. Redis (optional, recommended for production)

A cache-aside layer in front of the read-heavy API endpoints. **Entirely
optional** — if `REDIS_URL` is unset (or Redis is unreachable) the API simply
reads from PostgreSQL on every request.

**Local (Docker):**

```bash
docker run -d --name hypertft-redis -p 6379:6379 redis:7
```

Then set `REDIS_URL=redis://localhost:6379/0`. Managed options (Upstash, Redis
Cloud, Railway Redis) work the same way — paste their `redis://` / `rediss://`
URL.

**How it works (cache-aside):**

- **Cached endpoints** — `GET /comp/top` (keyed by `patch`/`limit`/`min_n`/
  `team_size`), `GET /comp/patches`, `GET /meta`, `GET /meta/units`. The
  personalized `POST /comp` and `POST /comp/suggest` are not cached.
- **Read** — on a request the API checks Redis first; on a miss it queries
  PostgreSQL, stores the result (TTL = `CACHE_TTL_SECONDS`), and returns it.
- **Invalidation** — the collector (new patch roster) and aggregator (new
  comps + summary) call `cache.invalidate()` after writing, which flushes the
  entire `hypertft:cache:*` namespace. The next read repopulates from the freshly
  indexed PostgreSQL data.
- **TTL** is only a safety net; correctness comes from explicit invalidation.

---

## Daily workflow

### Step 1 — Collect matches

Run from the **project root** (where `collector/` lives):

```bash
# All 16 platforms (Plat IV → Challenger across NA, EUW, KR, SEA, …)
python -m collector.main

# Specific regions only
python -m collector.main --platforms na1 euw1 kr

# Add exactly 200 new matches from NA
python -m collector.main --platforms na1 --limit 200
```

What this does:
- Fetches current patch data from Community Dragon → stores in the `meta` table
- Seeds PUUIDs from Challenger → Grandmaster → Master → Diamond → Emerald → Platinum
- Expands to match IDs, skips already-stored matches
- Downloads full match JSONs → stores in the `matches` table with a `tft_patch` field
- Checkpoints every 25 matches (crash-safe)

Expected output:
```
[INFO] collector.cdragon_client — Parsed set 17 (patch 17.8): 83 units …
[INFO] __main__ — === Platform: na1 (regional: americas) ===
[INFO] collector.riot_client — [na1] CHALLENGER: +250 PUUIDs → 250 total
[INFO] collector.riot_client — [na1] DIAMOND I page 1: +12 new → 262 total
[INFO] __main__ — [na1] 1192 unique match IDs discovered.
[INFO] __main__ — [na1] 208 new (skipping 984 existing).
[INFO] __main__ — [na1] Done — 208 new matches stored.
```

### Step 2 — Aggregate stats

```bash
python -m aggregator.main
```

What this does:
- Scans all matches from PostgreSQL
- Groups participants by `tft_patch` (e.g. `"17.8"`, `"17.7"`)
- Runs Jaccard similarity, comp bucketing, mutation and addition logic per patch
- Writes stats to the `comp_stats` table, keyed `(patch, comp_key)`
- Updates the `meta` table with the patch list and summary counts

Expected output:
```
[INFO] aggregator.main — 12480 participants from 984 matches.
[INFO] aggregator.main — Found 3 patches: 17.6, 17.7, 17.8
[INFO] aggregator.main — Aggregating patch 17.8 — 9200 participants …
[INFO] aggregator.main — Patch 17.8 — 312 comps written.
[INFO] aggregator.main — Aggregating patch 17.7 — 2100 participants …
[INFO] aggregator.main — Patch 17.7 — 198 comps written.
```

### Step 3 — Run the API

```bash
uvicorn api.main:app --reload --port 8000
```

API docs (Swagger UI): **http://localhost:8000/docs**
ReDoc: **http://localhost:8000/redoc**

---

## API reference

### Health

```
GET /health
→ { "status": "ok" }
```

### Meta

```
GET /meta
→ patch, set_number, total_matches, total_participants,
   total_comps, last_updated, regions, available_patches

GET /meta/units
→ full unit / trait / item roster for current patch
```

### Comp — patch filtering

All comp endpoints accept an optional `?patch=` query parameter.
**Omit it to always get the latest patch.**

```
GET /comp/patches
→ { "latest": "17.8", "patches": ["17.8", "17.7", "17.6"] }

GET /comp/top
GET /comp/top?patch=17.7
GET /comp/top?patch=17.8&limit=50&min_n=10&team_size=7
→ { patch, total, comps: [ {units, exact_avg, exact_n, ...} ] }

POST /comp
POST /comp?patch=17.7
Body: { "units": ["Ahri", "LeBlanc", "Nami"], "similarity_threshold": 0.60 }
→ { units, exact: {avg, n}, superset: {avg, n}, mutations, additions }

POST /comp/suggest
POST /comp/suggest?patch=17.8
Body: { "units": ["Ahri", "Nami"], "limit": 6 }   # threshold optional (auto-scales)
→ { board, patch, threshold_used, superset_avg, superset_n,
    suggested_comps, additions, mutations, exact_items, super_items }
```

`/comp/suggest` is backed by the GIN array index: it pulls only comps whose
`units_norm` overlaps the board (`&&`) before scoring exact similarity.

### Patch version translation

Match data contains `game_version` like `"Version 16.15.629.7318 (…)"`.
The collector extracts `16.15` and maps it to TFT patch `17.8` using
`shared/patch_map.py`. This mapping covers patches 16.2 through 19.1.

---

## Production deployment (Railway)

1. Push this folder to a GitHub repository
2. Go to **railway.app → New project → Deploy from GitHub**
3. Add the **PostgreSQL** plugin — Railway injects a `DATABASE_URL` variable
   the services pick up automatically
4. Add the remaining `.env` values (e.g. `RIOT_API_KEY`) as Railway variables
5. Set the start command:
   ```
   uvicorn api.main:app --host 0.0.0.0 --port $PORT
   ```

### Automated nightly collection

Add a second Railway service (cron) with this command:
```bash
python -m collector.main && python -m aggregator.main
```
Schedule: `0 3 * * *` (3 AM UTC daily)

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `connection refused` / `could not connect to server` | Postgres not running or wrong `DATABASE_URL` | Start Postgres and verify the connection string |
| `password authentication failed` | Wrong credentials in `DATABASE_URL` | Fix the user/password in the URI |
| `No PUUIDs collected` | Wrong platform string | Check `REGION_PLATFORM` in config |
| `API 404 comp not found` | No data for that patch | Run `GET /comp/patches` to see what's available |
| `Could not reach Community Dragon` | Network issue | Retry — CDragon is occasionally slow |
| `pydantic ValidationError: icon` | CDragon returns null icon | Already fixed in `shared/models.py` |
