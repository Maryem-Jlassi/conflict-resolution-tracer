"""
Shared write/conflict status constants.

Use these everywhere instead of inline string literals so experiments,
benchmarks, the HTTP API, and the pipeline all describe outcomes with the
same vocabulary.
"""

# Direct commit outcomes
STATUS_COMMITTED = "committed"
STATUS_REJECTED = "rejected"
STATUS_REJECTED_UNTRUSTED = "rejected_untrusted"
STATUS_REJECTED_SUSPICIOUS = "rejected_suspicious"
STATUS_REJECTED_NO_EVIDENCE = "rejected_no_evidence"
STATUS_EVIDENCE_REJECTED = "evidence_rejected"

# Conflict outcomes
STATUS_CONFLICT_RESOLVED = "conflict_resolved"
STATUS_RESOLVED = "resolved"
STATUS_UNRESOLVED = "unresolved"
STATUS_LOOP_FROZEN = "loop_frozen"

# Locking / infrastructure outcomes
STATUS_LOCK_FAILED = "lock_failed"

# Terminal sets used by experiments/logging
COMMIT_STATUSES = {STATUS_COMMITTED, STATUS_CONFLICT_RESOLVED}
CONFLICT_STATUSES = {STATUS_CONFLICT_RESOLVED, STATUS_UNRESOLVED}
REJECTION_STATUSES = {
    STATUS_REJECTED,
    STATUS_REJECTED_UNTRUSTED,
    STATUS_REJECTED_SUSPICIOUS,
    STATUS_REJECTED_NO_EVIDENCE,
    STATUS_EVIDENCE_REJECTED,
}

# Every status the pipeline may emit, for validation tests
ALL_PIPELINE_STATUSES = frozenset({
    STATUS_COMMITTED,
    STATUS_REJECTED,
    STATUS_REJECTED_UNTRUSTED,
    STATUS_REJECTED_SUSPICIOUS,
    STATUS_REJECTED_NO_EVIDENCE,
    STATUS_EVIDENCE_REJECTED,
    STATUS_CONFLICT_RESOLVED,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    STATUS_LOOP_FROZEN,
    STATUS_LOCK_FAILED,
})
