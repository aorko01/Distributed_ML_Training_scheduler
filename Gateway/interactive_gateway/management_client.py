import asyncio
import ssl
import httpx


class ManagementError(Exception):
    def __init__(self, status=503):
        self.status = status
        super().__init__("control unavailable or authorization denied")


class ManagementClient:
    def __init__(self, settings, transport=None, secret=None):
        self.client = httpx.AsyncClient(base_url=settings.management_url + "/internal/v1/", timeout=2,
            verify=ssl.create_default_context(cafile=settings.ca_file), transport=transport,
            headers={"Authorization": "Bearer " + (secret or settings.gateway_secret)})

    async def request(self, method, path, body=None, headers=None, retry=False):
        for attempt in range(2 if retry else 1):
            try:
                response = await self.client.request(method, path, json=body, headers=headers)
                if response.is_error:
                    raise ManagementError(response.status_code)
                return response.json()
            except (httpx.TransportError, ValueError):
                if not retry or attempt:
                    raise ManagementError() from None
                await asyncio.sleep(0.05)

    async def ready(self):
        try:
            response = await self.client.get(self.client.base_url.copy_with(path="/health/ready"))
            return response.status_code == 200
        except httpx.TransportError:
            return False

    async def claim(self, ticket, request_id):
        return await self.request("POST", "sessions/claim", {"ticket": ticket, "request_id": request_id}, retry=True)

    async def renew(self, session_id):
        return await self.request("POST", "sessions/" + session_id + "/renew")

    async def release(self, session_id):
        return await self.request("POST", "sessions/" + session_id + "/release", retry=True)

    async def close(self):
        await self.client.aclose()
