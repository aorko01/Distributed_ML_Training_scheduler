"""Privileged controller transport; errors never contain remote bodies/secrets."""

import os
from urllib.parse import urlparse
import httpx
from fastapi import HTTPException
from app.api.worker_execution_route_auth import read_secret


class ManagementClient:
    def __init__(self):
        url = os.environ["INTERACTIVE_MANAGEMENT_URL"]
        if urlparse(url).scheme != "https":
            raise ValueError("Management HTTPS required")
        secret = read_secret(os.environ["INTERACTIVE_CONTROLLER_SECRET_FILE"])
        if not 32 <= len(secret) <= 256 or len(set(secret)) < 8:
            raise ValueError("Invalid controller credential")
        self.client = httpx.Client(
            base_url=url.rstrip("/") + "/internal/v1/",
            verify=os.getenv("INTERACTIVE_MANAGEMENT_CA_FILE") or True,
            timeout=5,
            headers={"Authorization": "Bearer " + secret},
        )

    def call(self, method, path, body=None, key=None):
        try:
            response = self.client.request(
                method,
                path,
                json=body,
                headers={"Idempotency-Key": key} if key else None,
            )
            if response.status_code >= 400:
                raise HTTPException(
                    (
                        response.status_code
                        if response.status_code in (404, 409, 410, 422, 503)
                        else 503
                    ),
                    "Management unavailable",
                )
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "Management unavailable") from None

    def close(self):
        self.client.close()
