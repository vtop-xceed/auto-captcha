# Deployment inventory

What is live today (pre-restructure) and how to rebuild it.

## Running pieces

| Piece | Runs on | Public name | Repo dir |
|---|---|---|---|
| Solver API (`/solve`,`/report`) | VPS behind `captop.duckdns.org` (DuckDNS + reverse proxy) | `captop.duckdns.org` | `service/` |
| Solver API proxy | Cloudflare Worker | `captop-proxy.sykik.workers.dev` | `workers/captop-proxy/` |
| Labeler site | PythonAnywhere (`sykik.pythonanywhere.com`) | via worker `captop.sykik.workers.dev` | `labeler/` |
| Labeler proxy | Cloudflare Worker | `captop.sykik.workers.dev` | `workers/captop-labeler/` |

The workers exist because the campus Fortinet firewall blocks/rates the
colleague origins directly but passes Cloudflare IPs.

## Docker host (solver API)

```sh
git clone git@github.com:vtop-xceed/auto-captcha.git
cd auto-captcha
scripts/fetch-model.sh <release-tag>
docker compose up -d --build
scripts/smoke.sh   # must end with "smoke OK"
```

- `docker-compose.yml` mounts `./reports:/app/reports`; without this
  every redeploy erases the failed-captcha intake (this is exactly what
  the pre-restructure build did wrong).
- `MODEL_PATH` and `REPORT_DIR` have docker defaults baked into the
  image; override via compose environment if relocating.
- Reverse proxy keeps redirecting port 3000 -> captop.duckdns.org.

## PythonAnywhere (labeler)

```sh
python training/package.py        # produces captop_deploy.zip at repo root
```
Upload zip in the PythonAnywhere console, unzip next to the WSGI file,
point WSGI at `labeler/app.py`. `init_db.py` recreates `labels.db`
against `data/captchas` when empty. Keep a routine copy of
`labels.db` (the live one on PythonAnywhere holds months of votes —
the `data/labels.db` in this repo is a placeholder).

## Workers

```sh
cd workers/captop-proxy   && npx wrangler deploy
cd workers/captop-labeler && npx wrangler deploy
```

Names (`captop-proxy`, `captop`) and origins must stay stable; the
extension and contributors hardcode those URLs.

## Vocabulary drift watchlist

- `workers/captop-proxy/cloudflare-worker.js` origin may include a
  vars override via wrangler; origins currently hardcoded in the script.
- `labeler/` SQLite write path is single-writer, fine at this scale.

## Secrets this repo must never hold

Docker host SSH key, DuckDNS account, wrangler token, PythonAnywhere
API key, any Chrome `.pem` private keys, model signing credentials.
