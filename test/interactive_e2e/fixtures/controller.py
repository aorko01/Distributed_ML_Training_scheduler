"""Test-only Scheduler substitute with an explicit A/B permission table."""
import asyncio
from contextlib import asynccontextmanager
import os
import secrets

import httpx
from fastapi import FastAPI, HTTPException, Request

PERMISSIONS = {"user-a": {"resource-a", "resource-terminal"}, "user-b": {"resource-b"}}
resources = {}
management_url = "http://management:8020/internal/v1/"


def credential(name):
    from pathlib import Path
    return Path("/secrets/" + name).read_text().strip()


async def management(method, path, body=None, role="controller", headers=None):
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.request(method, management_url + path, json=body,
            headers={"Authorization": "Bearer " + credential(role), **(headers or {})})
        if response.is_error:
            raise HTTPException(response.status_code, "management unavailable or denied")
        return response.json()


async def agent(name, path, body=None, method="POST"):
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.request(method, "http://" + ("tailscale" if name == "gateway-agent" else name) + ":8081/" + path, json=body,
            headers={"Authorization": "Bearer " + credential("fixture")})
        if response.is_error:
            raise HTTPException(503, "endpoint fixture unavailable")
        return response.json()


@asynccontextmanager
async def lifespan(app):
    async def leases():
        while True:
            for resource_id, record in list(resources.items()):
                try:
                    await management("POST", "resources/" + resource_id + "/lease", {"generation": record["generation"]})
                except (HTTPException, httpx.TransportError):
                    # Keep renewing after the deliberate management outages.
                    pass
            await asyncio.sleep(2)
    task = asyncio.create_task(leases())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def auth(request: Request, call_next):
    expected = "fixture" if request.url.path.startswith("/fixture/") else "user-a" if request.headers.get("x-user") == "user-a" else "user-b"
    if not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + credential(expected)):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "denied"}, status_code=401)
    request.state.user = request.headers.get("x-user")
    return await call_next(request)


@app.get("/fixture/live")
async def live():
    return {"status": "live"}


@app.post("/fixture/prepare")
async def prepare(request: Request):
    body = await request.json()
    resource, generation, name, owner = (body[k] for k in ("resource", "generation", "agent", "owner"))
    if resource not in PERMISSIONS.get(owner, set()) or name not in ("a", "b", "replacement", "terminal"):
        raise HTTPException(403, "denied")
    enrollment = await management("POST", "enrollments", {"role": "endpoint", "identity": resource, "generation": generation},
        headers={"Idempotency-Key": resource + "-" + generation})
    await agent(name, "join", enrollment)
    confirmed = await management("POST", "enrollments/" + enrollment["enrollment_id"] + "/confirm", {"generation": generation})
    registered = await management("PUT", "resources/" + resource + "/endpoint", {"owner": owner, "generation": generation,
        "enrollment_id": enrollment["enrollment_id"], "service": "terminal" if name == "terminal" else "echo", "protocol": "tcp-stream-v1", "port": 9000})
    resources[resource] = {"generation": generation, "agent": name, "enrollment_id": enrollment["enrollment_id"], "service": "terminal" if name == "terminal" else "echo"}
    return {"enrollment": enrollment, "membership": confirmed, "endpoint": registered}


@app.post("/grants")
async def grant(request: Request):
    body = await request.json()
    user = request.state.user
    resource = body.get("resource_id")
    if resource not in PERMISSIONS.get(user, set()):
        raise HTTPException(403, "denied")
    return await management("POST", "access-grants", {"user": user, "resource_id": resource,
        "generation": body.get("generation", resources.get(resource, {}).get("generation")), "service": resources.get(resource, {}).get("service", "echo"),
        "gateway_id": "gateway-main", "authorized": True})


@app.post("/fixture/management")
async def internal(request: Request):
    # Fixture-only test driver can exercise role-denial and lifecycle APIs. This
    # code is not included in either application image/deployment manifest.
    body = await request.json()
    if body["role"] not in ("controller", "gateway", "bootstrap"):
        raise HTTPException(403, "denied")
    return await management(body["method"], body["path"], body.get("body"), body["role"], body.get("headers"))


@app.post("/fixture/agent")
async def endpoint_action(request: Request):
    body = await request.json()
    if body["name"] not in ("a", "b", "replacement", "gateway-agent", "fresh", "sentinel", "legacy", "terminal") or body["path"] not in ("tcp", "listeners", "join", "status", "offline", "online", "serve", "pty-status"):
        raise HTTPException(403, "denied")
    return await agent(body["name"], body["path"], body.get("body"), body.get("method", "POST"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8040, access_log=False)
