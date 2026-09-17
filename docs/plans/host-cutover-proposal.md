# Same-host cutover proposal

Implementation follows section 15 of `plan.md`. Installed Headscale remains
external infrastructure. These are one-time operator changes, not commands to
restart Headscale or rewrite its policy on every push.

## Current facts and prepared prerequisites

- Caddy's global HTTPS port is 8443. sslh owns public 443 and forwards TLS to
  Caddy; the existing Scheduler site is `scheduler.zulfiker.xyz:8443`.
- There is no Headscale virtual host in the installed Caddyfile. This explains
  the TLS failure for the advertised `https://headscale.zulfiker.xyz`.
- Headscale listens on `127.0.0.1:8080`. Its file-policy path is empty.
- Read-only CLI inspection found 99 existing nodes, all untagged, and no routes.
- Private network `dml-control` now exists: subnet `172.19.0.0/16`, host bridge
  address `172.19.0.1`.
- Stable controller/gateway/bootstrap, encryption and signing files are provisioned
  at `/etc/distributed-ml/interactive-secrets`, root-owned with service group 10001.
  The administrative key is not yet provisioned. Secret values were not printed.
- A root-only Postgres SQL backup completed under `/etc/distributed-ml/backups`.
  No database container has been stopped or migrated yet.
- The user confirms gateway routes and browser Origin should use
  `https://scheduler.zulfiker.xyz:8443`.

## Exact proposed changes

1. Merge `/tmp/dml-interactive-caddy-proposed.Caddyfile` into the installed Caddyfile.
   It preserves the Scheduler TLS issuer, sends `/v1/connect/*` to loopback 8030,
   retains Scheduler's fallback to 8000, and adds the missing public Headscale site.
   Public Headscale `/api/*` and `/swagger*` return 404. Administrative HTTPS is
   separate, bound only to `172.19.0.1:8444`, with the same trusted hostname/certificate.
   Never allow public admin access by matching loopback source: sslh makes public
   connections appear to originate from loopback. Caddy candidate validation passed.
2. Create dedicated Headscale user `interactive-operator` and provision
   `/tmp/dml-interactive-policy-proposed.hujson` as `/etc/headscale/interactive.hujson`.
   The policy has ordinary-member-to-member connectivity and only
   `tag:interactive-gateway` to `tag:interactive-endpoint` TCP 9000 for service roles.
   [Headscale 0.29.3 excludes tagged nodes from autogroup:member](https://github.com/juanfont/headscale/blob/v0.29.3/hscontrol/policy/v2/types.go#L760),
   so that legacy rule preserves ordinary-node connectivity without selecting new
   service identities. Live `headscale policy check` accepted the candidate.
3. Back up the installed Headscale database consistently and retain its original
   config. Change only `policy.path` to `/etc/headscale/interactive.hujson` and
   perform a one-time daemon restart to activate it. Config validation with the
   candidate policy passed. Preserve all existing nodes and settings; no reset,
   bulk key/node deletion or second production Headscale is proposed.
4. Capture one stable administrative key directly into a protected file, then
   provision the protected interactive environment with these non-secret settings:

   ```dotenv
   HM_HEADSCALE_URL=https://headscale.zulfiker.xyz:8444
   HM_HEADSCALE_HOST_OVERRIDE=headscale.zulfiker.xyz
   HM_HEADSCALE_HOST_IP=172.19.0.1
   HM_LOGIN_SERVER=https://headscale.zulfiker.xyz
   INTERACTIVE_SECRET_DIR=/etc/distributed-ml/interactive-secrets
   GATEWAY_ID=gateway-main
   GATEWAY_GENERATION=gateway-v1
   HM_SIGNING_KID=primary
   GW_ORIGINS=https://scheduler.zulfiker.xyz:8443
   GW_ALLOW_CLI=0
   GATEWAY_PORT=8030
   ```

   The hostname override applies only to management; the gateway sidecar uses
   public coordination. TLS validation remains enabled. Validate both paths from
   Docker before stopping any application for cutover.
5. Deliberately migrate Postgres's existing anonymous volume with the separate
   backup/copy script and invoke `REQUIRE_INTERACTIVE=1 ./restart.sh`. Retain the
   source volume and SQL backup. Ordinary pushes never recreate database storage.
   Desired Postgres credentials/database were compared in memory and match the
   current container; their values were not logged.
6. Verify real management/gateway readiness, preserved existing Headscale node IDs,
   stable gateway identity across restart and actual reverse-proxy WSS routing.

## External push hook

The user reports the push-to-main trigger is on another machine. The checked-in
Actions deployment invokes `$HOME/Desktop/github-deploy/deploy.sh` on a macOS
runner. Its contents and Linux invocation remain unverified here. Before publishing
to main, verify that external script updates this Linux checkout and invokes the
updated `restart.sh` with `REQUIRE_INTERACTIVE=1`; it must not retain any old
`down -v`, global stop, or database-volume deletion behavior.

Production cutover and automatic push deployment are not yet claimed complete.
