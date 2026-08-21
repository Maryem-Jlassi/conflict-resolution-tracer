"""
Unit tests — Evidence-provider key lifecycle (Phase 6).

Covers provider registration, key rotation, revocation, and persistence of the
provider registry (SQLite-backed so trusted keys survive restarts). A revoked
key must immediately stop accepting evidence signed with it.

Tests use a throwaway Ed25519 keypair (NOT the dev key) so revocation cannot be
silently masked by the dev-key fallback.
"""

from datetime import datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from crt_core.confidence_engine import EvidenceRecord, EvidenceType
from crt_core.crypto import (
    canonical_assertion_hash,
    EvidenceProviderRegistry,
    get_provider_registry,
    set_provider_registry,
    sign_evidence_message_with_key,
    verify_evidence_signature_crypto,
)
from crt_core.provenance import validate_and_stamp
from crt_service.storage import SQLiteProviderRegistry, SQLiteStorage

REF = datetime(2026, 7, 14, 10, 0, 0)


@pytest.fixture(autouse=True)
def _dev_key_and_fresh_registry(monkeypatch):
    """Opt into the dev key AND isolate the registry per test."""
    monkeypatch.setenv("CRT_ALLOW_DEV_EVIDENCE_KEY", "1")
    set_provider_registry(EvidenceProviderRegistry())
    yield
    set_provider_registry(EvidenceProviderRegistry())


def _make_keypair():
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key().public_bytes_raw()


class TestProviderRegistry:
    def test_register_and_verify_with_provider_key(self):
        priv, pub_bytes = _make_keypair()
        reg = get_provider_registry()
        reg.register_provider("labs", "key-1", pub_bytes)

        sig = sign_evidence_message_with_key(
            priv, EvidenceType.DATABASE, "db://x",
            provider_id="labs", key_id="key-1",
        )
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db://x", sig,
            provider_id="labs", key_id="key-1",
        ) is True

    def test_unknown_provider_key_fails_closed(self):
        priv, pub_bytes = _make_keypair()
        reg = get_provider_registry()
        reg.register_provider("labs", "key-1", pub_bytes)

        sig = sign_evidence_message_with_key(
            priv, EvidenceType.DATABASE, "db://x",
            provider_id="labs", key_id="key-1",
        )
        # Different key_id than the one registered → must not verify.
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db://x", sig,
            provider_id="labs", key_id="key-999",
        ) is False

    def test_revoked_key_stops_verifying(self):
        priv, pub_bytes = _make_keypair()
        reg = get_provider_registry()
        reg.register_provider("labs", "key-1", pub_bytes)
        sig = sign_evidence_message_with_key(
            priv, EvidenceType.DATABASE, "db://x",
            provider_id="labs", key_id="key-1",
        )
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db://x", sig,
            provider_id="labs", key_id="key-1",
        ) is True

        reg.revoke_key("labs", "key-1")
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db://x", sig,
            provider_id="labs", key_id="key-1",
        ) is False

    def test_key_rotation_new_key_valid_old_revoked(self):
        priv_old, pub_old = _make_keypair()
        priv_new, pub_new = _make_keypair()
        reg = get_provider_registry()
        reg.register_provider("labs", "key-1", pub_old)
        reg.register_provider("labs", "key-2", pub_new)
        reg.revoke_key("labs", "key-1")

        sig_old = sign_evidence_message_with_key(
            priv_old, EvidenceType.DATABASE, "db://x",
            provider_id="labs", key_id="key-1",
        )
        sig_new = sign_evidence_message_with_key(
            priv_new, EvidenceType.DATABASE, "db://x",
            provider_id="labs", key_id="key-2",
        )

        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db://x", sig_old,
            provider_id="labs", key_id="key-1",
        ) is False  # rotated out
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db://x", sig_new,
            provider_id="labs", key_id="key-2",
        ) is True  # current key

    def test_list_providers(self):
        reg = get_provider_registry()
        for provider in ("p1", "p2"):
            _, pub = _make_keypair()
            reg.register_provider(provider, "k", pub)
        assert set(reg.list_providers()) == {"p1", "p2"}


