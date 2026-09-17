import httpx
import pytest


@pytest.mark.asyncio
async def test_peer_reverse_port_policy_with_positive_controls(controller, records):
    a = records["a"]["membership"]["ips"][0]
    b = records["b"]["membership"]["ips"][0]
    gateway = (await controller.agent("gateway-agent", "status", method="GET"))["TailscaleIPs"][0]
    assert (await controller.agent("gateway-agent", "tcp", {"ip": b, "port": 9000}))["connected"]
    listener = await controller.agent("b", "listeners", method="GET")
    assert listener["ports"] == listener["tcp_ports"] == [9000, 9001]
    listener = await controller.agent("gateway-agent", "listeners", method="GET")
    assert listener["ports"] == listener["tcp_ports"] == [9000, 9001]
    assert not (await controller.agent("a", "tcp", {"ip": b, "port": 9000}))["connected"]
    assert not (await controller.agent("a", "tcp", {"ip": gateway, "port": 9000}))["connected"]
    assert not (await controller.agent("gateway-agent", "tcp", {"ip": b, "port": 9001}))["connected"]
    assert (await controller.agent("gateway-agent", "tcp", {"ip": b, "port": 9000}))["connected"]


@pytest.mark.asyncio
async def test_driver_cannot_bypass_gateway(controller, records):
    # Driver has only ingress network; no endpoint DNS/bridge/host port path.
    import asyncio
    bridge = (await controller.agent("a", "listeners", method="GET"))["bridge_ip"]
    for target in ("a", "b", bridge, records["a"]["membership"]["ips"][0]):
        with pytest.raises((OSError, TimeoutError)):
            async with asyncio.timeout(1):
                _, writer = await asyncio.open_connection(target, 9000)
                writer.close()
                await writer.wait_closed()
