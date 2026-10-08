# Created: WIB 2026-10-08 15:3x — trigger order tests (my-hl)
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hl.engine import Engine
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
    print("test_triggers OK")
    FAILS.clear()


def mk_engine():
    e = Engine({"BTC": {"max_leverage": 20, "sz_decimals": 2,
                        "impact_notional_usd": 6_000 * S}})
    e.set_oracle("BTC", 100 * S)
    for u in ("m", "t", "lp"):
        e.deposit(u, 10_000 * S)
    # resting book: m bids 100 (200 units), lp asks 110 (200 units)
    e.place("m", "BTC", True, 100 * S, 200 * S, "ALO")
    e.place("lp", "BTC", False, 110 * S, 200 * S, "ALO")
    return e


def test_stop_market():
    e = mk_engine()
    e.deposit("alice", 1_000 * S)
    # alice long 50 @110 (takes lp's ask)
    e.place("alice", "BTC", True, 110 * S, 50 * S, "IOC")
    r = e.place_trigger("alice", "BTC", True, "above", "sl", "market",
                        90 * S)
    # stop-buy trigger must be ABOVE mid (mid ~100-105): 90 below -> reject
    check(r == "bad_trigger", "st-bad-reject")
    # correct: stop-sell (close long) fires below
    r = e.place_trigger("alice", "BTC", False, "below", "sl", "market", 90 * S)
    check(r == "ok", "st-ok")
    # marks pass at 100: not fired (mark 100 > 90)
    ev = e.trigger_pass({"BTC": 100 * S})
    check(not any(x["t"] == "trigger_fire" for x in ev), "st-not-yet")
    ev = e.trigger_pass({"BTC": 89 * S})
    check(any(x["t"] == "trigger_fire" and x["fills"] == 1 for x in ev), "st-fired")
    a = e.ch.acc("alice")
    check(not a.positions, "st-closed")
    check(490 * S < a.usd < 500 * S, "st-pnl-range")  # closed at 100 bid: -500 less fees
    end()


def test_tp_oco_parent_fill():
    e = mk_engine()
    e.deposit("alice", 1_000 * S)
    st, fills = e.place("alice", "BTC", True, 110 * S, 50 * S, "GTC",
                        tp={"kind": "tp", "trigger_px": 120 * S})
    check(st == "filled", "oco-filled")
    check(len(e.trig.triggers) == 1, "oco-child-active")
    c = e.trig.triggers[0]
    check(c.is_buy is False and c.fire == "above", "oco-child-side")
    ev = e.trigger_pass({"BTC": 121 * S})
    check(any(x["t"] == "trigger_fire" for x in ev), "oco-fired")
    check(e.ch.acc("alice").positions["BTC"].szi == 50 * S, "oco-pos-kept")
    end()


def test_oco_parent_cancel_cancels_children():
    e = mk_engine()
    e.deposit("alice", 1_000 * S)
    st, fills = e.place("alice", "BTC", True, 110 * S, 50 * S, "GTC",
                        tp={"kind": "tp", "trigger_px": 120 * S})
    # parent rested at 110? m bids 100 only; parent rests (GTC buy 110 crosses ask 110 -> fills!)
    # make parent rest: buy below ask
    e2 = mk_engine()
    e2.deposit("alice", 1_000 * S)
    st, _ = e2.place("alice", "BTC", True, 105 * S, 50 * S, "GTC",
                     tp={"kind": "tp", "trigger_px": 120 * S})
    check(st == "rested", "oc2-rested")
    check(e2.trig.triggers[0].parent_oid is not None, "oc2-pending")
    stc, _ = e2.place("bob", "BTC", True, 105 * S, 50 * S, "GTC")
    # bob's order rests behind alice (same px, FIFO) -> alice still resting
    e2.apply_block(2, [{"t": "cancel", "coin": "BTC", "oid": None}])
    # direct book cancel of alice's parent by oid: find it
    oids = [oid for oid in e2.books["BTC"].orders]
    target = [oid for oid in oids if e2.books["BTC"].orders[oid].user == "alice"]
    check(len(target) == 1, "oc2-found")
    e2.apply_block(2, [{"t": "cancel", "coin": "BTC", "oid": target[0]}])
    check(len(e2.trig.triggers) == 0, "oc2-children-gone")
    end()


def test_twap():
    e = mk_engine()
    e.deposit("alice", 5_000 * S)
    e.deposit("lp2", 5_000 * S)
    e.place("lp2", "BTC", False, 101 * S, 100 * S, "ALO")  # near-mark ask
    # buy 100 units over 600s, starting now
    e.apply_block(1000, [{"t": "twap", "user": "alice", "coin": "BTC",
                          "is_buy": True, "sz": 100 * S, "duration_s": 600}])
    # at block 1300 (300s elapsed): target = 50 units; base = 100*30/600 = 5
    marks = {"BTC": 100 * S}
    e.block_ts = 1300
    ev = e.twap_pass(marks)
    tw = [x for x in ev if x["t"] == "twap_done"]
    # filled via lp asks at 110: ask sz 200 -> plenty
    a = e.ch.acc("alice")
    check(a.positions["BTC"].szi == 50 * S, "tw-50-filled")
    end()


def test_market_tp_sl_slippage_bound():
    e = Engine({"BTC": {"max_leverage": 20, "sz_decimals": 2,
                        "impact_notional_usd": 6_000 * S}})
    e.set_oracle("BTC", 100 * S)
    for u in ("m", "alice", "lp", "lp2"):
        e.deposit(u, 5_000 * S)
    e.place("m", "BTC", True, 100 * S, 200 * S, "ALO")
    e.place("lp", "BTC", False, 110 * S, 50 * S, "ALO")   # 50-unit ask only
    e.place("alice", "BTC", True, 110 * S, 50 * S, "IOC")  # long @110, asks empty
    e.place("lp2", "BTC", True, 120 * S, 100 * S, "ALO")   # bid rests (no asks)
    e.place_trigger("alice", "BTC", False, "above", "tp", "market", 120 * S)
    # fire at 121: market limit = 121*1.10 = 133.1 -> sweeps lp 110 asks only
    ev = e.trigger_pass({"BTC": 121 * S})
    check(any(x["t"] == "trigger_fire" and x["fills"] >= 1 for x in ev), "slip-fired")
    a = e.ch.acc("alice")
    check(not a.positions, "slip-closed")
    check(a.usd > 1_000 * S, "slip-profit")
    end()
