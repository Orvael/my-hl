# Created: WIB 2026-10-08 19:2x — my-hl testnet deployment runner
# Boots engine + chain + API + UI on one port. Live traffic becomes blocks:
# every BLOCK_S the runner submits buffered actions to the chain and checks a
# replica agrees (state hash match). Seeded with a live BTC book.
import sys
import os
import time
import threading
import json
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hl.engine import Engine            # noqa: E402
from hl.chain import Blockchain         # noqa: E402
from hl.api import ApiState, make_server  # noqa: E402
from hl.journal import state_hash       # noqa: E402
from hl.config import SCALE             # noqa: E402

S = SCALE
PORT = int(os.environ.get("MYHL_PORT", "8770"))
BLOCK_S = 2

# Real Hyperliquid majors — leverage per the verified margin-tiers table,
# impact notional 20k BTC/ETH + 6k others (specs/07).
ASSETS = {"BTC": {"max_leverage": 40, "sz_decimals": 5,
                  "impact_notional_usd": 20_000 * S},
          "ETH": {"max_leverage": 25, "sz_decimals": 4,
                  "impact_notional_usd": 20_000 * S},
          "SOL": {"max_leverage": 20, "sz_decimals": 2,
                  "impact_notional_usd": 6_000 * S},
          "XRP": {"max_leverage": 20, "sz_decimals": 1,
                  "impact_notional_usd": 6_000 * S},
          "DOGE": {"max_leverage": 10, "sz_decimals": 0,
                   "impact_notional_usd": 6_000 * S},
          "HYPE": {"max_leverage": 10, "sz_decimals": 2,
                   "impact_notional_usd": 6_000 * S}}
# Real prices, fetched live from Hyperliquid's public API at boot (one call,
# no RPC drain). Offline fallback = last known values.
def fetch_real_mids():
    fallback = {"BTC": 82_000, "ETH": 2_490, "SOL": 110, "XRP": 1.39,
                "DOGE": 0.085, "HYPE": 85}
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.hyperliquid.xyz/info",
            data=json.dumps({"type": "allMids"}).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "Mozilla/5.0"})
        out = json.loads(urllib.request.urlopen(req, timeout=8).read())
        return {c: float(out[c]) for c in ASSETS if c in out}
    except Exception:
        return {}


_REAL = fetch_real_mids()
_FALLBACK = {"BTC": 82_000, "ETH": 2_490, "SOL": 110, "XRP": 1.39,
             "DOGE": 0.085, "HYPE": 85}
SEED_MIDS = {**_FALLBACK, **_REAL}


