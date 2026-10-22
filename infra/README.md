# Infra notes

This directory records *production reality*, not aspiration.

- No IaC. Deployments are manual: docker compose on the VPS,
  PythonAnywhere upload for the labeler, wrangler for workers.
- DNS: `captop.duckdns.org` (A record -> VPS), workers under
  `*.workers.dev`. Consider migrating DuckDNS records to a domain the
  org owns before onboarding more maintainers.
- Backups (currently missing, add NOW):
  - `labels.db` on PythonAnywhere owns months of crowd votes.
  - `$REPORT_DIR` images on the VPS (volume is new; pre-restructure data
    dies with the container).
- Secrets to rotate when moving to the org:
  - anything referencing old personal endpoints
    (`captop.duckdns.org`, `*.sykik.workers.dev`,
    `sykik.pythonanywhere.com`)
  - wrangler account tokens, PythonAnywhere API keys, VPS SSH keys.
- Log analytics: `training/log_analytics.py` + `training/ip_geolocate.py`
  parse `labeler/logs/`; outputs land in `data/`.
