# MinIO Object Store

This directory contains a standalone deployment of MinIO to serve as the object storage backend for the Distributed ML Training Scheduler. It provides an S3-compatible API for storing job code (uploads) and training logs (outputs).

## Quickstart

1. **Configure Environment Variables**
   Create a `.env` file from the example:
   ```bash
   cp .env.example .env
   ```
   (Optional) Update `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` in `.env` for better security.

2. **Start the MinIO Server**
   ```bash
   docker compose up -d
   ```
   This will start the MinIO server, binding:
   - S3 API: `http://localhost:9000`
   - Console UI: `http://localhost:9001`

3. **Initialize Buckets**
   The scheduler and worker expect the `uploads` and `outputs` buckets to exist.
   Initialize them by running the provided script (make sure you have python installed and set up a venv):
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python init_buckets.py
   ```

## Services Integration

Other components of the system (Scheduler, Docker Image Builder, Worker) can connect to this MinIO instance using any standard S3 client or SDK (like the `minio` Python package or `boto3`). 

They will need to be configured with the S3 endpoint (e.g. `localhost:9000`) and the root credentials you set in the `.env` file. By using the S3 API, they can upload files and receive an object key representing the storage location in MinIO.
