"""OCI Network Security Group (NSG) IP-whitelisting service.

Wraps the Oracle Cloud Infrastructure (OCI) Python SDK to dynamically add
and remove ingress rules on a dedicated NSG attached to the SSH gateway
VM/VNIC.  Only TCP port 2222 (the gateway SSH port) is ever touched.

Authentication uses **Instance Principal** (no API keys / config file mounted
into the container).  All operations are best-effort: errors are logged and
never propagated to the caller.
"""

import logging
import os

logger = logging.getLogger("oci_whitelist_service")

# ---------------------------------------------------------------------------
# Lazy/guarded import of the OCI SDK.
#
# The OCI SDK is an optional dependency.  In local/dev environments it may not
# be pip-installed, and importing this module must never crash the app.  We
# therefore guard the import and set a module-level flag so that add_ip /
# remove_ip can no-op gracefully (logged) when the SDK is unavailable.
# ---------------------------------------------------------------------------
try:
    import oci
    from oci.auth.signers import InstancePrincipalsSecurityTokenSigner
    OCI_AVAILABLE = True
except ImportError:  # pragma: no cover - environment dependent
    oci = None
    InstancePrincipalsSecurityTokenSigner = None
    OCI_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuration (all from environment — never hardcoded OCIDs).
# ---------------------------------------------------------------------------
OCI_REGION = os.getenv("OCI_REGION", "")
OCI_COMPARTMENT_OCID = os.getenv("OCI_COMPARTMENT_OCID", "")
OCI_GATEWAY_NSG_OCID = os.getenv("OCI_GATEWAY_NSG_OCID", "")
OCI_USE_INSTANCE_PRINCIPAL = (
    os.getenv("OCI_USE_INSTANCE_PRINCIPAL", "true").lower() == "true"
)
GATEWAY_SSH_PORT = int(os.getenv("GATEWAY_SSH_PORT", "2222"))


def _build_client():
    """Build an OCI VirtualNetworkClient using Instance Principal auth."""
    if OCI_USE_INSTANCE_PRINCIPAL:
        signer = InstancePrincipalsSecurityTokenSigner()
        config = {
            "region": OCI_REGION,
            "tenancy": signer.tenancy_id,
        }
        return oci.core.VirtualNetworkClient(config, signer=signer)

    # Fallback (not recommended for production — no config file is mounted).
    logger.warning(
        "OCI_USE_INSTANCE_PRINCIPAL is false; falling back to config file. "
        "This is not recommended for production."
    )
    config = oci.config.from_file()
    if OCI_REGION:
        config["region"] = OCI_REGION
    return oci.core.VirtualNetworkClient(config)


def _client():
    """Return a configured VirtualNetworkClient (cached per-call for safety)."""
    return _build_client()


def _rule_matches(rule, ip: str, port: int) -> bool:
    """Return True if *rule* is an INGRESS TCP rule for *ip*/32 on *port*."""
    if rule.direction != "INGRESS":
        return False
    if rule.protocol != "6":  # 6 == TCP
        return False
    if rule.source != f"{ip}/32":
        return False
    tcp = rule.tcp_options
    if tcp is None or tcp.destination_port_range is None:
        return False
    pr = tcp.destination_port_range
    return pr.min == port and pr.max == port


def add_ip(ip: str) -> None:
    """Add an ingress rule for *ip* on the SSH port.

    Idempotent: if a matching rule already exists this is a no-op.
    Best-effort: all errors are logged and never raised.
    """
    if not OCI_AVAILABLE:
        logger.warning(
            "OCI SDK not installed; skipping whitelist add for %s", ip
        )
        return
    if not OCI_GATEWAY_NSG_OCID:
        logger.warning(
            "OCI_GATEWAY_NSG_OCID not set; skipping whitelist add for %s", ip
        )
        return
    try:
        client = _client()
        rules = client.list_network_security_group_security_rules(
            network_security_group_id=OCI_GATEWAY_NSG_OCID
        ).data

        for rule in rules:
            if _rule_matches(rule, ip, GATEWAY_SSH_PORT):
                logger.info("Whitelist rule already exists for %s; no-op", ip)
                return

        add_details = oci.core.models.AddNetworkSecurityGroupSecurityRulesDetails(
            security_rules=[
                oci.core.models.SecurityRuleDetails(
                    direction="INGRESS",
                    protocol="6",
                    source=f"{ip}/32",
                    tcp_options=oci.core.models.TcpOptions(
                        destination_port_range=oci.core.models.PortRange(
                            min=GATEWAY_SSH_PORT, max=GATEWAY_SSH_PORT
                        )
                    ),
                    description=f"temp ssh whitelist {ip}",
                )
            ]
        )
        client.add_network_security_group_security_rules(
            network_security_group_id=OCI_GATEWAY_NSG_OCID,
            add_network_security_group_security_rules_details=add_details,
        )
        logger.info("Added whitelist rule for %s on port %d", ip, GATEWAY_SSH_PORT)
    except Exception as e:
        logger.error("Failed to add whitelist rule for %s: %s", ip, e)


def remove_ip(ip: str) -> None:
    """Remove the ingress rule for *ip* on the SSH port.

    Idempotent: if no matching rule exists this is a no-op.
    Best-effort: all errors are logged and never raised.
    """
    if not OCI_AVAILABLE:
        logger.warning(
            "OCI SDK not installed; skipping whitelist remove for %s", ip
        )
        return
    if not OCI_GATEWAY_NSG_OCID:
        logger.warning(
            "OCI_GATEWAY_NSG_OCID not set; skipping whitelist remove for %s", ip
        )
        return
    try:
        client = _client()
        rules = client.list_network_security_group_security_rules(
            network_security_group_id=OCI_GATEWAY_NSG_OCID
        ).data

        rule_ids = [
            rule.id for rule in rules if _rule_matches(rule, ip, GATEWAY_SSH_PORT)
        ]

        if not rule_ids:
            logger.info("No whitelist rule found for %s; no-op", ip)
            return

        remove_details = (
            oci.core.models.RemoveNetworkSecurityGroupSecurityRulesDetails(
                security_rule_ids=rule_ids
            )
        )
        client.remove_network_security_group_security_rules(
            network_security_group_id=OCI_GATEWAY_NSG_OCID,
            remove_network_security_group_security_rules_details=remove_details,
        )
        logger.info("Removed whitelist rule(s) for %s", ip)
    except Exception as e:
        logger.error("Failed to remove whitelist rule for %s: %s", ip, e)
