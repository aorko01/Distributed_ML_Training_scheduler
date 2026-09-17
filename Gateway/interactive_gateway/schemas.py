from datetime import datetime
import ipaddress
from pydantic import BaseModel, ConfigDict, Field


class Authenticate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: str
    ticket: str = Field(min_length=1, max_length=8192)


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("UTC timestamp required")
    return result.timestamp()


def destination(record):
    if record.get("port") != 9000:
        raise ValueError("unallowlisted destination")
    networks = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
    ips = record["ips"]
    if not isinstance(ips, list) or not ips:
        raise ValueError("missing verified destination")
    for value in ips:
        ip = ipaddress.ip_address(value)
        if str(ip) in {str(ipaddress.ip_address(v)) for v in record.get("gateway_ips", [])} or not any(ip.version == n.version and ip in n for n in networks):
            raise ValueError("invalid tailnet destination")
    return next((ip for ip in ips if ":" not in ip), ips[0]), 9000
