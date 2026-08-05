"""
Provenance lineage tests (Phase 8).

Covers the first-class derivation chain:
- node construction from a stamped memory (content-hash binding)
- integrity walking (tamper detection, broken parents, cycles)
- ancestor / descendant queries
- in-memory and SQLite persistence round-trips
"""

import pytest

from tests.conftest import make_memory, REFERENCE_TIME
from lcm_core.lineage import (
    ChainIssue,
    InMemoryLineageStore,
    LineageNode,
    ancestors_of,
    descendants_of,
    node_from_stamped,
    walk_chain,
)
from lcm_core.provenance import _compute_content_hash
from lcm_service.storage import SQLiteStorage, SQLiteLineageStore


def _build_chain(store, root, n, *, prefix="node"):
    """Record a linear chain root -> ... -> leaf; returns [root, ..., leaf]."""
    nodes = [root]
    parent = root
    for i in range(1, n):
        child = make_memory(
            agent=f"a{i}",
            payload={f"{prefix}.{i}": f"v{i}"},
            path=f"{prefix}.{i}",
        )
        child = child.model_copy(update={
            "provenance_info": child.provenance_info.model_copy(update={
                "parent_memory_ids": [parent.provenance_id],
            })
        })
        store.add_node(node_from_stamped(child, path=f"{prefix}.{i}"))
        nodes.append(child)
        parent = child
    return nodes


class TestNodeFromStamped:
    def test_reproduces_committed_content_hash_when_no_extra_parents(self):
        stamped = make_memory(agent="alice", payload={"k": 1})
        node = node_from_stamped(stamped, path="k")
        assert node.provenance_id == stamped.provenance_id
        assert node.content_hash == _compute_content_hash(
            "alice", stamped.timestamp, {"k": 1},
        )
        assert node.agent_id == "alice"
        assert node.parent_memory_ids == []

    def test_extra_parents_bound_into_hash_without_mutating_memory(self):
        stamped = make_memory(agent="alice", payload={"k": 1})
        node = node_from_stamped(stamped, path="k", extra_parents=["p1", "p2"])
        assert node.parent_memory_ids == ["p1", "p2"]
        assert node.content_hash == _compute_content_hash(
            "alice", stamped.timestamp, {"k": 1},
            parent_hashes=["p1", "p2"],
        )
        # The committed memory itself is untouched.
        assert stamped.provenance_info.parent_memory_ids == []

    def test_extra_parents_exclude_own_id_and_dedupe(self):
        stamped = make_memory(agent="alice", payload={"k": 1})
        node = node_from_stamped(
            stamped, path="k",
            extra_parents=[stamped.provenance_id, "p1", "p1"],
        )
        assert node.parent_memory_ids == ["p1"]


class TestWalkChain:
    def test_linear_chain_walks_clean(self):
        store = InMemoryLineageStore()
        root = make_memory(agent="root", payload={"root": "r"})
        store.add_node(node_from_stamped(root, path="root"))
        nodes = _build_chain(store, root, n=4)
        leaf = nodes[-1]
        result = walk_chain(store, leaf.provenance_id)
        assert result.ok
        assert result.issues == []
        assert result.node_count == 4
        assert result.edge_count == 3

    def test_walk_from_single_node(self):
        store = InMemoryLineageStore()
        root = make_memory(agent="r", payload={"k": "v"})
        store.add_node(node_from_stamped(root, path="k"))
        result = walk_chain(store, root.provenance_id)
        assert result.ok
        assert result.node_count == 1
        assert result.edge_count == 0

    def test_dangling_parent_reported(self):
        store = InMemoryLineageStore()
        stamped = make_memory(agent="alice", payload={"k": 1})
        stamped = stamped.model_copy(update={
            "provenance_info": stamped.provenance_info.model_copy(update={
                "parent_memory_ids": ["ghost-parent"],
            })
        })
        store.add_node(node_from_stamped(stamped, path="k"))
        result = walk_chain(store, stamped.provenance_id)
        assert not result.ok
        kinds = {i.kind for i in result.issues}
        assert "missing_node" in kinds

    def test_self_reference_is_cycle(self):
        store = InMemoryLineageStore()
        stamped = make_memory(agent="alice", payload={"k": 1})
        stamped = stamped.model_copy(update={
            "provenance_info": stamped.provenance_info.model_copy(update={
                "parent_memory_ids": [stamped.provenance_id],
            })
        })
        store.add_node(node_from_stamped(stamped, path="k"))
        result = walk_chain(store, stamped.provenance_id)
        assert not result.ok
        assert any(i.kind == "cycle" for i in result.issues)

    def test_mutual_cycle_detected(self):
        store = InMemoryLineageStore()
        a = make_memory(agent="a", payload={"k": "a"})
        b = make_memory(agent="b", payload={"k": "b"})
        a2 = a.model_copy(update={
            "provenance_info": a.provenance_info.model_copy(update={
                "parent_memory_ids": [b.provenance_id],
            })
        })
        b2 = b.model_copy(update={
            "provenance_info": b.provenance_info.model_copy(update={
                "parent_memory_ids": [a.provenance_id],
            })
        })
        store.add_node(node_from_stamped(a2, path="k"))
        store.add_node(node_from_stamped(b2, path="k"))
        result = walk_chain(store, a.provenance_id)
        assert not result.ok
        assert any(i.kind == "cycle" for i in result.issues)

    def test_tampered_payload_detected(self):
        store = InMemoryLineageStore()
        stamped = make_memory(agent="alice", payload={"k": 1})
        store.add_node(node_from_stamped(stamped, path="k"))
        node = store.get_node(stamped.provenance_id)
        node.payload = {"agent_id": "alice", "timestamp": node.timestamp,
                        "assertion_payload": {"k": 999}}
        result = walk_chain(store, stamped.provenance_id)
        assert not result.ok
        assert any(i.kind == "tampered_content" for i in result.issues)

    def test_tampered_parent_reference_detected(self):
        store = InMemoryLineageStore()
        root = make_memory(agent="root", payload={"root": "r"})
        store.add_node(node_from_stamped(root, path="root"))
        child = make_memory(agent="child", payload={"child": "c"})
        child = child.model_copy(update={
            "provenance_info": child.provenance_info.model_copy(update={
                "parent_memory_ids": [root.provenance_id],
            })
        })
        store.add_node(node_from_stamped(child, path="child"))
        assert walk_chain(store, child.provenance_id).ok

        # Attacker rewrites the child's parent pointer — its own content hash
        # (which committed to the ORIGINAL parent id) must no longer match.
        node = store.get_node(child.provenance_id)
        node.parent_memory_ids = ["forged-parent"]
        result = walk_chain(store, child.provenance_id)
        assert not result.ok
        assert any(i.kind == "tampered_content" for i in result.issues)

    def test_custom_recompute_is_used(self):
        store = InMemoryLineageStore()
        stamped = make_memory(agent="alice", payload={"k": 1})
        store.add_node(node_from_stamped(stamped, path="k"))

        def evil_recompute(node):
            return "0" * 64

        result = walk_chain(store, stamped.provenance_id, recompute=evil_recompute)
        assert not result.ok
        assert any(i.kind == "tampered_content" for i in result.issues)


