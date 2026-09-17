import asyncio
import random
import ssl
from datetime import datetime

import httpx


class ControlUnavailable(Exception):
    pass


class UnknownResult(ControlUnavailable):
    """A non-idempotent mutation may have succeeded. Never blindly retry."""


def field(record, snake):
    parts = snake.split("_")
    camel = parts[0] + "".join(p.title() for p in parts[1:])
    return record.get(camel, record.get(snake))


def parse_node(record):
    key = field(record, "pre_auth_key")
    if not isinstance(key, dict) or not str(key.get("id", "")).isdigit():
        raise ValueError("node missing exact enrollment association")
    if not str(record.get("id", "")).isdigit() or not isinstance(record.get("tags"), list):
        raise ValueError("invalid pinned node schema")
    ips = field(record, "ip_addresses")
    if not isinstance(ips, list) or not ips or record.get("online") not in (True, False):
        raise ValueError("node missing membership fields")
    expiry = record.get("expiry")
    if expiry:
        expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00")).timestamp()
    return {"id": str(record["id"]), "key_id": str(key["id"]), "tags": record["tags"],
            "ips": ips, "online": record["online"], "expiry": expiry,
            "ephemeral": key.get("ephemeral", False), "reusable": key.get("reusable", False)}


class HeadscaleClient:
    def __init__(self, settings, transport=None):
        verify = ssl.create_default_context(cafile=settings.ca_file)
        self.client = httpx.AsyncClient(base_url=settings.headscale_url, verify=verify, timeout=3,
            headers={"Authorization": "Bearer " + settings.headscale_key}, transport=transport)

    async def request(self, method, path, payload=None, safe=False):
        attempts = 3 if safe else 1
        for attempt in range(attempts):
            try:
                response = await self.client.request(method, "/api/v1/" + path, json=payload)
                if safe and method != "GET" and response.status_code == 404:
                    return {}
                if response.status_code in (401, 403):
                    raise ControlUnavailable("administrative credentials rejected")
                if response.status_code >= 500:
                    raise httpx.ReadError("control unavailable")
                if response.is_error:
                    raise ControlUnavailable("administrative operation rejected")
                return response.json() if response.content else {}
            except (httpx.TransportError, ValueError) as error:
                if not safe:
                    raise UnknownResult("administrative mutation result unknown") from None
                if attempt == attempts - 1:
                    raise ControlUnavailable("administrative read/cleanup unavailable") from None
                await asyncio.sleep(min(0.1 * 2**attempt + random.uniform(0, 0.05), 0.5))

    async def create_key(self, tags, ephemeral, expiry):
        result = await self.request("POST", "preauthkey", {"reusable": False, "ephemeral": ephemeral,
            "expiration": expiry, "acl_tags": tags})
        key = field(result, "pre_auth_key")
        if not isinstance(key, dict) or not str(key.get("id", "")).isdigit() or not key.get("key"):
            raise UnknownResult("creation response missing key association")
        return str(key["id"]), key["key"]

    async def nodes(self):
        result = await self.request("GET", "node", safe=True)
        # Unrelated CLI/OIDC nodes may have no pre-auth key. They are never adopted.
        parsed = []
        for record in result.get("nodes", []):
            try:
                parsed.append(parse_node(record))
            except ValueError:
                continue
        return parsed

    async def cleanup(self, key_id, node_ids):
        for node_id in node_ids:
            await self.request("DELETE", "node/" + str(node_id), safe=True)
        if key_id:
            await self.request("DELETE", "preauthkey?id=" + str(key_id), safe=True)

    async def policy(self):
        return (await self.request("GET", "policy", safe=True))["policy"]

    async def close(self):
        await self.client.aclose()
