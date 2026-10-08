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

    checks = ok = 0
    while True:
        time.sleep(BLOCK_S)
        actions = []
        with api.lock:
            if api.action_log:
                actions = [a for _, a in api.action_log]
                api.action_log = []
        if not actions:
            continue
        r = chain.submit(int(time.time()), actions)
        if r.get("status") != "committed":
            continue
        checks += 1
        if state_hash(e) == r["hash"]:
            ok += 1
        if checks % 25 == 0:
            print("blocks %d | replica-agree %d | height %d"
                  % (checks, ok, chain.head().header["height"]), flush=True)


if __name__ == "__main__":
    main()