class TestSQLiteProviderRegistry:
    def test_persists_across_instances(self, tmp_path):
        db = tmp_path / "providers.db"
        priv, pub_bytes = _make_keypair()

        # Process/instance #1: register a provider key.
        r1 = SQLiteProviderRegistry(SQLiteStorage(str(db)))
        r1.register_provider("labs", "key-1", pub_bytes)
        set_provider_registry(r1)
        sig = sign_evidence_message_with_key(
            priv, EvidenceType.DATABASE, "db://x",
            provider_id="labs", key_id="key-1",
        )
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db://x", sig,
            provider_id="labs", key_id="key-1",
        ) is True

        # "Restart": a fresh storage instance sees the same persisted key and
        # the previously-signed evidence still verifies.
        r2 = SQLiteProviderRegistry(SQLiteStorage(str(db)))
        assert r2.get_public_key("labs", "key-1") is not None
        set_provider_registry(r2)
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db://x", sig,
            provider_id="labs", key_id="key-1",
        ) is True

    def test_revocation_persists(self, tmp_path):
        db = tmp_path / "providers2.db"
        priv, pub_bytes = _make_keypair()

        r1 = SQLiteProviderRegistry(SQLiteStorage(str(db)))
        r1.register_provider("labs", "key-1", pub_bytes)
        r1.revoke_key("labs", "key-1")

        # A fresh instance sees the revoked status persisted.
        r2 = SQLiteProviderRegistry(SQLiteStorage(str(db)))
        assert r2.get_public_key("labs", "key-1") is None
        assert r2.provider_status("labs", "key-1") == "revoked"

        set_provider_registry(r2)
        sig = sign_evidence_message_with_key(
            priv, EvidenceType.DATABASE, "db://x",
            provider_id="labs", key_id="key-1",
        )
        assert verify_evidence_signature_crypto(
            EvidenceType.DATABASE, "db://x", sig,
            provider_id="labs", key_id="key-1",
        ) is False

    def test_list_providers_after_persist(self, tmp_path):
        db = tmp_path / "providers3.db"
        _, pub = _make_keypair()
        s = SQLiteStorage(str(db))
        r = SQLiteProviderRegistry(s)
        r.register_provider("labs", "key-1", pub)
        assert r.list_providers() == ["labs"]


class TestProviderRegistryEndToEnd:
    def test_revoked_provider_evidence_degrades_in_pipeline(self):
        """A revoked provider's evidence must fail closed in validate_and_stamp."""
        priv, pub_bytes = _make_keypair()
        reg = get_provider_registry()
        reg.register_provider("labs", "key-1", pub_bytes)
        reg.revoke_key("labs", "key-1")

        ev = EvidenceRecord(
            evidence_type=EvidenceType.DATABASE,
            relevance_score=1.0,
            provider_id="labs",
            key_id="key-1",
            issued_at=(REF - timedelta(hours=1)).isoformat(),
            expires_at=(REF + timedelta(hours=1)).isoformat(),
        )
        sig = sign_evidence_message_with_key(
            priv, EvidenceType.DATABASE, None,
            provider_id="labs", key_id="key-1",
            issued_at=ev.issued_at, expires_at=ev.expires_at,
            assertion_hash=canonical_assertion_hash(
                agent_id="a", timestamp=REF,
                assertion_payload={"k": "v"},
            ),
        )
        from crt_core.provenance import RejectionError
        with pytest.raises(RejectionError):
            validate_and_stamp(
                {
                    "agent_id": "a",
                    "session_id": "s",
                    "timestamp": REF,
                    "confidence_score": 0.9,
                    "assertion_payload": {"k": "v"},
                },
                evidence_records=[ev],
                evidence_signature=sig,
                reference_time=REF,
            )
