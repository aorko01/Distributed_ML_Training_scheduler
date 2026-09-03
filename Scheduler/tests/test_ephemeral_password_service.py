"""Unit tests for Scheduler/app/services/ephemeral_password_service.py."""

import threading
import time

import pytest

# Adjust sys.path so the ``app`` package is importable when running from
# ``Scheduler/`` directory: ``python -m pytest tests/ -v``.
import sys  # noqa: E401
import os  # noqa: E401
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.services.ephemeral_password_service as eps  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_store():
    """Clear the in-memory store before and after every test."""
    with eps._lock:
        eps._store.clear()
    yield
    with eps._lock:
        eps._store.clear()


# ---------------------------------------------------------------------------
# Basic issue / verify
# ---------------------------------------------------------------------------

class TestIssueAndVerify:
    def test_issue_returns_plaintext(self):
        pw = eps.issue_ephemeral_password("alice", "sess1", "10.0.0.1")
        assert isinstance(pw, str)
        assert len(pw) > 0

    def test_verify_success(self):
        pw = eps.issue_ephemeral_password("alice", "sess1", "10.0.0.1")
        result = eps.verify_ephemeral_password("alice", pw)
        assert result is not None
        assert result["session_id"] == "sess1"
        assert result["headscale_ip"] == "10.0.0.1"

    def test_wrong_password_returns_none(self):
        eps.issue_ephemeral_password("alice", "sess1", "10.0.0.1")
        result = eps.verify_ephemeral_password("alice", "wrongpassword")
        assert result is None

    def test_wrong_username_rejected_and_deleted(self):
        """A wrong username returns None and the entry is deleted."""
        pw = eps.issue_ephemeral_password("alice", "sess1", "10.0.0.1")
        # Verify with wrong username → entry deleted.
        assert eps.verify_ephemeral_password("bob", pw) is None
        # Now even the correct user can't verify (entry gone).
        assert eps.verify_ephemeral_password("alice", pw) is None


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------

class TestExpiry:
    def test_expired_password_rejected(self):
        pw = eps.issue_ephemeral_password("alice", "sess1", "10.0.0.1", ttl_seconds=1)
        time.sleep(1.1)
        assert eps.verify_ephemeral_password("alice", pw) is None


# ---------------------------------------------------------------------------
# Multi-use (the core fix)
# ---------------------------------------------------------------------------

class TestMultiUse:
    def test_multi_use_within_window(self):
        """The same password can be verified multiple times — the key
        regression test for VS Code Remote-SSH support."""
        pw = eps.issue_ephemeral_password("alice", "sess1", "10.0.0.1", ttl_seconds=300)
        for _ in range(5):
            result = eps.verify_ephemeral_password("alice", pw)
            assert result is not None
            assert result["session_id"] == "sess1"

    def test_grace_extension_after_first_use(self):
        """After the base TTL expires, a previously-used password still
        succeeds because the grace window was added on first use."""
        pw = eps.issue_ephemeral_password("alice", "sess1", "10.0.0.1", ttl_seconds=1)
        # First use — within base window.
        result = eps.verify_ephemeral_password("alice", pw)
        assert result is not None
        # Wait past the base TTL.
        time.sleep(1.1)
        # Second use — base TTL expired but grace window still active.
        result = eps.verify_ephemeral_password("alice", pw)
        assert result is not None

    def test_hard_cap_from_issuance(self):
        """Even with grace extension, the hard cap from issuance is
        enforced."""
        original_grace = eps.GRACE_SECONDS
        try:
            eps.GRACE_SECONDS = 1  # tiny grace
            pw = eps.issue_ephemeral_password("alice", "sess1", "10.0.0.1", ttl_seconds=1)
            # First use — extends expiry by 1 s (GRACE_SECONDS).
            result = eps.verify_ephemeral_password("alice", pw)
            assert result is not None
            # Wait past base TTL + grace + hard cap.
            time.sleep(2.5)
            assert eps.verify_ephemeral_password("alice", pw) is None
        finally:
            eps.GRACE_SECONDS = original_grace


# ---------------------------------------------------------------------------
# Session revocation
# ---------------------------------------------------------------------------

class TestRevocation:
    def test_reissue_revokes_previous_password(self):
        """Issuing a second password for the same session invalidates
        the first."""
        pw1 = eps.issue_ephemeral_password("alice", "sess1", "10.0.0.1")
        pw2 = eps.issue_ephemeral_password("alice", "sess1", "10.0.0.2")
        # Old password is gone.
        assert eps.verify_ephemeral_password("alice", pw1) is None
        # New password works.
        result = eps.verify_ephemeral_password("alice", pw2)
        assert result is not None
        assert result["headscale_ip"] == "10.0.0.2"


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_concurrent_verifications_all_succeed(self):
        """Multiple threads verifying the same password all succeed."""
        pw = eps.issue_ephemeral_password("alice", "sess1", "10.0.0.1")
        results = []
        barrier = threading.Barrier(10)

        def _verify():
            barrier.wait()  # all threads start together
            r = eps.verify_ephemeral_password("alice", pw)
            results.append(r)

        threads = [threading.Thread(target=_verify) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 10 verifications should have succeeded.
        assert all(r is not None for r in results)
        assert len(results) == 10
