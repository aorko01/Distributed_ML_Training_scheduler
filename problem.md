# VS Code Remote-SSH sessions die at exactly 5.0s — root cause found, fix specified

Date: 2026-09-25. Repo: `/home/ubuntu/Distributed_ML_Training_scheduler`.
This file supersedes the 2026-09-20 Interactive-Editor note (that issue is closed).
This machine = Scheduler + Gateway + Headscale-Management. Worker host(s) and
Builder host are separate machines; client is macOS. Read this whole file
before touching anything — the bug is found, do not re-investigate from scratch.

## 1. TL;DR

Every long-lived SSH session is killed at **5.001–5.003s** (Access-measured)
by `asyncio.timeout(5)` in `Worker/interactive/broker.py:408-420`: the
`await self.relay_ssh(...)` call sits **inside** the 5s handshake-deadline
block, so the deadline cancels the whole relay instead of just the initial
read. Cancellation is swallowed silently (`except ... asyncio.CancelledError:
pass` → zero broker log lines), writers close, Access sees EOF
(`SSH_EXIT/closed`), gateway logs `backend-eof`. Single-shot `ssh host pwd`
(0.2s) always survives, which is why CLI checks look green while VS Code
(multi-second legs) always dies. **Fix = move the `SSH_OPEN` dispatch out of
the timeout block** (3-line diff, §6). Worker-side only; no scheduler/gateway/
client change required for this bug.

## 2. Symptom

VS Code Remote-SSH → "Connection closed" / `socketFactory.connect() failed`,
retries 5× then permanent failure. Plain `ssh <host> pwd` → `/workspace`,
exit 0, 0.2s, every time. (`navigator is now a global` + DEP0169 lines in the
VS Code log are extension-host noise — ignore.)

## 3. Architecture (one line per hop)

VS Code → local OpenSSH → `dml-ssh proxy` (per-leg fresh single-use grant) →
Scheduler `POST …/ssh-connection` (purpose=ssh) → Gateway WSS (ticket claim,
tailnet TCP 9000 dial) → Access `SSH_OPEN` → Worker unix broker
(`relay_ssh`) → fixed Docker-exec bridge → workload `127.0.0.1:2222` sshd.

## 4. Evidence trail (established — do NOT re-verify these)

1. **Grants innocent.** Scheduler logs: 16× `POST …/ssh-connection` → 200,
   zero 429/409 in-window. (Burst window 5/10s, migration `010`, is live.)
2. **Auth innocent.** Mac `ssh -v`: KEX ✅ → `Server accepts key` ✅ →
   channel open ✅ → `pwd` → `/workspace`, exit 0. Installed key == offered
   key, generation match. Workload sshd proven live via temp-key login
   (10001 + `/workspace`; temp key removed afterwards).
3. **Gateway innocent.** Sessions establish, KBs flow both ways, then
   `outcome=1000 reason=backend-eof` at 5.25–5.74s. Per `relay.py:51-57`,
   `backend-eof` is set in exactly one place: Access closed the tailnet TCP
   socket. Gateway's only 5s timer (lease renewal) yields different reasons.
   Two populations, use as classifier: `client-disconnect` + symmetric ~4KB
   = healthy manual probes; `backend-eof` + asymmetric 16–20KB out / 3–12KB
   back at ~5.3s = sick VS Code legs.
4. **Access log (assignment `710f1ce0`, container
   `dml-710f1ce0-…-access`): ten longs, ALL `outcome=SSH_EXIT
   detail=closed duration=5.001–5.003s`. No `SSH_UNAVAILABLE / SSH_TIMEOUT /
   SSH_PROTOCOL_ERROR / SSH_BUSY` anywhere. Two shorts (`0.938s`, `0.136s`)
   are the manual idle probes (client hung up first — healthy).
5. **Broker silence is the tell.** For assignment `710f1ce0` the broker logged
   `ssh relay closed` ONLY for the two shorts (byte-exact pair). The ten
   longs have zero broker lines: no relay-close, no key-install failure, no
   refusal — yet `relay_ssh` must log on every path that reaches the bridge,
   and pre-bridge refusals surface in Access as `SSH_UNAVAILABLE` (absent).
   A relay that produces no log and no error record = task was *cancelled*.
6. **5.001–5.003s × 10 = a timer, and 5.3 − 5.0 ≈ 0.3s = pre-Access setup**
   (grant + WSS + handshake). Gateway durations are measured from WSS accept;
   Access durations from its session start; the delta is the setup cost.

## 5. Root cause

`Worker/interactive/broker.py` (`handle`, ~line 408 in this checkout; worker
host tree may have drifted — confirm the same shape there):

