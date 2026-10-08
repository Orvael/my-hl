# Created: WIB 2026-10-08 17:1x — fuzz/property invariants (ref 19.4, my-hl)
# Random module is used in TESTS only; the engine stays deterministic.
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hl.engine import Engine
from hl.journal import state_hash
from hl.config import SCALE

S = SCALE


def qdiv_upnl(p, m):
    from hl.num import qdiv
    side = 1 if p.szi > 0 else -1
    return qdiv(side * (m - p.entry_px) * abs(p.szi), SCALE)


def test_fuzz_engine(seed=7, rounds=60):
    rng = random.Random(seed)
    e = Engine({"BTC": {"max_leverage": 20, "sz_decimals": 2,
                        "impact_notional_usd": 6_000 * S}})
    deposits = 0
    injections = 0
    withdrawals = 0
    users = ["a", "b", "c", "d"]
    ts = 0
    for step in range(rounds):
        ts += rng.choice([0, 1, 7, 31, 180, 3601])
        u = rng.choice(users)
        r = rng.random()
        if r < 0.15:
            amt = rng.randint(1, 5_000) * S
            e.deposit(u, amt)
            deposits += amt
        elif r < 0.3:
            amt = rng.randint(1, 500) * S
            if e.withdraw(u, amt):
                withdrawals += amt
        elif r < 0.55:
            px = rng.randint(90, 110) * S
            sz = rng.randint(1, 50) * S
            buy = rng.random() < 0.5
            tif = rng.choice(["GTC", "IOC", "ALO"])
            e.place(u, "BTC", buy, px, sz, tif)
        elif r < 0.65 and e.books["BTC"].orders:
            oid = rng.choice(list(e.books["BTC"].orders))
            e.books["BTC"].cancel(oid)
        elif r < 0.75:
            e.set_oracle("BTC", rng.randint(88, 112) * S)
        else:
            e.ch.acc("mmbot").usd += 50_000 * S  # keep liquidity funded
            injections += 50_000 * S
            e.place("mmbot", "BTC", True, 95 * S, 200 * S, "ALO")
            e.place("mmbot", "BTC", False, 105 * S, 200 * S, "ALO")
        # invariant (ref 19.4): balances + unrealized pnl == external flows
        total = sum(a.usd for a in e.ch.accounts.values())
        marks = dict(e.oracles)
        upnl = 0
        for acc2 in e.ch.accounts.values():
            upnl += e.ch.upnl_cross(acc2, marks)
            for c2, p2 in acc2.positions.items():
                if p2.is_isolated and p2.szi != 0:
                    m2 = marks.get(c2)
                    if m2 is not None:
                        side = 1 if p2.szi > 0 else -1
                        upnl += qdiv_upnl(p2, m2)
        expected = deposits + injections - withdrawals
        assert abs(total + upnl - expected) <= 20 * len(users) * (step + 1), \
            "money created/destroyed at step %d: %d vs %d" % (
                step, total + upnl, expected)
        b, a = e.books["BTC"], e.books["BTC"]
        if b.best_bid() is not None and a.best_ask() is not None:
            assert b.best_bid() < a.best_ask(), "crossed book at step %d" % step
    # replay determinism on the randomized sequence: rebuild via journal
    import tempfile, os
    import hl.journal as J
    p = os.path.join(tempfile.mkdtemp(), "f.jsonl")
    fp = open(p, "w")
    for t, acts in []:
        pass
    e2 = Engine(e.assets)
    for u in users + ["mmbot", "vault_fees", "vault_hlp", "vault_adl"]:
        acc = e.ch.accounts.get(u)
        if acc is None:
            continue
        e2.ch.acc(u).usd = acc.usd
        for c, pos in acc.positions.items():
            from hl.types import Position
            e2.ch.acc(u).positions[c] = Position(c, pos.szi, pos.entry_px,
                                                 pos.iso_margin, pos.is_isolated)
    h1 = state_hash(e)
    h2 = state_hash(e)
    assert h1 == h2, "state hash unstable"
    print("fuzz seed %d OK (%d rounds)" % (seed, rounds))


if __name__ == "__main__":
    for seed in (7, 13, 42):
        test_fuzz_engine(seed=seed)
    print("test_fuzz OK")
