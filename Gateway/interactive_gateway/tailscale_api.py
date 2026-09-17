import httpx


class TailscaleAPI:
    def __init__(self, socket):
        self.client = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=socket),
            base_url="http://local-tailscaled.sock/localapi/v0/", timeout=2)

    async def status(self):
        result = await self.client.get("status")
        result.raise_for_status()
        return result.json()

    async def start(self, login_server, hostname, key):
        result = await self.client.post("start", json={"AuthKey": key,
            "UpdatePrefs": {"ControlURL": login_server, "Hostname": hostname, "WantRunning": True, "CorpDNS": False}})
        result.raise_for_status()
        # The pinned CLI explicitly triggers login after Start on fresh state.
        result = await self.client.post("login-interactive")
        result.raise_for_status()

    async def close(self):
        await self.client.aclose()
