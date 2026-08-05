"""Provenance lineage — first-class derivation chain with integrity walking.

Phase 8: every accepted memory is recorded as a :class:`LineageNode` whose
``content_hash`` deterministically binds agent_id + timestamp + payload + its
``parent_memory_ids``.  The graph of these nodes is a hash chain:  an
:func:`walk_chain` re-derives each node's content hash from the fields actually
stored in the lineage table, so tampering with any node's content — or with the
parent references a child's hash commits to — is detected on the walk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Protocol

from .provenance import _compute_content_hash


# ---------------------------------------------------------------------------
# Node model
# ---------------------------------------------------------------------------

@dataclass
class LineageNode:
    """A single memory in the provenance graph.

    ``payload`` retains the exact (agent_id, timestamp, assertion_payload)
    triple the content hash was computed over, so an integrity walk can
    re-derive the hash without trusting the recorded ``content_hash`` column.
    """

    provenance_id: str
    content_hash: str
    agent_id: str
    timestamp: str
    parent_memory_ids: List[str] = field(default_factory=list)
    path: Optional[str] = None
    payload: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "provenance_id": self.provenance_id,
            "content_hash": self.content_hash,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp,
            "parent_memory_ids": list(self.parent_memory_ids),
            "path": self.path,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LineageNode":
        return cls(
            provenance_id=data["provenance_id"],
            content_hash=data["content_hash"],
            agent_id=data["agent_id"],
            timestamp=data["timestamp"],
            parent_memory_ids=list(data.get("parent_memory_ids") or []),
            path=data.get("path"),
            payload=data.get("payload"),
        )


def node_from_stamped(
    stamped,
    path: Optional[str] = None,
    extra_parents: Optional[List[str]] = None,
) -> LineageNode:
    """Build a :class:`LineageNode` from a stamped memory.

    ``extra_parents`` lets a caller bind additional derivation parents (e.g. a
    conflict winner derived from both the incumbent and the challenger) into
    the node's content hash *without* mutating the committed memory.
    """
    parents = list(stamped.provenance_info.parent_memory_ids or [])
    if extra_parents:
        for pid in extra_parents:
            if pid != stamped.provenance_id and pid not in parents:
                parents.append(pid)
    parents.sort()

    content_hash = _compute_content_hash(
        agent_id=stamped.agent_id,
        timestamp=stamped.timestamp,
        assertion_payload=stamped.assertion_payload,
        parent_hashes=parents or None,
    )
    payload = {
        "agent_id": stamped.agent_id,
        "timestamp": stamped.timestamp.isoformat(),
        "assertion_payload": stamped.assertion_payload,
    }
    return LineageNode(
        provenance_id=stamped.provenance_id,
        content_hash=content_hash,
        agent_id=stamped.agent_id,
        timestamp=stamped.timestamp.isoformat(),
        parent_memory_ids=parents,
        path=path,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Storage protocol
# ---------------------------------------------------------------------------

class LineageStore(Protocol):
    def add_node(self, node: LineageNode) -> None: ...
    def get_node(self, provenance_id: str) -> Optional[LineageNode]: ...
    def all_nodes(self) -> List[LineageNode]: ...
    def node_count(self) -> int: ...


class InMemoryLineageStore:
    """Trivial in-memory store — useful for tests and pure analysis."""

    def __init__(self) -> None:
        self._nodes: Dict[str, LineageNode] = {}

    def add_node(self, node: LineageNode) -> None:
        self._nodes[node.provenance_id] = node

    def get_node(self, provenance_id: str) -> Optional[LineageNode]:
        return self._nodes.get(provenance_id)

    def all_nodes(self) -> List[LineageNode]:
        return list(self._nodes.values())

    def node_count(self) -> int:
        return len(self._nodes)


# ---------------------------------------------------------------------------
# Integrity walk
# ---------------------------------------------------------------------------

@dataclass
class ChainIssue:
    """A single integrity problem discovered on a chain walk."""

    kind: str
    provenance_id: str
    detail: str

    def __repr__(self) -> str:
        return f"ChainIssue({self.kind!r}, {self.provenance_id!r}, {self.detail!r})"


@dataclass
class ChainWalkResult:
    """Outcome of :func:`walk_chain`."""

    root_id: str
    node_count: int
    edge_count: int
    issues: List[ChainIssue]
    ok: bool
    visited: List[str]


def _recompute_hash(node: LineageNode) -> Optional[str]:
    """Re-derive a node's content hash from the fields stored in lineage."""
    payload = node.payload or {}
    try:
        ts = datetime.fromisoformat(payload.get("timestamp", node.timestamp))
    except (ValueError, TypeError):
        return None
    return _compute_content_hash(
        agent_id=payload.get("agent_id", node.agent_id),
        timestamp=ts,
        assertion_payload=payload.get("assertion_payload", {}),
        parent_hashes=node.parent_memory_ids or None,
    )