```python
async with asyncio.timeout(5):          # intended: bound the initial read only
    kind, payload = await read_record(reader)
    ...
    if kind == Type.SSH_OPEN:
        await self.relay_ssh(reader, writer, payload)   # BUG: whole relay under the deadline
        return
```

At 5.0s the timeout cancels `relay_ssh`; `relay_ssh`'s
`except (EOFError, ConnectionError, asyncio.CancelledError): pass` swallows
it silently → `finally` closes relay + writers → Access EOF → `SSH_EXIT` →
gateway `backend-eof` → local `ssh` dies → VS Code "Connection closed".
This is the *same bug class* the file already documents for workspace
sessions (lines ~424-428: "Running workspace.run() under it cancelled every
session at ~5s") — the SSH path reintroduced it.

## 6. The fix (worker host)

Move the dispatch out; the timeout must bound only the initial read (mirror
the `HELLO` handling directly below it):

```python
async with asyncio.timeout(5):
    kind, payload = await read_record(reader)
    if not await asyncio.to_thread(self.healthy):
        await write_record(writer, Type.ERROR, json_bytes({"code": "UNAVAILABLE"}))
        return
    if kind == Type.PROBE and not payload:
        await write_record(writer, Type.READY)
        return
if kind == Type.SSH_OPEN:                       # <-- outside the timeout block
    await self.relay_ssh(reader, writer, payload)
    return
if kind == Type.HELLO:
    ...
```

Then on the worker host: reinstall/restart the worker
(`sudo bash Worker/join_worker.sh --registered` or equivalent), start a fresh
runtime, and re-run the VS Code flow. Expect legs to live past 5s; confirm
with one gateway row showing a long `client-disconnect` (or a sustained
session) instead of `backend-eof` at 5.3s.

## 7. Verify-fix checklist (each must hold)

- [ ] VS Code connects, opens `/workspace`, survives >60s idle + active use.
- [ ] Gateway shows no `backend-eof` at ~5.3s for real legs (only clean
      `client-disconnect` on user close).
- [ ] Access `SSH_EXIT` durations are spread (session lengths), not 5.00x.
- [ ] Parallel install + exec legs coexist (the original multi-leg scenario).
- [ ] Plain `ssh host pwd` still 0.2s-clean (no regression).

## 8. Rollout state (what's already live — don't redo)

- Scheduler: `INTERACTIVE_SSH_ENABLED=1`, SSH lifetime 14400; Management
  `HM_SSH_SESSION_MAX=14400`; Gateway `GW_ALLOW_CLI=1`; all images carry the
  SSH code; Postgres has 009/010 columns (`010` file also present for fresh
  builds); Management sqlite v2 with grant `purpose`.
- Revisions: `6fbfeeb7` = `IMAGE_READY`/`ssh_profile=v1` (use for tests).
- Live runtime at handoff: `c0ef2c8d` gen 2 (`READY`, ssh ready) — will need
  a fresh runtime after the worker restart anyway.
- Scheduler-side improvements already deployed: `_ssh_blocker()` 409 detail
  names the failing clause; `ssh-info` includes
  state/desired_state/failure; burst grants 5/10s.
- UI source has the SSH blocks (exact-ID connect command, how-to, hints) but
  `dist/` was NOT rebuilt here (needs Node ≥20) — rebuild + redeploy pending.
- dml-ssh client fixes (§3a error detail, §3b proxy teardown, 429 retry)
  were in the worker owner's unpushed tree at handoff — confirm pushed.
- Open follow-ups (not this bug): mount `./migrations` into the API service
  or document image-rebuild requirement (`010` never auto-applied because of
  this); `618d6868` FAILED 06:55 during an API recreate — suspected
  heartbeat-gap reap, unconfirmed (worker journal `705e4d01`); rule: no API
  recreates while a live runtime exists (volume+reload covers code changes).

## 9. Correlation keys

- Failing window runtimes: `57d39b8f` g2 (STOPPED, restart-timing — closed),
  `a279783c` g4 (STOPPED — needs owner journal `035b7097`),
  `14edf618` g3 (FAILED — check orphans), `618d6868` g1 (FAILED 06:55, see §8),
  `c0ef2c8d` g2 (READY/ssh-ready — the VS Code test runtime).
- Gateway backend-eof cluster: 06:59:01→07:01:17 UTC (sessions
  `5a823911, f0dd08f8, 8da5e357, cb6451be, b321561a, c515f2c4, 7477242d,
  93fb99f4, 3eba15fc, 381b343b, d2023121, e99a0b3f` — first two + last four
  same cluster; 05:46:55 `f182b6df` 179/156B is the same signature on an
  idle leg).
- Access outcomes: assignment `710f1ce0`, ten `SSH_EXIT/closed @5.00x` +
  two healthy shorts (07:05:39, 07:07:49).
