# Durable workspace snapshots and training

## Deployment

Apply `Scheduler/migrations/011_workspace_durable_publication.sql` after the
earlier migrations. The snapshot API requires a dedicated MinIO `snapshots`
bucket; run `Object_store/init_buckets.py` after deploying the Object Store.
The generic object routes reject this bucket. Keep the existing Caddy
`/objects/*` route to the Object Store API so Workers and Builders can reach
scoped upload and download URLs over HTTPS.

Generate one printable random key with `openssl rand -hex 48` and install its
exact text in
`Scheduler/secrets/snapshot-service.key` and
`Object_store/secrets/snapshot-service.key` on their respective hosts. Set each
file to mode `0600`; its directory is excluded from Git. The compose files
mount these paths at `/run/dml-secrets/snapshot-service.key`. The Scheduler
uses `OBJECT_STORE_URL` to reach the same routed Object Store API; configure a
routable HTTPS URL on the Scheduler host. Workers and Builders receive scoped
capabilities, never this key or MinIO root credentials.

Deploy Object Store, Builder, Worker, Scheduler, and UI before enabling the
feature flags. Keep `WORKSPACE_SAVE_ENABLED=0` and
`WORKSPACE_TRAINING_SUBMISSION_ENABLED=0` until migration, cross-host save,
reopen, SSH, and batch validation pass. Enable Save for an operator canary
first; enable training submissions after a saved digest runs through VRAM
estimation and training. Existing VS Code Remote-SSH operation remains
available with both flags disabled.

Set the ordinary runtime cap consistently on Worker and Scheduler (600
seconds by default); align the SSH lifetime settings on both hosts. With Save
enabled, Scheduler records a deadline for ready runtimes and requests an
automatic Save and Stop three minutes before it. Worker holds its local cap
while the Save is active, and Scheduler retains the assignment through
publication or a terminal failure. The last successful revision remains the
recovery point if the host disappears suddenly.

## Operation and recovery

A Save begins as `REQUESTED`, captures the labelled workload, uploads an
immutable archive into the private bucket, and creates one SNAPSHOT revision.
Builder imports it, verifies its identity and configuration, pushes a unique
tag, resolves and pulls its digest, and marks the revision ready. The saved
workspace head advances only if it still matches the head pinned at request
time. A stale branch stays in history and does not displace a newer head.

`Save for Later` keeps the runtime and Remote-SSH session open. `Save and
Stop` waits for a successful publication before requesting runtime cleanup.
Submitting live training uses the same publication path and creates one Job
after assignment release. Training an existing ready revision creates a Job
directly after the runtime is gone. Both Jobs pin the published digest and
enter VRAM estimation without an ordinary image build.

The Worker keeps its archive and staging image until Scheduler accepts the
receipt. An interrupted upload can retry the same operation ID. On Worker or
host loss before durable receipt, Scheduler fails the Save and leaves the
previous saved head intact. A Builder retry uses a fresh attempt tag. Never
prune images or artifacts broadly: historical revisions and Jobs may still
reference them. Review retention references before exact-key deletion.

Record operation ID, revision ID, assignment ID, stage, size, and safe failure
code when investigating a stuck Save. Avoid logging signed capability tokens,
service keys, package output, or archive contents.

## Acceptance gate

On a disposable two-Worker deployment: edit a file and install pip and apt
packages through VS Code on Worker A; Save for Later and confirm SSH remains
connected; remove A's workload and staging artifacts; reopen on Worker B and
confirm the edited file, packages, fresh SSH host key, and absence of the old
runtime's SSH secrets. Train that exact revision and confirm the Job completes
with its saved digest. Inject one upload and one Builder push failure to verify
retry and previous-head preservation. Leave feature flags off until this gate
passes in the actual deployment.
