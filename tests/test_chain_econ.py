# Created: WIB 2026-10-08 18:3x — chain + economy sweep tests (my-hl)
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hl.chain import Blockchain, action_hash
from hl.engine import Engine
from hl.spot import SpotStore
from hl.api import ApiState
from hl.evm_side import EvmSide
from hl.config import SCALE

S = SCALE
FAILS = []


def check(cond, label):
    if not cond:
        FAILS.append(label)
    return cond


def end():
    if FAILS:
        print("FAILS:", FAILS)
        sys.exit(1)
    print("test_chain_econ OK")
    FAILS.clear()


ASSETS = {"BTC": {"max_leverage": 20, "sz_decimals": 2,
                  "impact_notional_usd": 6_000 * S}}


def test_chain():
    ch = Blockchain(ASSETS, {"v1": 40, "v2": 30, "v3": 30})
    check(ch.head().header["height"] == 0, "ch-genesis")
    r = ch.submit(100, [{"t": "oracle", "coin": "BTC", "px": 100 * S},
                        {"t": "deposit", "user": "a", "usd": 1_000 * S}])
    check(r["status"] == "committed" and r["height"] == 1, "ch-block1")
    ch.submit(200, [{"t": "deposit", "user": "b", "usd": 500 * S}])
    check(len(ch.blocks) == 3, "ch-3-blocks")
    h2 = ch.block(2).header
    check(h2["prev_hash"] == ch.block(1).hash(), "ch-link")
    ok, why = ch.verify_chain()
    check(ok, "ch-verify")
    # tamper: rewrite an action inside block 1
    ch.blocks[1].actions[1]["usd"] = 1
    ok, why = ch.verify_chain()
    check(not ok and "action_hash" in why, "ch-tamper-action")
    ch.blocks[1].actions[1]["usd"] = 1_000 * S
    # tamper: break the hash link
    ch.blocks[2].header["prev_hash"] = "0xdead"
    ok, why = ch.verify_chain()
    check(not ok and "prev_hash" in why, "ch-tamper-link")
    end()


def test_fee_tiers_referrals():
    e = Engine(ASSETS)
    e.deposit("whale", 50_000_000 * S)
    e.deposit("ref", 20_000 * S)
    e.deposit("new", 20_000 * S)
    e.deposit("lp", 2_000_000 * S)
    e.place("lp", "BTC", True, 100 * S, 100_000 * S, "ALO")
    # whale trades 10M notional in two cap-sized ($5M) taker orders -> tier 1
    for _ in range(2):
        st, fills = e.place("whale", "BTC", False, 100 * S, 50_000 * S, "IOC")
        check(st == "filled", "ft-fill")
    check(e.fee_tier("whale") >= 1, "ft-tier1")
    check(e.taker_fee_for("whale") == 40_000, "ft-rate")
    # referral: codeowner needs 10k volume first; lp re-prices an ask
    e.deposit("codeowner", 20_000 * S)
    e.place("lp", "BTC", False, 100 * S, 10_000 * S, "ALO")  # fresh ask
    e.place("codeowner", "BTC", True, 100 * S, 200 * S, "IOC")
    check(e.volume.get("codeowner", 0) >= 10_000 * S, "fr-vol")
    ok, why = e.refer("codeowner", "new")
    check(ok, "fr-ok")
    before = e.ch.acc("codeowner").usd
    e.builder = None
    st, fills = e.place("new", "BTC", True, 100 * S, 100 * S, "IOC")
    check(st == "filled", "fr-fill")
    check(e.ch.acc("codeowner").usd > before, "fr-referrer-paid")
    # new's taker fee carried the 4% discount: rate 45k*0.96 = 43_200
    check(e.taker_fee_for("new") == 43_200, "fr-discount")
    end()


def test_delist():
    e = Engine(ASSETS)
    e.set_oracle("BTC", 100 * S)
    e.deposit("alice", 10_000 * S)
    e.deposit("carol", 10_000 * S)
    e.place("carol", "BTC", True, 100 * S, 100 * S, "ALO")
    e.place("alice", "BTC", False, 100 * S, 100 * S, "IOC")
    # 1h TWAP settle px supplied by the caller (validator-fed)
    settled = e.delist("BTC", 105 * S)
    check(settled >= 1, "dl-settled")
    check(not e.books["BTC"].orders, "dl-orders-canceled")
    st, why = e.place("alice", "BTC", True, 105 * S, 10 * S)
    check(st is None and why == "delisted", "dl-no-new")
    end()


def test_multisig():
    api = ApiState(Engine(ASSETS))
    api.exchange({"action": {"type": "deposit_guard", "x": 1}, "nonce": 0,
                  "signature": {"signer": "m"}})
    api.engine.deposit("m", 1_000 * S)
    r = api.exchange({"action": {"type": "convertToMultiSigUser",
                                 "authorized": ["a1", "a2", "a3"],
                                 "threshold": 2},
                      "nonce": 1, "signature": {"signer": "m"}})
    check(r["status"] == "ok", "ms-convert")
    # below threshold rejected
    r2 = api.exchange({"action": {"type": "multiSig", "target": "m",
                                  "signatures": ["a1"],
                                  "inner_action": {"type": "usdSend",
                                                   "destination": "x",
                                                   "amount": 10 * S}},
                       "nonce": 2, "signature": {"signer": "a1"}})
    check(r2["status"] == "err", "ms-below")
    # 2-of-3 passes, leader must be authorized
    r3 = api.exchange({"action": {"type": "multiSig", "target": "m",
                                  "signatures": ["a1", "a2"],
                                  "inner_action": {"type": "usdSend",
                                                   "destination": "x",
                                                   "amount": 10 * S}},
                       "nonce": 3, "signature": {"signer": "a2"}})
    check(r3["status"] == "ok", "ms-pass")
    check(api.engine.ch.acc("x").usd == 10 * S, "ms-executed")
    end()


def test_points_airdrop():
    sp = SpotStore()
    idx, _ = sp.deploy("AIRD", 8, 2, 1_000_000 * S, "sys", {})
    minted = sp.distribute_by_points(idx, 1_000_000 * S,
                                     {"u1": 3, "u2": 1})
    check(minted == 1_000_000 * S, "pa-minted")
    check(sp.bal("u1", idx) == 750_000 * S, "pa-u1")
    check(sp.bal("u2", idx) == 250_000 * S, "pa-u2")
    end()


def test_gas():
    ev = EvmSide()
    r = ev.corewriter("u1", 1, b"x", 0)
    check(r == "no_gas", "g-no-gas")
    ev.native["u1"] = 100_000_000
    r = ev.corewriter("u1", 1, b"x", 0)
    check(r is None, "g-ok")
    check(ev.native["u1"] == 100_000_000 - 25_000 * 1_000, "g-charged")
    end()


test_chain()
test_fee_tiers_referrals()
test_delist()
test_multisig()
test_points_airdrop()
test_gas()
end()
