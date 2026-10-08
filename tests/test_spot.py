# Created: WIB 2026-10-08 16:0x — spot / HIP-1 / HIP-2 tests (my-hl)
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hl.spot import SpotStore, USDC_IDX, SPOT_TAKER, SPOT_MAKER
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
    print("test_spot OK")
    FAILS.clear()


def test_deploy_and_send():
    sp = SpotStore()
    idx, why = sp.deploy("PURR", 5, 0, 600_000_000 * 10**5, "dep1",
                         {"a": 100 * 10**5, "b": 50 * 10**5})
    check(idx == 1 and why == "ok", "sp-deploy")
    check(sp.bal("a", 1) == 100 * 10**5, "sp-genesis")
    check(sp.send("a", "b", 1, 30 * 10**5), "sp-send")
    check(sp.bal("a", 1) == 70 * 10**5 and sp.bal("b", 1) == 80 * 10**5, "sp-bals")
    idx2, why = sp.deploy("TOOLONGNAME", 8, 2, 100, "d", {})
    check(idx2 is None and why == "bad_name", "sp-bad-name")
    end()


def test_spot_trade_fees():
    sp = SpotStore()
    idx, _ = sp.deploy("PURR", 5, 0, 600_000_000 * 10**5, "dep1", {})
    sp._add("lp", idx, 1_000 * 10**5)   # lp has 1000 PURR
    sp._add("taker", USDC_IDX, 10_000 * 10**8)
    # lp asks 10 PURR @ 2 USDC/PURR: px = 2e8, sz = 10e5
    st, fills = sp.place_spot("lp", idx, False, 2 * S, 10 * 10**5, "ALO")
    check(st == "rested", "tr-lp-rest")
    st, fills = sp.place_spot("taker", idx, True, 2 * S, 10 * 10**5, "IOC")
    check(st == "filled" and len(fills) == 1, "tr-fill")
    check(sp.bal("taker", idx) == 10 * 10**5, "tr-taker-base")
    check(sp.bal("lp", USDC_IDX) == 20 * S - 800_000, "tr-lp-quote")  # 0.040% maker
    end()


def test_hl_quotes():
    sp = SpotStore()
    idx, _ = sp.deploy("PURR", 5, 0, 600_000_000 * 10**5, "dep1", {})
    huser = "hip2_%d" % idx
    sp._add(huser, idx, 100 * 10**5)          # 100 PURR
    sp._add(huser, USDC_IDX, 1_000 * S)       # 1000 USDC
    from hl.book import OrderBook
    sp.pairs[(idx, 0)] = {"book": OrderBook("1/USDC"), "hl_last_ts": 0}
    sp.add_hl((idx, 0), 1 * S, 4, 10 * 10**5, 2, 0)
    ok = sp.hl_pass((idx, 0), 100)
    check(ok, "hl-fired")
    book = sp.pairs[(idx, 0)]["book"]
    check(len(book.asks) >= 1, "hl-asks")
    check(len(book.bids) >= 1, "hl-bids")
    # taker buys 10 PURR from the HL ask at start*1.003
    sp._add("taker", USDC_IDX, 1_000 * S)
    ask_px = min(book.asks)
    st, fills = sp.place_spot("taker", idx, True, ask_px * 2, 10 * 10**5, "IOC")
    check(st == "filled", "hl-taker-fill")
    check(sp.bal("taker", idx) == 10 * 10**5, "hl-taker-got-base")
    end()
