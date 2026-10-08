# Created: WIB 2026-10-08 14:5x — journal, replay, state hash (my-hl)
import json
import hashlib

from .engine import Engine

_ASSETS = {
    "BTC": {"max_leverage": 20, "sz_decimals": 2,
            "impact_notional_usd": 200_000 * 10**8},
}


def state_dict(e):
    accounts = {}
    for u in sorted(e.ch.accounts):
        a = e.ch.accounts[u]
        positions = {}
        for c in sorted(a.positions):
            p = a.positions[c]
            positions[c] = [p.szi, p.entry_px]
        accounts[u] = [a.usd, positions]
    books = {}
    for c in sorted(e.books):
        b = e.books[c]
        books[c] = {
            "bids": {str(px): [[o.oid, o.sz_rem] for o in b.bids[px]]
                     for px in sorted(b.bids, reverse=True)},
            "asks": {str(px): [[o.oid, o.sz_rem] for o in b.asks[px]]
                     for px in sorted(b.asks)},
        }
    return {
        "accounts": accounts,
        "books": books,
        "oracles": {c: e.oracles[c] for c in sorted(e.oracles)},
        "next_oid": e.next_oid,
        "block_ts": e.block_ts,
        "last_funding_ts": e.last_funding_ts,
    }


def state_hash(e):
    blob = json.dumps(state_dict(e), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def append_block(fp, ts, actions):
    fp.write(json.dumps({"ts": ts, "actions": actions}, sort_keys=True,
                        separators=(",", ":")) + "\n")


def replay(path, assets=None):
    """Rebuild an Engine from a JSONL journal. Returns (engine, hash)."""
    assets = assets or _ASSETS
    e = Engine(assets)
    with open(path) as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            blk = json.loads(line)
            e.apply_block(blk["ts"], blk["actions"])
    return e, state_hash(e)