def walk_chain(
    store: LineageStore,
    root_id: str,
    recompute: Optional[Callable[[LineageNode], Optional[str]]] = None,
) -> ChainWalkResult:
    """Walk the provenance chain from ``root_id`` verifying structure + hashes.

    Issues reported (``kind``):

    - ``missing_node``        — a referenced provenance_id is not in the store
      (broken/ dangling parent link).
    - ``missing_content_hash``— node records no content hash.
    - ``tampered_content``    — re-derived content hash != recorded hash
      (content or parent-reference tampering).
    - ``cycle``               — the chain revisits a node already being walked.
    """
    recompute_fn = recompute or _recompute_hash
    issues: List[ChainIssue] = []
    visited: List[str] = []
    state: Dict[str, str] = {}
    edge_count = 0

    def visit(provenance_id: str) -> None:
        nonlocal edge_count
        if state.get(provenance_id) == "done":
            return
        if state.get(provenance_id) == "visiting":
            issues.append(ChainIssue(
                "cycle", provenance_id,
                "cycle detected — node is its own ancestor",
            ))
            return
        state[provenance_id] = "visiting"

        node = store.get_node(provenance_id)
        if node is None:
            issues.append(ChainIssue(
                "missing_node", provenance_id,
                "referenced provenance_id absent from the lineage store",
            ))
            state[provenance_id] = "done"
            return

        visited.append(provenance_id)
        if not node.content_hash:
            issues.append(ChainIssue(
                "missing_content_hash", provenance_id,
                "node records no content hash",
            ))
        else:
            recomputed = recompute_fn(node)
            if recomputed is None:
                issues.append(ChainIssue(
                    "missing_content_hash", provenance_id,
                    "cannot re-derive content hash (bad stored fields)",
                ))
            elif recomputed != node.content_hash:
                issues.append(ChainIssue(
                    "tampered_content", provenance_id,
                    "recomputed content hash does not match recorded hash",
                ))

        for parent in node.parent_memory_ids:
            if parent == provenance_id:
                issues.append(ChainIssue(
                    "cycle", provenance_id,
                    "node lists itself as a parent",
                ))
                continue
            edge_count += 1
            visit(parent)

        state[provenance_id] = "done"

    visit(root_id)
    return ChainWalkResult(
        root_id=root_id,
        node_count=len(visited),
        edge_count=edge_count,
        issues=issues,
        ok=not issues,
        visited=visited,
    )


# ---------------------------------------------------------------------------
# Graph queries
# ---------------------------------------------------------------------------

def ancestors_of(store: LineageStore, root_id: str) -> List[str]:
    """provenance_ids transitively above ``root_id`` (via parent links)."""
    order: List[str] = []
    seen: set = set()

    def visit(provenance_id: str) -> None:
        node = store.get_node(provenance_id)
        if node is None:
            return
        for parent in node.parent_memory_ids:
            if parent not in seen:
                seen.add(parent)
                order.append(parent)
                visit(parent)

    visit(root_id)
    return order


def descendants_of(store: LineageStore, root_id: str) -> List[str]:
    """provenance_ids transitively derived from ``root_id``."""
    children: Dict[str, List[str]] = {}
    for node in store.all_nodes():
        for parent in node.parent_memory_ids:
            children.setdefault(parent, []).append(node.provenance_id)

    out: List[str] = []
    seen: set = set()
    stack = list(children.get(root_id, []))
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
        stack.extend(children.get(pid, []))
    return out