def q5(px):
    """Quantize an S-scaled int price to the 5-sig-fig tick (specs/05).
    INT-ONLY: 10 ** negative-exp is a float in Python, and float px breaks
    px_valid's sig-fig check AND the engine's int-only money math."""
    import math
    usd = px / S
    if usd <= 0:
        return 1
    exp = int(math.floor(math.log10(usd)))
    if exp >= 4:
        q = 10 ** (exp - 4) * S
    else:
        q = max(1, S // (10 ** (4 - exp)))
    return max(1, (px // q) * q)


def tick_of(coin):
    """Smallest valid price step: 10^(2+szDecimals) S-units (specs/05)."""
    return 10 ** (2 + ASSETS[coin]["sz_decimals"])


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
    for c0, m0 in SEED_MIDS.items():
        e.set_oracle(c0, m0 * S)

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
    seed = [{"t": "oracle", "coin": c0, "px": m0 * S}
            for c0, m0 in SEED_MIDS.items()]
    seed.append({"t": "deposit", "user": "lp_bot", "usd": 50_000_000 * S})
    e.apply_block(0, seed)
    chain.submit(0, seed)
    for c0, m0 in SEED_MIDS.items():
        off = max(1, m0 * 13 // 10_000)   # ~0.13% seed spread
        step = max(1, m0 // 8_000)
        for lvl in range(6):
            for side_px, is_buy in ((m0 - off - lvl * step, True),
                                    (m0 + off + lvl * step, False)):
                a = {"t": "place", "user": "lp_bot", "coin": c0,
                     "is_buy": is_buy, "px": side_px * S, "sz": 40 * S,
                     "tif": "ALO"}
                e.apply_block(0, [a])
                chain.submit(0, [a])

    # candle history: 240 minutes of seeded walk so charts open with depth
    api.candles = {c: [] for c in ASSETS}
    for coin in ASSETS:
        px = SEED_MIDS[coin]
        t0 = int(time.time() * 1000) // 60000 * 60000 - 240 * 60000
        rng2 = random.Random(hash(coin) % 9999)
        for i in range(240):
            o = px
            steps = [rng2.gauss(0, px / 400) for _ in range(12)]
            h = px + max(max(steps), 0)
            l = px + min(min(steps), 0)
            px = max(px + steps[-1], px // 100)
            api.candles[coin].append(
                {"t": (t0 + i * 60000) // 1000, "o": round(o, 6),
                 "h": round(h, 6), "l": round(l, 6), "c": round(px, 6)})

    srv = make_server(api, chain=chain, host="0.0.0.0", port=PORT)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("my-hl testnet on 0.0.0.0:%d  (UI at http://localhost:%d/)"
          % (PORT, PORT), flush=True)

    # --- market simulation: sim_bot random-walk taker + lp_bot re-quotes ---
    rng = random.Random(20261008)
    api.rate_limit = 100_000
    seed2 = [{"t": "deposit", "user": "sim_bot", "usd": 20_000_000 * S},
             {"t": "deposit", "user": "sim_base", "usd": 1_000_000 * S}]
    for a in seed2:
        e.apply_block(int(time.time()), [a])
    chain.submit(int(time.time()), seed2)
    nonce = [1000]

    stats = {"filled": 0, "canceled": 0, "rejected": 0}
    def bot(user, action):
        nonce[0] += 1
        r = api.exchange({"action": action, "nonce": nonce[0],
                          "signature": {"signer": user}})
        try:
            st = r["response"]["statuses"][0]["status"]                 if r.get("status") == "ok" else r.get("response")
            if st == "filled":
                stats["filled"] += 1
            elif st == "canceled":
                stats["canceled"] += 1
            else:
                stats["rejected"] += 1
                if stats["rejected"] % 25 == 1:
                    print("BOT REJECT: %s action=%s" % (r, action), flush=True)
        except Exception:
            pass
        return r

    tick = 0
    checks = ok = 0
    leader_hash = None
    drift = {c: 0.0 for c in ASSETS}
    while True:
        time.sleep(0.4)
        tick += 1
        mids = {}
        for coin in ASSETS:
            b = e.books[coin]
            mid = b.mid() or e.oracles[coin]
            mids[coin] = mid
            # momentum random walk per market: drift moves the mid over time
            drift[coin] = max(-0.34, min(0.34,
                drift[coin] + rng.gauss(0, 0.18)))
            if rng.random() < 0.55:
                side = rng.random() < (0.5 + drift[coin])
                sz = rng.randint(2, 20) * S
                # offset >= spread: on wide-for-size markets (DOGE +/- $0.01
                # on $0.15) a fixed +/-0.9% never crosses
                hf = max(q5(S // 100), mid // 200)
                off = max(mid * 1009 // 1000 - mid, hf * 2)
                off2 = max(mid - mid * 991 // 1000, hf * 2)
                px = max(tick_of(coin), q5(mid + off)) if side else \
                    max(tick_of(coin), q5(mid - off2))
                bot("sim_bot", {"type": "order", "orders": [
                    {"coin": coin, "is_buy": bool(side), "limit_px": px,
                     "sz": sz, "tif": "IOC"}]})
                # oracle follows the market but anchors to the seed mid:
                # pure relative steps compound unbounded on small mids
                if tick % 3 == 0:
                    pull = (SEED_MIDS[coin] * S - mid) // 64
                    d = 1 if drift[coin] > 0.05 else \
                        (-1 if drift[coin] < -0.05 else 0)
                    new_oracle = q5(mid + pull + (mid // 400) * d)
                    bot("oracle_feed", {"type": "oracle", "coin": coin,
                                        "px": new_oracle})
        # lp re-quotes every 1s around each moving mid (via API)
        if tick % 2 == 0:
            for coin in ASSETS:
                bb = e.books[coin]
                for oid in list(bb.orders):
                    if bb.orders[oid].user == "lp_bot":
                        bot("lp_bot", {"type": "cancel", "coin": coin,
                                       "oid": oid})
                m2 = bb.mid() or mids[coin]
                half = max(q5(S // 100), m2 // 200)  # >=1 tick spread
                # drift TILTS the band (tightens the side momentum favors)
                # but the band center stays ON the mid: a center shift makes
                # the band lead the mid and the mid chase it -> ratchet
                tilt = m2 * int(drift[coin] * 100) // 40_000
                for i in range(3):
                    for is_buy in (True, False):
                        lvl = max(tick_of(coin), m2 // 4_000)
                        px_off = max(tick_of(coin),
                                     q5(m2 + (half + i * lvl) *
                                        (-1 if is_buy else 1) +
                                        (tilt if is_buy else -tilt)))
                        bot("lp_bot", {"type": "order", "orders": [
                            {"coin": coin, "is_buy": is_buy,
                             "limit_px": px_off, "sz": 50 * S,
                             "tif": "ALO"}]})
        # live candle updates per market
        for coin in ASSETS:
            cstore = api.candles[coin]
            if not cstore:
                continue
            cm = e.books[coin].mid()
            if cm is None:
                continue
            px = cm / 1e8
            mnow = int(time.time() * 1000) // 60000 * 60000 // 1000
            last = cstore[-1]
            if last["t"] != mnow:
                cstore.append({"t": mnow, "o": last["c"], "h": max(last["c"], px),
                               "l": min(last["c"], px), "c": round(px, 6)})
                if len(cstore) > 600:
                    cstore.pop(0)
            else:
                last["h"] = max(last["h"], px)
                last["l"] = min(last["l"], px)
                last["c"] = round(px, 6)

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
            b = e.books["BTC"]
            print("t%d BTC mid %.1f | blocks %d agree %d"
                  % (tick, mids["BTC"] / 1e8, checks, ok), flush=True)


def _mk_pos(coin, szi, px):
    from hl.types import Position
    return Position(coin, szi, px)


if __name__ == "__main__":
    main()
