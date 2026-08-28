# OCI VM Manual Setup — SSH Interactive Connection with Dynamic IP Whitelisting

## 1. Architecture overview

The Scheduler (running on the OCI VM in the `api` container) dynamically manages
ingress rules on a dedicated Network Security Group (NSG) attached to the SSH
Gateway VM's VNIC. When a user requests an interactive SSH session, the Scheduler
reads the caller's source IP (preferring the first hop of `X-Forwarded-For`,
falling back to `request.client.host`) and adds an **INGRESS TCP** rule for that
IP on port `GATEWAY_SSH_PORT` (default **2222**) to the NSG. The SSH Gateway
container listens on 2222 and, when a connection closes, POSTs to the Scheduler's
`/interactive/connection/closed` callback with the source IP and the shared
`X-Gateway-Secret` header. The Scheduler then removes the NSG rule. A background
sweeper also removes rules whose TTL (`WHITELIST_TTL_SECONDS`, default 86400) has
expired, as a safety net for leaked entries.

## 2. Prerequisites / OCI networking

On the VM (via the OCI Console or CLI), perform the following:

1. Identify the **compartment** that will hold the resources and note its OCID
   (e.g. `ocid1.compartment.oc1..<unique-id>`).
2. Create a dedicated NSG for SSH gateway access, e.g. `gateway-ssh-nsg`, in that
   compartment. Record its OCID (e.g. `ocid1.networksecuritygroup.oc1..<unique-id>`).
3. **Attach the NSG to the Gateway VM's VNIC** (the primary VNIC of the VM that
   runs the `gateway` container). The NSG must be associated with the VNIC, not
   just the subnet, so the rules apply to traffic destined for that VM.
4. Ensure the NSG has **no `0.0.0.0/0` ingress rule for TCP 2222** (default-deny
   posture). The Scheduler will add per-IP `/32` rules on demand; a broad open
   rule would defeat the whitelist.
5.    Record the NSG OCID — it becomes `OCI_GATEWAY_NSG_OCID` in §4.

## 3. IAM / Instance Principal setup

The Scheduler authenticates to OCI using **Instance Principal** (no API keys or
config file mounted into the container). On the VM (OCI Console or CLI):

1. **Create a Dynamic Group** that matches the Scheduler VM instance. Use either:
   - `ALL {instance.id = 'ocid1.instance.oc1..<unique-id>'}` (specific instance), or
   - `ALL {instance.compartment.id = '<compartment-ocid>'}` (all instances in the
     compartment — broader, use with care).

   Replace `<compartment-ocid>` with the compartment OCID from §2.

2. **Create an IAM policy** in the tenancy (or compartment) granting the dynamic
   group permission to manage NSGs:

   ```
   allow dynamic-group <dg-name> to manage network-security-groups in compartment <compartment-name>
   ```

   Also add the broader networking family permission so the SDK can list VNICs
   and NSG rules:

   ```
   allow dynamic-group <dg-name> to use virtual-network-family in compartment <compartment-name>
   ```

   The `manage network-security-groups` permission covers listing, adding, and
   removing security rules on the NSG. The `use virtual-network-family` permission
   covers reading VNIC and NSG metadata needed by the SDK calls.

3. **Enable Instance Principal** on the VM (if not already): in the OCI Console,
   go to the instance → **Instance Principal** → **Enable**. The VM must be
   launched with a dynamic group membership and the above policy attached.

4. Verify from the VM host (not inside the container yet):

   ```bash
   curl -s -H "Authorization: Bearer Oracle" \
     http://169.254.2.254/opc/v1/instance/principal
   ```

   A successful response confirms Instance Principal is active.

## 4. Environment variables on the VM

Edit `Scheduler/.env` on the VM. The `api` service reads these via the
`environment:` block in `docker-compose.yml` (each is `${VAR:-default}`). The
`gateway` service reads a subset. **OCIDs must never be hardcoded in code** —
they come exclusively from environment variables.

