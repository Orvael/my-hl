# Created: WIB 2026-10-08 18:2x — the blockchain layer (my-hl, specs/13)
import hashlib
import json

from .consensus import HyperBFTSim
from .journal import state_hash


def _h(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def action_hash(actions):
    return _h(actions)


class Block:
    __slots__ = ("header", "actions")

    def __init__(self, header, actions):
        self.header = header
        self.actions = actions

    def hash(self):
        return _h(self.header)


class Blockchain:
    """Hash-linked blocks with one-block finality (HyperBFT commit).
    verify_chain replays all actions from genesis into a fresh engine and
    must reproduce the last block's state_hash — tamper evidence."""

    def __init__(self, assets, stakes, validators=None):
        from .engine import Engine
        self.assets = assets
        self.stakes = stakes
        self.sim = HyperBFTSim(stakes, lambda: Engine(assets))
        self.blocks = []
        genesis_header = {
            "height": 0,
            "prev_hash": "0x" + "00" * 32,
            "ts": 0,
            "action_hash": _h([]),
            "proposer": "GENESIS",
            "state_hash": state_hash(self.sim.nodes[self.sim._leader()].engine),
        }
        self.blocks.append(Block(genesis_header, []))

    def submit(self, ts, actions, faulty=()):
        """Runs one consensus round; on commit, appends a linked block."""
        r = self.sim.commit(ts, actions, faulty=faulty)
        if r["status"] != "committed":
            return r
        prev = self.blocks[-1].header
        h = self.sim.nodes[self.sim._leader()].engine
        header = {
            "height": prev["height"] + 1,
            "prev_hash": self.blocks[-1].hash(),
            "ts": ts,
            "action_hash": action_hash(actions),
            "proposer": self.sim._leader(),
            "state_hash": r["hash"],
        }
        self.blocks.append(Block(header, actions))
        return {"status": "committed", "height": header["height"],
                "hash": self.blocks[-1].hash()}

    def head(self):
        return self.blocks[-1]

    def block(self, height):
        if 0 <= height < len(self.blocks):
            return self.blocks[height]
        return None

    def verify_chain(self):
        """Re-hash links + replay from genesis; returns (ok, detail)."""
        for i in range(1, len(self.blocks)):
            if self.blocks[i].header["prev_hash"] != self.blocks[i - 1].hash():
                return False, "prev_hash broken at %d" % i
            if self.blocks[i].header["action_hash"] != action_hash(self.blocks[i].actions):
                return False, "action_hash mismatch at %d" % i
        from .engine import Engine
        e = Engine(self.assets)
        for b in self.blocks[1:]:
            e.apply_block(b.header["ts"], b.actions)
        last = self.blocks[-1].header["state_hash"]
        if state_hash(e) != last:
            return False, "replay state mismatch"
        return True, "ok"

    def to_json(self, height):
        b = self.block(height)
        if b is None:
            return None
        return {"header": b.header, "actions": b.actions}