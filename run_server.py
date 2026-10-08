# Created: WIB 2026-10-08 19:2x — my-hl testnet deployment runner
# Boots engine + chain + API + UI on one port. Live traffic becomes blocks:
# every BLOCK_S the runner submits buffered actions to the chain and checks a
# replica agrees (state hash match). Seeded with a live BTC book.
import sys
import os
import time
import threading
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hl.engine import Engine            # noqa: E402
from hl.chain import Blockchain         # noqa: E402
from hl.api import ApiState, make_server  # noqa: E402
from hl.journal import state_hash       # noqa: E402
from hl.config import SCALE             # noqa: E402

S = SCALE
PORT = int(os.environ.get("MYHL_PORT", "8770"))
BLOCK_S = 2

ASSETS = {"BTC": {"max_leverage": 20, "sz_decimals": 2,
                  "impact_notional_usd": 6_000 * S}}


def main():
    chain = Blockchain(ASSETS, {"v1": 40, "v2": 30, "v3": 30})
    from hl.spot import SpotStore
    from hl.staking import Staking
    from hl.vaults import HlpVault, VaultFactory
    spot = SpotStore()
    staking = Staking()
    vault = HlpVault()
    vfactory = VaultFactory()
    vfactory.create("public_vault_leader", "alpha", usd=100_000 * S,
                    ts=0, marks={"BTC": 82_000 * S})
    staking.register("v1", 40_000 * S, 3)
    staking.register("v2", 30_000 * S, 5)
    staking.register("v3", 30_000 * S, 5)
    # LEADER = its own engine (applies each action exactly once at dispatch).
    # Chain nodes are pure replicas that re-apply blocks in apply_block.
    api = ApiState(Engine(ASSETS), spot=spot,
                   staking=staking, vault=vault, vault_factory=vfactory)
    e = api.engine
    e.set_oracle("BTC", 82_000 * S)

    # spot: PURR/USDC seeded + Hyperliquidity quoting
    pidx, _ = spot.deploy("PURR", 5, 0, 600_000_000 * 10 ** 5, "system", {})
    spot._add("hip2_%d" % pidx, pidx, 5_000 * 10 ** 5)
    spot._add("hip2_%d" % pidx, 0, 20_000 * S)
    spot.place_spot("hip2_%d" % pidx, pidx, False, 4 * S, 10 * 10 ** 5, "ALO")
    spot.add_hl((pidx, 0), 4 * S, 3, 10 * 10 ** 5, 2, 0)

    # HLP vault seed equity
    marks0 = {"BTC": 82_000 * S}
    vault.deposit("treasury", 5_000_000 * S, 0, marks0)

    # genesis block: oracle + liquidity seed (each order under the $5M cap)
    seed = [{"t": "oracle", "coin": "BTC", "px": 82_000 * S},
            {"t": "deposit", "user": "lp_bot", "usd": 50_000_000 * S}]
    e.apply_block(0, seed)
    chain.submit(0, seed)
    for lvl in range(6):
        for side_px, is_buy in ((81_900 - lvl * 10, True),
                                (82_100 + lvl * 10, False)):
            a = {"t": "place", "user": "lp_bot", "coin": "BTC",
                 "is_buy": is_buy, "px": side_px * S, "sz": 40 * S,
                 "tif": "ALO"}
            e.apply_block(0, [a])
            chain.submit(0, [a])

    srv = make_server(api, chain=chain, host="0.0.0.0", port=PORT)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("my-hl testnet on 0.0.0.0:%d  (UI at http://localhost:%d/)"
          % (PORT, PORT), flush=True)

    # --- market simulation: sim_bot random-walk taker + lp_bot re-quotes ---
    import random
    rng = random.Random(20261008)
    api.rate_limit = 100_000
    seed2 = [{"t": "deposit", "user": "sim_bot", "usd": 20_000_000 * S},
             {"t": "deposit", "user": "sim_base", "usd": 1_000_000 * S}]
    for a in seed2:
        e.apply_block(int(time.time()), [a])
    chain.submit(int(time.time()), seed2)
    nonce = [1000]

    def bot(user, action):
        nonce[0] += 1
        return api.exchange({"action": action, "nonce": nonce[0],
                             "signature": {"signer": user}})

    last_quote = 0
    tick = 0
    checks = ok = 0
    drift = 0.0
    while True:
        time.sleep(0.4)
        tick += 1
        b = e.books["BTC"]
        mid = b.mid() or e.oracles["BTC"]
        # momentum random walk: persistent drift moves the mid over time
        drift += rng.gauss(0, 0.18)
        drift = max(-0.34, min(0.34, drift))
        if rng.random() < 0.65:
            side = rng.random() < (0.5 + drift)
            sz = rng.randint(2, 20) * S
            px = (mid * 1009 // 1000) // S * S if side else (mid * 991 // 1000) // S * S
            bot("sim_bot", {"type": "order", "orders": [
                {"coin": "BTC", "is_buy": bool(side), "limit_px": px,
                 "sz": sz, "tif": "IOC"}]})
            # oracle follows the market (validators publish market prices)
            if tick % 3 == 0:
                new_oracle = mid + (mid // 400) * (1 if drift > 0.05 else
                                                   (-1 if drift < -0.05 else 0))
                bot("oracle_feed", {"type": "oracle", "coin": "BTC",
                                    "px": new_oracle})
        # lp re-quotes every 1s around the moving mid (int ticks, via API)
        if tick % 2 == 0:
            for oid in list(b.orders):
                if b.orders[oid].user == "lp_bot":
                    bot("lp_bot", {"type": "cancel", "coin": "BTC", "oid": oid})
            m2 = b.mid() or mid
            half = m2 // 200  # 0.05% base spread
            sk = int(drift * 100)  # momentum MIGRATES the band midpoint
            for i in range(3):
                for is_buy in (True, False):
                    base = m2 + sk * 2 * S  # both sides shift with drift
                    px_off = (base + (half + i * 5 * S) * (1 if not is_buy
                                                           else -1)) // S * S
                    bot("lp_bot", {"type": "order", "orders": [
                        {"coin": "BTC", "is_buy": is_buy, "limit_px": px_off,
                         "sz": 50 * S, "tif": "ALO"}]})
        # --- block production: atomic drain + passes + submit ---
        # The leader (api engine) applied actions at dispatch; the passes run
        # here under the same lock and timestamp as the replicas' apply_block,
        # and the leader hash is captured before any new order can interleave.
        if tick % 8 == 0:
            spot.hl_pass((pidx, 0), int(time.time()))
        with api.lock:
            actions = []
            if api.action_log:
                actions = [a for _, batch in api.action_log for a in batch]
                api.action_log = []
            ts = int(time.time())
            if actions:
                e.run_passes(ts)
                leader_hash = state_hash(e)
        if actions:
            r = chain.submit(ts, actions)
            if r.get("status") == "committed":
                checks += 1
                if leader_hash == r["state_hash"]:
                    ok += 1
                else:
                    from hl.journal import state_dict
                    ld = state_dict(e)
                    key = "?"
                    for name in sorted(chain.sim.nodes):
                        rd = state_dict(chain.sim.nodes[name].engine)
                        if rd == ld:
                            continue
                        for k in ld:
                            if ld[k] != rd.get(k):
                                key = k
                                break
                        break
                    print("MISMATCH h%d key=%s" % (
                        chain.head().header["height"], key), flush=True)
        if tick % 10 == 0:
            print("t%d bids %d asks %d mid %.1f | blocks %d agree %d"
                  % (tick, len(b.bids), len(b.asks), mid / 1e8, checks, ok),
                  flush=True)


def _mk_pos(coin, szi, px):
    from hl.types import Position
    return Position(coin, szi, px)


if __name__ == "__main__":
    main()
