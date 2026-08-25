import logging

import paramiko

import config
from ssh_key_manager import ssh_key_manager

logger = logging.getLogger("session_client")


class SessionClient:
    """Open SSH sessions to interactive containers over the tailnet."""

    def connect(self, session_id: str, target_ip: str) -> paramiko.SSHClient:
        key_path = ssh_key_manager.get_private_key_path(session_id)
        if not key_path:
            raise FileNotFoundError(f"No private key stored for session {session_id}")

        client = paramiko.SSHClient()
        # Container host keys are ephemeral; trusting them blindly is fine for MVP.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=target_ip,
            port=22,
            username=config.SSH_USER,
            key_filename=key_path,
            timeout=config.SSH_CONNECT_TIMEOUT,
            allow_agent=False,
            look_for_keys=False,
        )
        logger.info("SSH connected to %s for session %s", target_ip, session_id)
        return client

    def execute_command(self, client: paramiko.SSHClient, command: str) -> str:
        _, stdout, stderr = client.exec_command(command, timeout=config.SSH_CONNECT_TIMEOUT)
        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if exit_code != 0:
            raise RuntimeError(
                f"Command failed with exit code {exit_code}: {err.strip() or output.strip()}"
            )
        return output

    def close(self, client: paramiko.SSHClient):
        try:
            client.close()
        except Exception as e:
            logger.debug("Error closing SSH client: %s", e)


session_client = SessionClient()
