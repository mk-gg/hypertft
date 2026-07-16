# Deploying HyperTFT on a GCP Always-Free e2-micro

Everything (PostgreSQL, Redis, the API, and the data pipeline) runs on one
`e2-micro` VM. Co-locating the pipeline with the database is the point: the
aggregator's reads become localhost traffic, so metered egress is only the
API's small JSON responses.

Always-Free constraints this setup is tuned for:

- 1 shared vCPU / **1 GB RAM** → Postgres+Redis memory limits + 2 GB swap
- 30 GB standard persistent disk (plenty: DB is ~200 MB steady-state)
- 1 GB/month network egress → pipeline must run **on the VM**, never remotely

---

## 1. One-time GCP console steps (manual)

1. Create a project at console.cloud.google.com (e.g. `hypertft`).
2. Attach a billing account (required even for Always Free), then create a
   **Budget alert** at $5 (Billing → Budgets & alerts) so a misconfiguration
   can't burn silently.
3. Compute Engine → Create instance:
   - Name `hypertft`, region **us-west1 / us-central1 / us-east1**
     (Always-Free regions only — anything else bills).
   - Machine type **e2-micro**.
   - Boot disk: **Debian 12**, 30 GB, **standard persistent disk**
     (balanced/SSD is NOT free).
   - Firewall: allow HTTP and HTTPS traffic.
4. Reserve the external IP (VPC network → IP addresses → Reserve static) —
   a static IP attached to a running Always-Free instance is free.
5. DNS: point `api.<your-domain>` (A record) at that IP. If the domain is on
   Cloudflare, set the record to **DNS only (grey cloud)** first so Caddy can
   obtain its Let's Encrypt certificate; you can enable the proxy afterwards.

## 2. Provision the VM

SSH in (Compute Engine → SSH button), then:

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/mk-gg/hypertft.git
cd hypertft/backend/deploy/gcp
sudo bash setup.sh
```

The script installs and configures PostgreSQL, Redis, Caddy, a 2 GB swap
file, a Python venv, the API systemd service, the pipeline cron, and nightly
`pg_dump` backups. It prints the generated database password at the end.

Then edit `/opt/hypertft/backend/.env` and fill in:

- `RIOT_API_KEY` — your Riot personal key
- `CORS_ORIGINS` — your frontend origin(s)
- `PLATFORM_ROTATION` — already set to `na1 euw1|kr sg2|vn2 oc1`

And set your API domain in `/etc/caddy/Caddyfile` (one line), then
`sudo systemctl restart caddy`.

## 3. Migrate the data from Supabase

The DB is small (~200 MB), so this is a single dump/restore:

```bash
pg_dump "<SUPABASE_SESSION_POOLER_URL>" \
  --no-owner --no-privileges -Fc -f /tmp/hypertft.dump
pg_restore --no-owner --no-privileges -d hypertft /tmp/hypertft.dump
```

Verify: `psql hypertft -c "SELECT count(*) FROM comp_stats"` matches Supabase.

## 4. Cut over

1. Frontend: set `PUBLIC_TFT_API_URL=https://api.<your-domain>` in the
   Cloudflare Pages env and redeploy.
2. GitHub Actions: disable the `Data pipeline` schedule (Actions → Data
   pipeline → ⋯ → Disable workflow) — the VM cron replaces it. Keep the
   workflow file for manual runs if you like.
3. Watch one pipeline run: `sudo journalctl -u hypertft-pipeline --since today`
   or `tail -f /var/log/hypertft/pipeline.log`.
4. After a clean week, pause/delete the Supabase project (export a final
   backup first).

## Day-2 operations

| Task | Command |
|---|---|
| API status / logs | `systemctl status hypertft-api` / `journalctl -u hypertft-api -f` |
| Run pipeline now | `sudo -u hypertft /opt/hypertft/backend/deploy/gcp/run_pipeline.sh` |
| Deploy new code | `cd /opt/hypertft && sudo -u hypertft git pull && sudo systemctl restart hypertft-api` |
| Backups | `/var/backups/hypertft/` (nightly, 7 kept) |
| Disk/memory | `df -h`, `free -m` (swap use during deep passes is normal) |
