"""
Corrected Phase A — experiment verifier boundary (HMAC-SHA256).

The experiment verifier provides deterministic oracle labels for controlled
evaluation. It is not a mechanism for open-world truth discovery.

The ``POST /verify`` boundary authenticates an immutable canonical outcome
payload with HMAC-SHA256 keyed by a server/orchestrator-held secret
(``CRT_VERIFIER_SECRET``). The verifier identity is assigned internally by the
server *after* authentication succeeds; it is never read from request JSON.
Source/consumer agents do not hold the secret and therefore cannot verify
themselves or impersonate a verifier, and any modification of the outcome
payload invalidates authentication.

No OAuth, user accounts, JWT, or general identity platform is introduced.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime
from typing import Optional, Union


# The single experimental verifier identity. Assigned by the server only after
# a valid HMAC authenticates; never supplied by a caller.
EXPERIMENT_ORACLE_VERIFIER = "experiment_oracle"

# Verifier identities can never be used as target agent ids (self-verification
# is structurally impossible because agents do not hold the verifier secret).
RESERVED_VERIFIER_IDS = frozenset({EXPERIMENT_ORACLE_VERIFIER})

# Environment variable holding the server/orchestrator-held verifier secret.
VERIFIER_SECRET_ENV = "CRT_VERIFIER_SECRET"


def get_verifier_secret() -> Optional[str]:
    """Return the server-held verifier secret, or ``None`` if unconfigured.

    The secret is environment-supplied only (``CRT_VERIFIER_SECRET``); it never
    lives in source, logs, or test artifacts. Callers must fail closed when it
    is absent.
    """
    secret = os.environ.get(VERIFIER_SECRET_ENV)
    if secret is not None:
        secret = secret.strip() or None
    return secret


def canonical_verification_payload(
    *,
    outcome_id: str,
    target_agent_id: str,
    domain: str,
    correct: bool,
    target_provenance_id: Optional[str],
    observed_at: Optional[Union[str, datetime]],
) -> str:
    """Legacy 6-field deterministic canonical message (back-compat).

    Retained only for pre-existing call sites. New code must use
    :func:`canonical_verification_fingerprint` /
    :func:`canonical_verifier_message`, which additionally bind the internal
    verifier identity into the immutable semantic fingerprint (Section C) so
    that the bound verifier identity cannot be silently swapped without
    invalidating the HMAC.
    """
    obs = observed_at.isoformat() if isinstance(observed_at, datetime) else str(observed_at or "")
    return "|".join(
        [
            str(outcome_id),
            str(target_agent_id),
            str(domain),
            "1" if bool(correct) else "0",
            str(target_provenance_id or ""),
            obs,
        ]
    )


def canonical_verification_fingerprint(
    *,
    outcome_id: str,
    target_agent_id: str,
    domain: str,
    correct: bool,
    verifier_identity: str,
    target_provenance_id: Optional[str],
    observed_at: Optional[Union[str, datetime]],
) -> str:
    """Immutable 7-field semantic fingerprint binding the verifier identity.

    Adds ``verifier_identity`` as a dedicated slot so that swapping which
    verifier produced an outcome changes the fingerprint and therefore
    invalidates any existing HMAC (Section C). The 7 fields are exactly:

        1. outcome_id
        2. target_agent_id
        3. domain
        4. correct flag ("1"/"0")
        5. verifier_identity
        6. target_provenance_id ("")
        7. observed_at (ISO-8601 of the datetime, or "")

    Every field is always present; missing optionals collapse to an empty
    string slot. ``correct`` is canonicalized to a stable "1"/"0" token.
    """
    obs = observed_at.isoformat() if isinstance(observed_at, datetime) else str(observed_at or "")
    return "|".join(
        [
            str(outcome_id),
            str(target_agent_id),
            str(domain),
            "1" if bool(correct) else "0",
            str(verifier_identity or ""),
            str(target_provenance_id or ""),
            obs,
        ]
    )


def canonical_verifier_message(
    *,
    outcome_id: str,
    target_agent_id: str,
    domain: str,
    correct: bool,
    target_provenance_id: Optional[str],
    observed_at: Optional[Union[str, datetime]],
) -> str:
    """Canonical message authenticated by the verifier token.

    This is the request-side canonicalization: it does NOT include the
    verifier identity, because that identity is assigned internally by the
    server *after* authentication succeeds (Section C) — a caller cannot know
    or supply it. Authentication vouches only for the semantic outcome
    payload; the bound internal identity is fixed server-side and is then
    embedded into the durable fingerprint by
    :func:`canonical_verification_fingerprint`.
    """
    return canonical_verification_payload(
        outcome_id=outcome_id,
        target_agent_id=target_agent_id,
        domain=domain,
        correct=correct,
        target_provenance_id=target_provenance_id,
        observed_at=observed_at,
    )


def compute_verifier_token(secret: str, payload: str) -> str:
    """Compute the HMAC-SHA256 token (hex) over ``payload`` keyed by ``secret``."""
    return hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def expect_verifier_token(secret: Optional[str], payload: str) -> str:
    """Compute the expected verifier token for a given secret + payload.

    Convenience alias over :func:`compute_verifier_token`; mirrors the shape of
    the authentication call so a test/operator can produce the expected token
    with the same arguments an authenticated caller would send.
    """
    return compute_verifier_token(secret, payload)


def authenticate_verifier(payload: str, token: Optional[str], secret: Optional[str]) -> bool:
    """Constant-time authentication of a caller-supplied verifier token.

    Fails closed (returns ``False``) when the secret or token is missing/empty,
    never raising on authentication state. Delegates to
    :func:`verify_verifier_token`.
    """
    return verify_verifier_token(secret, payload, token)


def verify_verifier_token(secret: Optional[str], payload: str, token: Optional[str]) -> bool:
    """Constant-time comparison of a caller-supplied token against the expected.

    Fails closed when the secret or token is missing/empty.
    """
    if not secret or not token:
        return False
    expected = compute_verifier_token(secret, payload)
    return hmac.compare_digest(expected, token.strip())
