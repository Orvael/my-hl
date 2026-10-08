# Created: WIB 2026-10-08 16:3x — HyperBFT-style consensus simulation (my-hl)
import hashlib
import json

from .journal import state_hash


class Node:
    """One replica. Honest nodes vote on the leader's block; jailed nodes
    are excluded; a node equivocating is jailed (specs/11)."""

    def __init__(self, name, engine_builder):
        self.name = name
        self.engine_builder = engine_builder
        self.engine = engine_builder()
        self.block_height = 0

    def apply(self, ts, actions):
        self.engine.apply_block(ts, actions)
        self.block_height += 1
        return state_hash(self.engine)


class HyperBFTSim:
    """Leader proposes; commit when stake-weighted votes > 2/3 of active stake.
    Missed vote or equivocation -> jailed, stake locked, NO slashing."""

    def __init__(self, stakes, engine_builder):
        self.stakes = dict(stakes)
        self.total_stake = sum(stakes.values())
        self.jailed = {}
        self.nodes = {n: Node(n, engine_builder) for n in stakes}
        self.height = 0
        self.leader_i = 0
        self.active = lambda: [n for n in sorted(self.stakes)
                               if n not in self.jailed]

    def _leader(self):
        act = self.active()
        if not act:
            return None
        return act[self.leader_i % len(act)]

    def commit(self, ts, actions, faulty=()):
        """One round: every honest active node executes the leader's block;
        commit when stake-weighted participation > 2/3. Missed vote = jailing."""
        leader = self._leader()
        if leader is None:
            return {"status": "halt"}
        votes = 0
        newly_jailed = []
        h = None
        for v in self.active():
            if v in faulty and v != leader:
                self.jailed[v] = self.height
                newly_jailed.append(v)
                continue
            h = self.nodes[v].apply(ts, actions)
            votes += self.stakes[v]
        self.height += 1
        self.leader_i += 1
        if h is None:
            return {"status": "halt", "jailed": newly_jailed}
        if votes * 3 > self.total_stake * 2:
            return {"status": "committed", "hash": h, "jailed": newly_jailed}
        return {"status": "no_quorum", "jailed": newly_jailed}

    def agree(self):
        """All non-jailed nodes at the same height hold the same state hash."""
        hashes = set()
        for n in self.nodes.values():
            if n.name in self.jailed:
                continue
            hashes.add(state_hash(n.engine))
        return len(hashes) <= 1
