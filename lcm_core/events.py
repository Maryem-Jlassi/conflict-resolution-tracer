"""LCM Event system — enables the Inspector to observe LCM internals without controlling them."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict
from uuid import uuid4
from enum import Enum


class EventType(str, Enum):
    MEMORY_INGESTED       = "MEMORY_INGESTED"
    PROVENANCE_VALIDATED  = "PROVENANCE_VALIDATED"
    CONFLICT_DETECTED     = "CONFLICT_DETECTED"
    PSI_COMPUTED          = "PSI_COMPUTED"
    CONFLICT_RESOLVED     = "CONFLICT_RESOLVED"
    CONFLICT_UNRESOLVED   = "CONFLICT_UNRESOLVED"
    TRUST_UPDATED         = "TRUST_UPDATED"
    MEMORY_RETRIEVED      = "MEMORY_RETRIEVED"
    LOOP_DETECTED         = "LOOP_DETECTED"
    WRITE_REJECTED        = "WRITE_REJECTED"
    EVIDENCE_VERIFIED     = "EVIDENCE_VERIFIED"
    VERIFICATION_ERROR    = "VERIFICATION_ERROR"


@dataclass
class LCMEvent:
    """A single observable event emitted by the LCM Core.

    Attributes:
        event_type:  One of the EventType enum values.
        timestamp:   UTC time of the event.
        data:        Arbitrary structured payload specific to each event type.
        event_id:    Unique UUID assigned at creation.
    """

    event_type: EventType
    timestamp: datetime
    data: Dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict (JSON-safe)."""
        return {
            "event_id":   self.event_id,
            "event_type": self.event_type,
            "timestamp":  self.timestamp.isoformat(),
            "data":       self.data,
        }
