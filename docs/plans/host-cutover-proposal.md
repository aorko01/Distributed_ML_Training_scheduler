# Same-host cutover record

The separately authorized one-time cutover in section 15 of `plan.md` is applied
and verified. Installed Headscale remains external infrastructure; ordinary
`restart.sh` deployments do not change its configuration or restart its daemon.

## Applied configuration

- sslh receives public HTTPS on 443 and forwards TLS to Caddy on 8443. Public
  8443 was unreachable, so clients and the Origin allowlist use existing public
  `https://scheduler.zulfiker.xyz` on 443. No cloud firewall change is required.
- Caddy preserves Scheduler's TLS issuer and fallback to loopback 8000, routes
  `/v1/connect/*` to loopback 8030, and serves public Headscale coordination at
  `https://headscale.zulfiker.xyz`. Public `/api/*` and `/swagger*` return 404.
- Private administrative HTTPS binds only `172.19.0.1:8444`. Management alone maps
  the Headscale hostname to this bridge address and verifies the trusted certificate.
  An active and persistent INPUT rule permits TCP 8444 only from `172.19.0.0/16`
  on the existing `dml-control` bridge. No public 8443/8444 allow rule was added.
  Do not recreate this external network without updating the bind and firewall.
- Dedicated Headscale user `interactive-operator` owns the two service tags.
  `/etc/headscale/interactive.hujson` preserves ordinary-member connectivity and
  permits only Gateway-to-endpoint TCP 9000 for the service roles. Config/policy
  validation passed before the separately authorized one-time daemon restart.
- Stable file credentials are under `/etc/distributed-ml/interactive-secrets`.
  Files and `/etc/distributed-ml/interactive.env` are root-owned, service group
  10001, mode 0640; the secret directory is 0750. Values were not printed.
  The administrative key was created with a 90-day lifetime; rotate it before
  expiry through the documented protected-file procedure, never on every push.
- Consistent Headscale SQLite, original config/Caddy/firewall and SQL backups are
  retained under root-only `/etc/distributed-ml/backups`. The one-time Postgres
  copy migrated the original anonymous volume to `scheduler-postgres-data`,
  retaining both the source volume and SQL backup.

Protected environment contains these non-secret settings:

```dotenv
HM_HEADSCALE_URL=https://headscale.zulfiker.xyz:8444
HM_HEADSCALE_HOST_OVERRIDE=headscale.zulfiker.xyz
HM_HEADSCALE_HOST_IP=172.19.0.1
HM_LOGIN_SERVER=https://headscale.zulfiker.xyz
INTERACTIVE_SECRET_DIR=/etc/distributed-ml/interactive-secrets
GATEWAY_ID=gateway-main
GATEWAY_GENERATION=gateway-v1
HM_SIGNING_KID=primary
GW_ORIGINS=https://scheduler.zulfiker.xyz
GW_ALLOW_CLI=0
GATEWAY_PORT=8030
```

## Actual production verification

- Authenticated administrative HTTPS from the private Docker network returned 200;
  trusted coordination health returned 200; public administrative access returned 404.
- Two actual `sudo env REQUIRE_INTERACTIVE=1 bash restart.sh` deployments succeeded.
  Management and Gateway passed actual readiness. Scheduler public HTTPS OpenAPI
  returned 200 after deployment. Gateway retained Headscale node ID 163 across
  the second deployment; all 99 original Headscale node IDs remain present.
- `host_smoke.py` joined a real temporary endpoint using an API-issued single-use
  key, confirmed and registered it, renewed its lease, and waited for the actual
  Gateway probe. An API-issued ticket opened public trusted Caddy WSS, exactly
  relayed binary data containing NUL and non-UTF8 bytes, and failed replay with
  close code 4410. Exact endpoint cleanup reached REVOKED; disposable containers
  and their state/socket volumes were removed. Production state was retained.

## External push hook: remaining installation and verification

The user supplied the actual macOS `$HOME/Desktop/github-deploy/deploy.sh`.
Its Scheduler SSH command currently uses unprivileged `git pull && bash restart.sh`.
That cannot read the protected configuration and the VM checkout currently uses
`codex/interactive-infrastructure`, so implicit pull does not establish main deployment.

`deploy/external-deploy.sh.example` preserves the supplied builder/UI/Worker commands
and changes only Scheduler deployment: fetch main, checkout main, fast-forward it,
then invoke `sudo -n env REQUIRE_INTERACTIVE=1 bash restart.sh`. Noninteractive sudo
was verified on this VM. Install the reviewed replacement on the external machine
and retain executable permissions. That machine is outside the available workspace.

Changes are published on the review branch. Publishing to main and verifying the
subsequent external Actions deployment remain pending until the replacement is
installed. The registry-dependent builder gate retains its original main/manual
behavior and has not run on this review branch. No automatic deployment success
is claimed from a manual VM cutover.
