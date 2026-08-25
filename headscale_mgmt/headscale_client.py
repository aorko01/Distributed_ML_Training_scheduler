import logging
import re
import subprocess
from datetime import datetime, timedelta, timezone

import requests

import config

logger = logging.getLogger("headscale_client")

# Matches keys like "node-1AbCdEfGhIjKlMnOp" returned by headscale.
# Requires a long token so header words like "Key" are never matched.
_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{11,}")


class HeadscaleError(Exception):
    pass


class HeadscaleClient:
    """Create / revoke Headscale pre-auth keys.

    Prefers the Headscale HTTP API when HEADSCALE_API_URL is configured,
    otherwise falls back to the local `headscale` CLI via subprocess.
    """

    def _cli(self, *args: str) -> str:
        cmd = [config.HEADSCALE_CLI_PATH, *args]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
        except FileNotFoundError:
            raise HeadscaleError(f"headscale CLI not found at '{config.HEADSCALE_CLI_PATH}'")
        if result.returncode != 0:
            raise HeadscaleError(
                f"headscale CLI failed ({result.returncode}): {result.stderr.strip()}"
            )
        return result.stdout

    def _api_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if config.HEADSCALE_API_KEY:
            headers["Authorization"] = f"Bearer {config.HEADSCALE_API_KEY}"
        return headers

    def _resolve_user_id(self, user: str) -> str:
        url = f"{config.HEADSCALE_API_URL}/api/v1/user"
        resp = requests.get(url, headers=self._api_headers(), timeout=30)
        resp.raise_for_status()
        for u in resp.json().get("users", []):
            if str(u.get("id")) == user or u.get("name") == user:
                return str(u["id"])
        raise HeadscaleError(f"Headscale user '{user}' not found")

    def create_preauth_key(
        self,
        expiry_seconds: int = None,
        user: str = None,
        reusable: bool = True,
        ephemeral: bool = True,
    ) -> str:
        expiry_seconds = expiry_seconds or config.PREAUTH_KEY_EXPIRY
        user = user or config.HEADSCALE_USER

        if config.HEADSCALE_API_URL:
            return self._create_key_via_api(expiry_seconds, user, reusable, ephemeral)
        return self._create_key_via_cli(expiry_seconds, user, reusable, ephemeral)

    def _extract_key(self, text: str) -> str | None:
        match = _KEY_RE.search(text.replace("\n", " "))
        return match.group(0) if match else None

    def _create_key_via_cli(self, expiry_seconds: int, user: str,
                            reusable: bool, ephemeral: bool) -> str:
        args = [
            "preauthkeys", "create",
            "--user", user,
            "--expiration", f"{expiry_seconds}s",
        ]
        if reusable:
            args.append("--reusable")
        if ephemeral:
            args.append("--ephemeral")

        output = self._cli(*args)
        key = self._extract_key(output)
        if not key:
            raise HeadscaleError(f"Could not parse pre-auth key from CLI output: {output!r}")
        logger.info("Created pre-auth key for user %s", user)
        return key

    def _create_key_via_api(self, expiry_seconds: int, user: str,
                            reusable: bool, ephemeral: bool) -> str:
        url = f"{config.HEADSCALE_API_URL}/api/v1/preauthkey"
        user_id = self._resolve_user_id(user)
        expiration = (
            datetime.now(timezone.utc) + timedelta(seconds=expiry_seconds)
        ).isoformat().replace("+00:00", "Z")
        payload = {
            "user": user_id,
            "reusable": reusable,
            "ephemeral": ephemeral,
            "expiration": expiration,
        }
        try:
            resp = requests.post(url, json=payload, headers=self._api_headers(), timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise HeadscaleError(f"Headscale API create preauthkey failed: {e}")

        key = data.get("preAuthKey", {}).get("key")
        if not key:
            raise HeadscaleError(f"Headscale API response missing key: {data!r}")
        logger.info("Created pre-auth key via API for user %s", user)
        return key

    def _find_key_id(self, key: str) -> str:
        url = f"{config.HEADSCALE_API_URL}/api/v1/preauthkey"
        user_id = self._resolve_user_id(config.HEADSCALE_USER)
        resp = requests.get(
            url, params={"user": user_id}, headers=self._api_headers(), timeout=30
        )
        resp.raise_for_status()
        for k in resp.json().get("preAuthKeys", []):
            listed = k.get("key", "")
            if listed == key or (
                listed.endswith("-***") and key.startswith(listed[: -len("-***")])
            ):
                return str(k["id"])
        raise HeadscaleError("Pre-auth key not found in Headscale")

    def revoke_key(self, key: str) -> None:
        if config.HEADSCALE_API_URL:
            url = f"{config.HEADSCALE_API_URL}/api/v1/preauthkey/expire"
            try:
                key_id = self._find_key_id(key)
                resp = requests.post(
                    url,
                    json={"id": key_id},
                    headers=self._api_headers(),
                    timeout=30,
                )
                resp.raise_for_status()
            except Exception as e:
                raise HeadscaleError(f"Headscale API expire preauthkey failed: {e}")
        else:
            self._cli("preauthkeys", "expire", "--key", key)
        logger.info("Revoked pre-auth key")


headscale_client = HeadscaleClient()
