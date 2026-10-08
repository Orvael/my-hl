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
    api = ApiState(chain.sim.nodes[chain.sim._leader()].engine)
    e = api.engine
    e.set_oracle("BTC", 82_000 * S)

    # genesis block: oracle + liquidity seed (each order under the $5M cap)
    seed = [{"t": "oracle", "coin": "BTC", "px": 82_000 * S},
            {"t": "deposit", "user": "lp_bot", "usd": 50_000_000 * S}]
    chain.submit(0, seed)
    e.deposit("lp_bot", 50_000_000 * S)
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
    chain.submit(int(time.time()), seed2)
    for a in seed2:
        e.apply_block(int(time.time()), [a])
    e.ch.acc("sim_base").positions["BTC"] = _mk_pos("BTC", 400 * S, 82_000 * S)
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
                e.set_oracle("BTC", new_oracle)
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
        # --- block production from live traffic ---
        actions = []
        with api.lock:
            if api.action_log:
                actions = [a for _, a in api.action_log]
                api.action_log = []
        if actions:
            ts = int(time.time())
            r = chain.submit(ts, actions)
            e.run_passes(ts)
            if r.get("status") == "committed":
                checks += 1
                if state_hash(e) == r["hash"]:
                    ok += 1
                else:
                    print("REPLICA MISMATCH at height %d" %
                          chain.head().header["height"], flush=True)
        if tick % 10 == 0:
            print("t%d bids %d asks %d mid %.1f | blocks %d agree %d"
                  % (tick, len(b.bids), len(b.asks), mid / 1e8, checks, ok),
                  flush=True)


def _mk_pos(coin, szi, px):
    from hl.types import Position
    return Position(coin, szi, px)


if __name__ == "__main__":
    main()
