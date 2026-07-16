#!/usr/bin/env bash
# Daily HyperTFT data pipeline, run from cron on the VM.
#
# Mirrors what the GitHub Actions workflow did, co-located with the database
# so the aggregator's reads never leave the machine:
#   1. resolve today's platform group from PLATFORM_ROTATION (day-of-year)
#   2. collect new matches
#   3. aggregate (incremental fold; deep pass when due)
#   4. ping the Cloudflare deploy hook so the static frontend rebuilds
set -euo pipefail

APP=/opt/hypertft
BACKEND=$APP/backend
PY=$APP/venv/bin/python
cd "$BACKEND"

# Export .env so PLATFORM_ROTATION (and optionally CLOUDFLARE_DEPLOY_HOOK_URL)
# are visible to this script; the Python processes read .env themselves.
set -a; . ./.env; set +a

echo "════ $(date -u +'%F %T') UTC — pipeline start ════"

PLATFORMS=$("$PY" scripts/select_platforms.py | sed 's/^platforms=//')
echo "platforms: $PLATFORMS"

# shellcheck disable=SC2086  # word-splitting the platform list is intended
"$PY" -m collector.main --platforms $PLATFORMS
"$PY" -m aggregator.main

if [ -n "${CLOUDFLARE_DEPLOY_HOOK_URL:-}" ]; then
    curl -fsS -X POST "$CLOUDFLARE_DEPLOY_HOOK_URL" >/dev/null \
        && echo "frontend rebuild triggered"
fi

echo "════ $(date -u +'%F %T') UTC — pipeline done ════"