class TestGraphQueries:
    def test_ancestors_of_leaf(self):
        store = InMemoryLineageStore()
        root = make_memory(agent="root", payload={"root": "r"})
        store.add_node(node_from_stamped(root, path="root"))
        nodes = _build_chain(store, root, n=4)
        leaf = nodes[-1]
        anc = ancestors_of(store, leaf.provenance_id)
        assert root.provenance_id in anc
        assert leaf.provenance_id not in anc
        assert len(anc) == 3

    def test_descendants_of_root(self):
        store = InMemoryLineageStore()
        root = make_memory(agent="root", payload={"root": "r"})
        store.add_node(node_from_stamped(root, path="root"))
        nodes = _build_chain(store, root, n=4)
        desc = descendants_of(store, root.provenance_id)
        assert sorted(desc) == sorted([n.provenance_id for n in nodes[1:]])


class TestPersistence:
    def test_node_dict_round_trip(self):
        stamped = make_memory(agent="alice", payload={"k": 1})
        node = node_from_stamped(stamped, path="k", extra_parents=["p1"])
        clone = LineageNode.from_dict(node.to_dict())
        assert clone == node

    def test_sqlite_round_trip(self):
        store = InMemoryLineageStore()
        root = make_memory(agent="root", payload={"root": "r"})
        store.add_node(node_from_stamped(root, path="root"))
        nodes = _build_chain(store, root, n=3)

        s = SQLiteStorage(":memory:")
        sqlite_store = SQLiteLineageStore(s)
        for node in store.all_nodes():
            sqlite_store.add_node(node)

        assert s.lineage_node_count() == 3
        fetched = s.get_lineage_node(nodes[-1].provenance_id)
        assert fetched == store.get_node(nodes[-1].provenance_id)
        assert len(sqlite_store.all_nodes()) == 3

        # Fresh storage must start empty.
        assert SQLiteStorage(":memory:").lineage_node_count() == 0

    def test_sqlite_durable_across_connections(self, tmp_path):
        db = tmp_path / "lineage.db"
        s1 = SQLiteStorage(str(db))
        stamped = make_memory(agent="alice", payload={"k": 1})
        s1.store_lineage_node(node_from_stamped(stamped, path="k"))

        s2 = SQLiteStorage(str(db))
        node = s2.get_lineage_node(stamped.provenance_id)
        assert node is not None
        assert node.content_hash == _compute_content_hash(
            "alice", stamped.timestamp, {"k": 1},
        )

    def test_sqlite_walk_clean(self):
        s = SQLiteStorage(":memory:")
        store = SQLiteLineageStore(s)
        root = make_memory(agent="root", payload={"root": "r"})
        store.add_node(node_from_stamped(root, path="root"))
        nodes = _build_chain(store, root, n=3)
        result = walk_chain(store, nodes[-1].provenance_id)
        assert result.ok
        assert result.node_count == 3
