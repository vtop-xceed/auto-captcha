# Workers

Cloudflare Workers deployed between VIT's Fortinet firewall and the backends.
Two independent deployments, each in its own directory:

| Directory | Serves | Origin | Live URL | Consumers |
|---|---|---|---|---|
| `captop-proxy/` | Solver API proxy | `captop.duckdns.org` | `captop-proxy.sykik.workers.dev` | Browser extension (`extension`) default endpoint |
| `captop-labeler/` | Labeling site proxy | `sykik.pythonanywhere.com` | `captop.sykik.workers.dev` | Labeler contributors |

Deploy with `npx wrangler deploy` from the respective directory.
Both exist because Fortinet unblocks/unrates Cloudflare IPs but not the
college-categorized origins directly.
