#!/usr/bin/env bash
# One-shot provisioning for the HyperTFT backend on a GCP e2-micro (Debian 12).
# Run as root: sudo bash setup.sh
#
# Installs and configures, tuned for 1 GB RAM:
#   - 2 GB swap file (deep aggregation passes need the headroom)
#   - PostgreSQL (localhost only) with conservative memory settings
#   - Redis (localhost only, 64 MB LRU cache)
#   - Python venv + app under /opt/hypertft, run by the 'hypertft' user
#   - systemd service for the API (uvicorn on 127.0.0.1:8000)
#   - Caddy reverse proxy (automatic HTTPS once a domain is configured)
#   - Daily pipeline cron + nightly pg_dump backups (7 kept)
#
# Idempotent-ish: safe to re-run after a partial failure.
set -euo pipefail

APP_DIR=/opt/hypertft
APP_USER=hypertft
DB_NAME=hypertft
DB_USER=hypertft
REPO_URL=https://github.com/mk-gg/hypertft.git

echo "── swap ─────────────────────────────────────────────────────────────"
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "── packages ─────────────────────────────────────────────────────────"
apt-get update
apt-get install -y --no-install-recommends \
    postgresql postgresql-contrib redis-server \
    python3 python3-venv python3-pip git curl \
    debian-keyring debian-archive-keyring apt-transport-https

# Caddy (official repo)
if ! command -v caddy >/dev/null; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update && apt-get install -y caddy
fi

echo "── postgres (1 GB RAM tuning, localhost only) ───────────────────────"
PG_CONF_DIR=$(dirname "$(ls /etc/postgresql/*/main/postgresql.conf | head -1)")/conf.d
mkdir -p "$PG_CONF_DIR"
cat > "$PG_CONF_DIR/hypertft.conf" <<'EOF'
# Tuned for a 1 GB e2-micro sharing RAM with Redis + the API.
listen_addresses = 'localhost'
max_connections = 25
shared_buffers = 128MB
work_mem = 8MB
maintenance_work_mem = 48MB
effective_cache_size = 384MB
EOF
systemctl restart postgresql

DB_PASS=$(openssl rand -hex 16)
sudo -u postgres psql -v ON_ERROR_STOP=1 <<EOF
DO \$\$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
        CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
    ELSE
        ALTER ROLE ${DB_USER} PASSWORD '${DB_PASS}';
    END IF;
END \$\$;
SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec
EOF

echo "── redis (64 MB LRU, localhost only) ────────────────────────────────"
# Debian redis reads a single conf file; append the tuning idempotently.
grep -q '^# hypertft tuning' /etc/redis/redis.conf || cat >> /etc/redis/redis.conf <<'EOF'

# hypertft tuning
maxmemory 64mb
maxmemory-policy allkeys-lru
EOF
systemctl restart redis-server

echo "── app user + checkout + venv ───────────────────────────────────────"
id -u $APP_USER >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash $APP_USER
if [ ! -d $APP_DIR/.git ]; then
    git clone "$REPO_URL" $APP_DIR
fi
chown -R $APP_USER:$APP_USER $APP_DIR
sudo -u $APP_USER python3 -m venv $APP_DIR/venv
sudo -u $APP_USER $APP_DIR/venv/bin/pip install --upgrade pip
sudo -u $APP_USER $APP_DIR/venv/bin/pip install -r $APP_DIR/backend/requirements.txt

echo "── .env ─────────────────────────────────────────────────────────────"
ENV_FILE=$APP_DIR/backend/.env
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<EOF
RIOT_API_KEY=FILL-ME-IN
DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@localhost:5432/${DB_NAME}
REDIS_URL=redis://localhost:6379/0
CORS_ORIGINS=["https://FILL-ME-IN"]
PLATFORM_ROTATION=na1 euw1|kr sg2|vn2 oc1
TARGET_MATCHES_PER_RUN=500
EOF
    chown $APP_USER:$APP_USER "$ENV_FILE"
    chmod 600 "$ENV_FILE"
fi

echo "── systemd: API service ─────────────────────────────────────────────"
install -m 644 $APP_DIR/backend/deploy/gcp/hypertft-api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hypertft-api

echo "── caddy reverse proxy ──────────────────────────────────────────────"
if ! grep -q hypertft /etc/caddy/Caddyfile 2>/dev/null; then
    cat > /etc/caddy/Caddyfile <<'EOF'
# hypertft — replace with your API domain, then: systemctl restart caddy
api.example.com {
    reverse_proxy 127.0.0.1:8000
}
EOF
fi
systemctl enable --now caddy

echo "── pipeline cron + backups ──────────────────────────────────────────"
mkdir -p /var/log/hypertft /var/backups/hypertft
chown $APP_USER:$APP_USER /var/log/hypertft /var/backups/hypertft
chmod +x $APP_DIR/backend/deploy/gcp/run_pipeline.sh
cat > /etc/cron.d/hypertft <<EOF
# Daily data pipeline (collect → aggregate → frontend rebuild hook)
0 6 * * * $APP_USER $APP_DIR/backend/deploy/gcp/run_pipeline.sh >> /var/log/hypertft/pipeline.log 2>&1
# Nightly database backup, keep the last 7
30 4 * * * $APP_USER pg_dump -Fc -d ${DB_NAME} -f /var/backups/hypertft/${DB_NAME}-\$(date +\%u).dump
EOF

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo " Done. Database password (also written into backend/.env):"
echo "   ${DB_PASS}"
echo ""
echo " Next steps:"
echo "   1. Edit ${ENV_FILE}  (RIOT_API_KEY, CORS_ORIGINS)"
echo "   2. Set your domain in /etc/caddy/Caddyfile, then: systemctl restart caddy"
echo "   3. Restore data (see README.md), then: systemctl restart hypertft-api"
echo "════════════════════════════════════════════════════════════════════"