| Variable | Where it goes | Purpose | Example | Secret? |
|---|---|---|---|---|
| `OCI_REGION` | Scheduler `.env` → `api` | OCI region for the SDK client | `us-ashburn-1` | No |
| `OCI_COMPARTMENT_OCID` | Scheduler `.env` → `api` | Compartment OCID (IAM scoping) | `ocid1.compartment.oc1..<...>` | No |
| `OCI_GATEWAY_NSG_OCID` | Scheduler `.env` → `api` | NSG OCID to add/remove rules on | `ocid1.networksecuritygroup.oc1..<...>` | No |
| `OCI_USE_INSTANCE_PRINCIPAL` | Scheduler `.env` → `api` | Enable Instance Principal auth | `true` | No |
| `GATEWAY_CALLBACK_SECRET` | Scheduler `.env` → `api` (as `GATEWAY_CALLBACK_SECRET`) **and** `gateway` (as `SCHEDULER_CALLBACK_SECRET`) | Shared secret for the gateway→scheduler callback | `openssl rand -hex 32` | **Yes** |
| `WHITELIST_TTL_SECONDS` | Scheduler `.env` → `api` | TTL for whitelist entries | `86400` | No |
| `WHITELIST_SWEEP_INTERVAL` | Scheduler `.env` → `api` | Sweep interval for expired entries | `300` | No |
| `GATEWAY_SSH_PORT` | Scheduler `.env` → `api` **and** `gateway` | SSH port the gateway listens on | `2222` | No |
| `GATEWAY_PUBLIC_HOST` | Scheduler `.env` → `api` **and** `gateway` | Public hostname/IP users SSH to | `gateway.example.com` | No |
| `SCHEDULER_API_URL` | `gateway` (optional) | Scheduler callback URL | `http://api:8000` | No |

Generate the shared secret on the VM:

```bash
openssl rand -hex 32
```

Paste the output into `GATEWAY_CALLBACK_SECRET` in `Scheduler/.env`. The
`docker-compose.yml` already maps this to `SCHEDULER_CALLBACK_SECRET` in the
`gateway` service, so the same value flows to both sides automatically.

## 5. Docker Compose deployment

On the VM, from the `Scheduler/` directory:

```bash
cd /path/to/Scheduler
docker compose build
docker compose up -d
```

### Critical: publish port 2222 on the host

The `gateway` service in `Scheduler/docker-compose.yml` currently only
**exposes** ports `8200` and `2222` internally — it does **not** publish `2222`
to the host VM. This means the host VM does **not** listen on 2222, so external
SSH clients cannot reach the gateway container, and the NSG whitelist rules are
meaningless (there is no host listener to accept the traffic).

**On the VM, edit `Scheduler/docker-compose.yml`** and add a `ports:` entry to
the `gateway` service so 2222 is published to the host:

```yaml
  gateway:
    build: ../gateway
    container_name: app-gateway
    restart: always
    devices:
      - /dev/net/tun:/dev/net/tun
    cap_add:
      - NET_ADMIN
      - NET_RAW
    volumes:
      - gateway-keys:/data/ssh-keys
    expose:
      - "8200"
      - "2222"
    ports:
      - "2222:2222"          # <-- ADD THIS LINE
    environment:
      ...
```

> **Alternative** (if you cannot edit compose): run a host-level forwarder on
> the VM:
> ```bash
> socat TCP-LISTEN:2222,fork,reuseaddr TCP:127.0.0.1:2222
> ```
> or an iptables DNAT rule. **Option (a) — adding `ports:` — is recommended.**

The gateway already has `/dev/net/tun`, `NET_ADMIN`, and `NET_RAW` in the compose
file (required for `tailscaled`); no change needed there.

### Verify dependencies inside containers

After `docker compose up -d`:

```bash
# OCI SDK present in the api container
docker exec app-api python -c "import oci; print(oci.__version__)"

# requests present in the gateway container
docker exec app-gateway python -c "import requests; print(requests.__version__)"

# Gateway callback secret is wired through
docker exec app-gateway env | grep SCHEDULER
```

## 6. Reverse proxy (nginx) configuration

The Scheduler reads the caller's source IP preferring the first hop of
`X-Forwarded-For`, falling back to `request.client.host` (see
`Scheduler/app/api/jobs_route.py`, `_get_source_ip`). If you place nginx in front
of the `api` container, it **must** forward the original client IP so the
whitelist targets the correct address.

On the VM, configure nginx (e.g. `/etc/nginx/sites-available/scheduler` or a
conf.d drop-in) with:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

