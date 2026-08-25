import logging
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import config

logger = logging.getLogger("ssh_key_manager")


class SSHKeyManager:
    """Generate and manage per-session ED25519 SSH keypairs.

    Private keys are persisted at <SSH_KEY_DIR>/<session_id> with mode 0o600;
    the gateway keeps private keys and only ever hands out public keys.
    """

    def __init__(self, key_dir: str = None):
        self.key_dir = key_dir or config.SSH_KEY_DIR

    def _key_path(self, session_id: str) -> str:
        return os.path.join(self.key_dir, session_id)

    def generate_keypair(self, session_id: str) -> tuple[str, str]:
        os.makedirs(self.key_dir, exist_ok=True)

        private_key = Ed25519PrivateKey.generate()
        priv_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        pub_openssh = (
            private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.OpenSSH,
                format=serialization.PublicFormat.OpenSSH,
            )
            .decode("utf-8")
        )

        path = self._key_path(session_id)
        with open(path, "w") as f:
            f.write(priv_pem)
        os.chmod(path, 0o600)
        logger.info("Generated SSH keypair for session %s", session_id)

        return priv_pem, pub_openssh

    def get_private_key_path(self, session_id: str) -> str | None:
        path = self._key_path(session_id)
        return path if os.path.isfile(path) else None

    def get_public_key(self, session_id: str) -> str | None:
        path = self.get_private_key_path(session_id)
        if not path:
            return None
        with open(path) as f:
            priv_pem = f.read()
        try:
            loaded = serialization.load_ssh_private_key(priv_pem.encode(), password=None)
        except ValueError as e:
            logger.error("Failed to load private key for session %s: %s", session_id, e)
            return None
        return (
            loaded.public_key()
            .public_bytes(
                encoding=serialization.Encoding.OpenSSH,
                format=serialization.PublicFormat.OpenSSH,
            )
            .decode("utf-8")
        )

    def delete_keypair(self, session_id: str) -> bool:
        path = self._key_path(session_id)
        if os.path.isfile(path):
            os.remove(path)
            logger.info("Deleted SSH keypair for session %s", session_id)
            return True
        return False


ssh_key_manager = SSHKeyManager()
