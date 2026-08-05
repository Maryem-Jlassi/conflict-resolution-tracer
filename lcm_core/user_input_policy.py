"""User-input policy — gated, delegable authority for ``user_input`` evidence.

Phase 9: ``user_input`` is the highest-authority evidence tier (1.0), so it must
be treated as *delegated* authority rather than a self-serve label. The default
policy requires gateway attestation (a valid Ed25519 evidence signature) exactly
like database/document/tool_output, and can restrict *which agents* the user has
delegated relay rights to. Unsigned or non-delegated ``user_input`` degrades to
``agent_claim_default`` (authority 0.1) instead of silently claiming 1.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .confidence_engine import EvidenceType, EVIDENCE_AUTHORITY
from .config import UNVERIFIED_AUTHORITY_FALLBACK, UNVERIFIED_CONFIDENCE_FALLBACK


@dataclass
class UserInputDecision:
    """Outcome of applying a :class:`UserInputPolicy` to one evidence record."""

    accepted: bool
    source_type: Optional[str]
    authority_score: Optional[float]
    verified_confidence_cap: Optional[float]
    reason: str
    policy: str


@dataclass
class UserInputPolicy:
    """
    Policy governing how ``user_input`` evidence is admitted.

    Semantics
    ---------
    * ``require_attestation`` — ``user_input`` claims must carry a valid gateway
      evidence signature (Ed25519 over the canonical V2 message). Without one the
      claim degrades to ``fallback_evidence_type`` (``agent_claim_default``) at
      ``fallback_authority``, matching the documented "requires gateway
      attestation" contract.
    * ``allowed_relayers`` — delegated-authority allowlist. When set, only these
      ``agent_id`` values may relay ``user_input``. ``None`` (default) = open to
      any agent, but attestation is still required.
    * ``unauthorized_relay_action`` — ``"reject"`` (default) hard-rejects a
      non-delegated relay (fail-closed); ``"degrade"`` instead falls back to
      ``fallback_evidence_type`` like the missing-attestation path.
    """

    name: str = "default"
    require_attestation: bool = True
    fallback_evidence_type: EvidenceType = EvidenceType.AGENT_CLAIM_DEFAULT
    fallback_authority: float = UNVERIFIED_AUTHORITY_FALLBACK
    allowed_relayers: Optional[Tuple[str, ...]] = None
    unauthorized_relay_action: str = "reject"

    def evaluate(self, agent_id: str, signature_valid: bool) -> UserInputDecision:
        """Apply this policy to a single ``user_input`` evidence record."""
        if (
            self.allowed_relayers is not None
            and agent_id not in self.allowed_relayers
        ):
            if self.unauthorized_relay_action == "degrade":
                return UserInputDecision(
                    accepted=True,
                    source_type=self.fallback_evidence_type.value,
                    authority_score=self.fallback_authority,
                    verified_confidence_cap=self.fallback_authority,
                    reason=(
                        f"agent '{agent_id}' is not a delegated user-input relay; "
                        f"degraded to {self.fallback_evidence_type.value}"
                    ),
                    policy=self.name,
                )
            return UserInputDecision(
                accepted=False,
                source_type=None,
                authority_score=None,
                verified_confidence_cap=None,
                reason=(
                    f"agent '{agent_id}' is not authorized to relay user input "
                    "(delegation allowlist)"
                ),
                policy=self.name,
            )

        if self.require_attestation and not signature_valid:
            return UserInputDecision(
                accepted=True,
                source_type=self.fallback_evidence_type.value,
                authority_score=self.fallback_authority,
                verified_confidence_cap=UNVERIFIED_CONFIDENCE_FALLBACK,
                reason=(
                    "user_input requires gateway attestation (a valid evidence "
                    f"signature); degraded to {self.fallback_evidence_type.value}"
                ),
                policy=self.name,
            )

        return UserInputDecision(
            accepted=True,
            source_type=EvidenceType.USER_INPUT.value,
            authority_score=EVIDENCE_AUTHORITY[EvidenceType.USER_INPUT],
            verified_confidence_cap=None,
            reason="user_input attested by a valid gateway signature",
            policy=self.name,
        )


DEFAULT_USER_INPUT_POLICY = UserInputPolicy()

_active_policy: UserInputPolicy = DEFAULT_USER_INPUT_POLICY


def get_user_input_policy() -> UserInputPolicy:
    """Return the active user-input policy (module-global, like replay guard)."""
    return _active_policy


def set_user_input_policy(policy: UserInputPolicy) -> None:
    """Install a different user-input policy (deployment / test override)."""
    global _active_policy
    _active_policy = policy


def reset_user_input_policy() -> None:
    """Restore the default policy (tests)."""
    global _active_policy
    _active_policy = DEFAULT_USER_INPUT_POLICY