The Scheduler takes the **first hop** of `X-Forwarded-For` (the leftmost entry),
which is the original client IP when nginx appends `$remote_addr`. If nginx is
not configured to set `X-Forwarded-For`, the Scheduler falls back to
`request.client.host` (the immediate peer), which may be the proxy itself.

## 7. Connectivity checks

On the VM:

1. **Scheduler API reachable from the gateway container:**
   ```bash
   docker exec app-gateway curl -s http://api:8000/scheduler/health
   ```
   Expect `{"status": "ok", "service": "scheduler"}`.

2. **Gateway API reachable from the api container:**
   ```bash
   docker exec app-api curl -s http://gateway:8200/health
   ```

3. **Host listener on 2222** (confirms the §5 fix):
   ```bash
   ss -tlnp | grep 2222
   ```
   Expect a listener (either the published container port or the `socat`
   forwarder).

4. **NSG rule visibility** (from the VM host, using the OCI CLI):
   ```bash
   oci network nsg security-rules list --nsg-id <OCI_GATEWAY_NSG_OCID>
   ```
   Before any session, there should be **no** INGRESS TCP 2222 rule for your IP.

## 8. End-to-end verification on the VM

1. **Whitelist on connect:** Trigger an interactive session request (e.g. via
   the UI or `POST /interactive/create`). Then list NSG rules:
   ```bash
   oci network nsg security-rules list --nsg-id <OCI_GATEWAY_NSG_OCID>
   ```
   Confirm an INGRESS TCP rule appears for your client IP with
   `source = "<your-ip>/32"`, `protocol = "6"`, port range `2222`, and
   description `temp ssh whitelist <your-ip>`.

2. **Whitelist removal on disconnect:** Close the SSH session. Re-list the NSG
   rules and confirm the per-IP rule has been removed.

3. **Sweeper cleanup:** Set a short TTL for testing by editing
   `Scheduler/.env` on the VM:
   ```
   WHITELIST_TTL_SECONDS=60
   WHITELIST_SWEEP_INTERVAL=30
   ```
   Restart the `api` container (`docker compose restart api`), trigger a session,
   then wait past the TTL and confirm the rule is swept away even if the
   callback did not fire.

4. **Callback secret rejection:** From the VM host, simulate a bad callback:
   ```bash
   curl -X POST http://localhost:8000/interactive/connection/closed \
     -H "X-Gateway-Secret: wrong" \
     -H "Content-Type: application/json" \
     -d '{"source_ip":"1.2.3.4"}'
   ```
   Expect HTTP **401** with `Invalid gateway secret`.

## 9. Troubleshooting

- **OCI SDK errors in `docker logs app-api`** (e.g. `Unauthorized` or
  `NotAuthorizedOrNotFound`): the dynamic group or IAM policy is missing or
  incorrect. Re-check §3 — the dynamic group must match the Scheduler VM
  instance, and the policy must grant `manage network-security-groups` and
  `use virtual-network-family` in the correct compartment.

- **Instance Principal not configured**: the `curl` check in §3 step 4 fails.
  Enable Instance Principal on the VM in the OCI Console and ensure the VM's
  dynamic group membership is correct.

- **Wrong IP whitelisted**: nginx is not setting `X-Forwarded-For`, so the
  Scheduler falls back to `request.client.host` (the proxy/loopback address).
  Fix the nginx config per §6.

- **NSG rules have no effect**: the NSG is not attached to the Gateway VM's
  VNIC (it must be on the VNIC, not just the subnet). Re-check §2 step 3.

- **Gateway cannot reach the Scheduler**: verify `SCHEDULER_API_URL` is set
  correctly in the `gateway` service (default `http://api:8000`). Check
  `docker exec app-gateway curl -s http://api:8000/health`.

- **Callback returns 401**: `GATEWAY_CALLBACK_SECRET` (api side) and
  `SCHEDULER_CALLBACK_SECRET` (gateway side) do not match. Both derive from the
  same `.env` value via `docker-compose.yml`; regenerate with
  `openssl rand -hex 32` and restart both containers.

- **Host not listening on 2222**: external SSH cannot connect. This is the §5
  issue — add `ports: - "2222:2222"` to the `gateway` service (or run the
  `socat`/iptables alternative) and restart the gateway container.
