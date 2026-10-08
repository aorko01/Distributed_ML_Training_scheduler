# Object Store

This stack provides S3-compatible storage for job uploads and output files. It runs SeaweedFS 4.47 behind the existing FastAPI object-store API. The `MINIO_*` environment variable names are retained for compatibility with that API.

## Deploy

1. Set `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_PUBLIC_ENDPOINT`, and `MINIO_PUBLIC_SECURE` in `.env`. Use non-default credentials and restrict the file to its owner (`chmod 600 .env`). Set the two bucket names if they differ from `uploads` and `outputs`.
2. Run `docker compose up -d --build` from this directory.
3. Check `docker compose ps` and `curl --fail http://127.0.0.1:8010/health`.

The object-store API creates the `uploads` and `outputs` buckets when it starts. Data persists in the `object_data` Docker volume. Compose publishes the S3 API on `127.0.0.1:9000` and the object-store API on `127.0.0.1:8010`; Caddy serves both through `https://object.zulfiker.xyz` (S3 at `/`, API at `/objects/*` and `/health`). `MINIO_PUBLIC_ENDPOINT` must name that public S3 host so presigned URLs work for browsers and workers.

The API currently allows unauthenticated upload, listing, and download at `/objects/*`. Protect that route before storing sensitive data or accepting untrusted clients.
