import httpx
import pytest
from headscale_management.headscale_client import HeadscaleClient, UnknownResult, ControlUnavailable, parse_node


def test_pinned_node_schema():
    node = parse_node({"id": "18446744073709551615", "preAuthKey": {"id": "5"}, "tags": ["tag:interactive-endpoint"],
        "ipAddresses": ["100.64.0.2"], "online": True, "expiry": "2027-01-01T00:00:00Z"})
    assert node["key_id"] == "5" and node["expiry"] > 0
    with pytest.raises(ValueError):
        parse_node({"id": "1", "tags": []})


@pytest.mark.asyncio
async def test_creation_timeout_once_safe_reads_retry_cleanup_404(system):
    calls = []
    def fail(request):
        calls.append(request.method)
        raise httpx.ReadTimeout("secret upstream exception")
    client = HeadscaleClient(system.settings, httpx.MockTransport(fail))
    with pytest.raises(UnknownResult):
        await client.create_key(["tag:interactive-endpoint"], True, "2027-01-01T00:00:00Z")
    assert calls == ["POST"]
    with pytest.raises(ControlUnavailable):
        await client.nodes()
    assert calls == ["POST", "GET", "GET", "GET"]
    await client.close()
    client = HeadscaleClient(system.settings, httpx.MockTransport(lambda r: httpx.Response(404)))
    await client.cleanup("3", ["4"])
    await client.close()
