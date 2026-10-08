# Created: WIB 2026-10-08 15:5x — tiers, isolated, partial liq, caps (my-hl)
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hl.engine import Engine
from hl.clearing import ClearingHouse
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
    print("test_margin2 OK")
    FAILS.clear()


TIERS = [{"lower_usd": 0, "max_lev": 40},
         {"lower_usd": 100_000 * S, "max_lev": 20}]


def test_caps():
    e = Engine({"BTC": {"max_leverage": 20, "sz_decimals": 2,
                        "impact_notional_usd": 6_000 * S}})
    e.deposit("a", 1_000_000 * S)
    st, why = e.place("a", "BTC", True, 100 * S, 60_000 * S, "IOC")
    check(st is None and why == "too_large", "cap-market")
    st, why = e.place("a", "BTC", True, 100 * S, 60_000 * S, "ALO")
    check(st == "rested", "cap-limit-ok")
    st, why = e.place("a", "BTC", True, 100 * S, 600_000 * S, "ALO")
    check(st is None and why == "too_large", "cap-limit-big")
    end()


def test_tiers():
    cfg = {"max_leverage": 20, "sz_decimals": 2, "impact_notional_usd": 6_000 * S,
           "margin_tiers": TIERS}
    ch = ClearingHouse({"BTC": cfg})
    mm = ch.maintenance_req_pos("BTC", 150_000 * S)
    check(mm == 2_500 * S, "tier-mm")
    check(ch.maintenance_req_pos("BTC", 50_000 * S) == 625 * S, "tier-mm-below")
    check(ch.tier_max_lev("BTC", 50_000 * S) == 40, "tier-maxlev")
    check(ch.tier_max_lev("BTC", 150_000 * S) == 20, "tier-maxlev2")
    end()


def _iso_setup(e):
    e.set_oracle("BTC", 100 * S)
    e.deposit("bob", 10_000 * S)
    e.deposit("carol", 10_000 * S)
    e.place("carol", "BTC", True, 95 * S, 100 * S, "ALO")
    e.place("bob", "BTC", False, 95 * S, 100 * S, "IOC")  # bob long 100 @95
    e.ch.open_isolated("bob", "BTC", 500 * S)


def test_isolated():
    e = Engine({"BTC": {"max_leverage": 20, "sz_decimals": 2,
                        "impact_notional_usd": 6_000 * S}})
    _iso_setup(e)
    b = e.ch.acc("bob")
    check(b.positions["BTC"].iso_margin == 500 * S, "iso-bucket")
    check(9_494 * S < b.usd < 9_496 * S, "iso-cross-debited")
    end()


def test_isolated_liq_keeps_cross():
    e = Engine({"BTC": {"max_leverage": 20, "sz_decimals": 2,
                        "impact_notional_usd": 6_000 * S}})
    _iso_setup(e)
    e.deposit("cp", 1_000 * S)
    e.place("cp", "BTC", False, 98 * S, 100 * S, "ALO")  # ask for forced close
    e.set_oracle("BTC", 98 * S)
    e.liq_pass(dict(e.oracles))
    b = e.ch.acc("bob")
    check(not b.positions, "isol-flat")
    # bucket 500 absorbed the 300 loss, 200 returned to cross: 9495.7 + 200
    check(9_600 * S < b.usd < 9_700 * S, "isol-cross-kept")
    check(e.ch.acc("vault_hlp").usd == 0, "isol-no-backstop")
    end()


def test_partial_liq():
    e = Engine({"BTC": {"max_leverage": 20, "sz_decimals": 2,
                        "impact_notional_usd": 6_000 * S}})
    e.set_oracle("BTC", 100 * S)
    e.deposit("alice", 10_000 * S)
    e.deposit("carol", 1_000_000 * S)
    e.place("carol", "BTC", True, 97 * S, 2_000 * S, "ALO")   # bid: alice's entry
    st, _ = e.place("alice", "BTC", False, 97 * S, 2_000 * S, "IOC")  # short @97
    check(st == "filled", "pl-open")
    # asks near the liq price for the forced BUY close (integer cents: 99.60 USD)
    ask_px = 9_960 * (S // 100)
    e.place("carol", "BTC", False, ask_px, 2_000 * S, "ALO")
    e.set_oracle("BTC", ask_px)
    e.liq_pass(dict(e.oracles))
    a = e.ch.acc("alice")
    check(not a.positions, "pl-flat")
    check(4_400 * S < a.usd < 4_800 * S, "pl-kept")
    end()
