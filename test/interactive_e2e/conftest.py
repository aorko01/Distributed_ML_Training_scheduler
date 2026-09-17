import asyncio
from pathlib import Path
import time

import httpx
import pytest


def pytest_addoption(parser):
    parser.addoption("--deliberate-failure", action="store_true", default=False)


def pytest_collection_finish(session):
    if not session.items:
        raise pytest.UsageError("required interactive E2E selected zero tests")


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter and reporter.stats.get("skipped"):
        session.exitstatus = 1


class Controller:
    def headers(self, user=None):
        name = user or "fixture"
        return {"Authorization": "Bearer " + Path("/secrets/" + name).read_text().strip(), **({"x-user": user} if user else {})}

    async def request(self, path, body=None, user=None, method="POST", expected=200):
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.request(method, "http://controller:8040/" + path, json=body, headers=self.headers(user))
        if response.status_code != expected:
            # Do not dump request/response keys or tickets into pytest artifacts.
            raise AssertionError(f"controller returned {response.status_code}; expected {expected}; path {path}")
        return response.json()

    async def internal(self, method, path, body=None, role="controller", expected=200, headers=None):
        return await self.request("fixture/management", {"method": method, "path": path, "body": body, "role": role, "headers": headers}, expected=expected)

    async def ticket(self, user="user-a", resource="resource-a", generation=None, expected=200):
        body = {"resource_id": resource}
        if generation:
            body["generation"] = generation
        return await self.request("grants", body, user=user, expected=expected)

    async def agent(self, name, path, body=None, method="POST"):
        return await self.request("fixture/agent", {"name": name, "path": path, "body": body, "method": method})


async def wait(fn, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = await fn()
            if value:
                return value
        except (AssertionError, httpx.TransportError):
            pass
        await asyncio.sleep(0.25)
    raise AssertionError("bounded fixture deadline exceeded")


@pytest.fixture(scope="session")
def controller():
    return Controller()


@pytest.fixture(scope="session")
def records(controller):
    async def prepare():
        await wait(lambda: controller.request("fixture/live", method="GET"))
        await controller.agent("gateway-agent", "serve")
        result = {}
        for name, user in (("a", "user-a"), ("b", "user-b")):
            result[name] = await controller.request("fixture/prepare", {"resource": "resource-" + name, "generation": "g1", "agent": name, "owner": user})
            await wait(lambda user=user, name=name: controller.ticket(user, "resource-" + name))
        return result
    return asyncio.run(prepare())
