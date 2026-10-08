# Created: WIB 2026-10-08 17:0x — vault, outcomes, trailing, HIP-1 extras, staking, sampling
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hl.vaults import HlpVault, LOCKUP_S
from hl.outcomes import OutcomeMarket, interpolate
from hl.triggers import TrailingStop, TriggerStore
from hl.spot import SpotStore, USDC_IDX
from hl.staking import Staking
from hl.hip3 import Hip3Store, DEPLOY_STAKE
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
    print("test_v2_layers OK")
    FAILS.clear()


def test_vault():
    v = HlpVault()
    marks = {"BTC": 100 * S}
    check(v.deposit("a", 1_000 * S, 0, marks), "v-dep1")
    check(v.total_shares == 1_000 * S, "v-shares1")
    check(v.deposit("b", 500 * S, 0, marks), "v-dep2")
    check(v.total_shares == 1_500 * S, "v-shares2")
    # vault trades profitably: long 10 @100 booked at 105 -> +50 realized
    v.book_fill("BTC", True, 100 * S, 10 * S, 0)
    v.book_fill("BTC", False, 105 * S, 10 * S, 0)
    eq = v.equity(marks)
    check(eq == 1_500 * S + 50 * S, "v-equity")
    # b redeems half of his 500 shares after lockup: share px = 1550/1500
    check(not v.can_withdraw("b", 100), "v-locked")
    got = v.withdraw("b", 1, 2, LOCKUP_S + 1, marks)
    from hl.num import qdiv
    sp = qdiv(1550 * S * S, 1500 * S)          # share price, truncated once
    exp = qdiv(250 * S * sp, S)                # then again in the payout
    check(got == exp, "v-withdraw")
    check(v.total_shares == 1_250 * S, "v-burn")
    # backstop absorption flows into equity
    v.absorb_backstop(100 * S, "BTC", -20 * S, 100 * S)
    check(v.cash == 1_550 * S - got + 100 * S, "v-backstop-cash")
    end()


def test_outcomes():
    m = OutcomeMarket(("priceBinary", "BTC", "1d"), "BTC", 3600, [100 * S])
    m.on_mark(3500, 99 * S)
    m.on_mark(3700, 101 * S)
    # settle time 3600 sits between marks: interpolated = 99 + (100/200)*2 = 100
    px = interpolate(99 * S, 3500, 101 * S, 3700, 3600)
    check(px == 100 * S, "o-interp")
    payouts = m.settle_binary(3500, 99 * S, 3700, 101 * S)
    m2 = OutcomeMarket(("priceBinary", "BTC", "1d"), "BTC", 3600, [100 * S + 1])
    m2.buy("u", 0, 100 * S, 60, 100)  # 100 NO shares (idx 0) at 0.60 -> $60
    pay = m2.settle_binary(3500, 99 * S, 3700, 101 * S)
    # interpolated 100 < target 100+1 -> YES=0 -> NO pays 1
    check(pay.get("u", 0) == 100 * S, "o-no-wins")
    m3 = OutcomeMarket(("priceBucket", "BTC", "15m"), "BTC", 3600,
                       [99 * S, 101 * S])
    m3.buy("u", 1, 50 * S, 50, 100)  # middle bucket [p1, p2)
    pay3 = m3.settle_bucket(3500, 99 * S, 3700, 101 * S)
    check(pay3.get("u", 0) == 50 * S, "o-bucket-mid")
    end()


def test_trailing():
    ts = TrailingStop("u", "BTC", False, 2 * S)  # close long: trail highest
    check(ts.on_mark(100 * S) is None, "tr-1")
    check(ts.on_mark(105 * S) is None, "tr-2")
    fired = ts.on_mark(102 * S + 9)
    check(fired is not None and fired == 103 * S, "tr-fired")  # retrace >= 2
    tb = TrailingStop("u", "BTC", True, 2 * S)  # close short: trail lowest
    check(tb.on_mark(100 * S) is None, "tr-3")
    check(tb.on_mark(97 * S) is None, "tr-4")
    fired2 = tb.on_mark(99 * S + 5)
    check(fired2 is not None and fired2 == 99 * S, "tr-fired2")
    # never moves backward
    check(ts.best == 105 * S, "tr-best")
    end()


def test_scale():
    ts = TriggerStore()
    orders = ts.scale_orders("u", "BTC", True, 99 * S, 103 * S, 5, S)
    check(len(orders) == 5, "sc-n")
    check(orders[0][0] == 99 * S and orders[-1][0] == 103 * S, "sc-range")
    check(all(s == S for _, s in orders), "sc-sz")
    end()


def test_hip1_extras():
    sp = SpotStore()
    idx, _ = sp.deploy("ANCH", 8, 2, 1_000_000 * S, "sys", {})
    sp._add("big", idx, 400_000 * S)
    sp._add("small", idx, 5 * 10**7)  # below the 1e-6-of-max-supply floor
    idx2, _ = sp.deploy("NEW", 8, 2, 500_000 * S, "sys", {})
    minted = sp.genesis_from_anchor(idx2, idx, 500_000 * S)
    check(minted > 0, "h1-minted")
    check(sp.bal("big", idx2) > 0, "h1-anchor-big")
    # small holder is below the 1e-6 floor -> excluded
    check(sp.bal("small", idx2) == 0, "h1-anchor-small-excluded")
    # auction decays 2x-last to 500 over 31h
    sp.start_spot_auction(0, last_px=1_000 * S)
    check(sp.spot_auction_px(0) == 2_000 * S, "h1-auction-start")
    check(sp.spot_auction_px(31 * 3600) == 500 * S, "h1-auction-end")
    end()


def test_dust():
    sp = SpotStore()
    idx, _ = sp.deploy("PURR", 5, 0, 600_000_000 * 10**5, "dep1", {})
    sp._add("lp", idx, 1_000 * 10**5)
    sp._add("lp", USDC_IDX, 100 * S)
    st, _ = sp.place_spot("lp", idx, True, 1 * S, 10 * 10**5, "ALO")  # bid
    # two dust holders: 0.5 PURR each (below 1 lot = 1 PURR), ~$0.5 each
    sp._add("d1", idx, 5 * 10**4)
    sp._add("d2", idx, 5 * 10**4)
    r = sp.dust_pass(idx, 1 * S)
    check(r is not None and r["users"] == 2, "du-users")
    check(sp.bal("d1", idx) == 0 and sp.bal("d2", idx) == 0, "du-swept")
    check(sp.bal("d1", USDC_IDX) > 0, "du-usdc-back")
    end()


def test_reserves():
    h = Hip3Store()
    h.stake("d", DEPLOY_STAKE)
    h.deploy_dex("d", "dx", 0)
    check(h.reserves_x10() == 70, "rv-7")
    h.start_auction("X", 0)
    ok, px = h.use_reserve("d", "dx", "X", 10)
    check(ok and px > 500 * S, "rv-used")
    check(h.reserves_x10() == 72, "rv-grows")
    end()


def test_staking():
    st = Staking()
    check(not st.register("v1", 5_000 * S, 3), "stk-min")
    check(st.register("v1", 10_000 * S, 3), "stk-reg")
    st.delegate("u", "v1", 90_000 * S)
    check(st.total_stake("v1") == 100_000 * S, "stk-total")
    st.accrue(365 * 24 * 3600)
    # 2.2% APR on 100k = 2200; commission 3% = 66; u gets 93% of rest
    check(st.rewards["v1"] == 66 * S, "stk-comm")
    exp = (2200 * S - 66 * S) * 90_000 // 100_000
    check(abs(st.rewards["u"] - exp) < 10 * S, "stk-deleg")
    st.jail("v1")
    before = st.rewards["u"]
    st.accrue(2 * 365 * 24 * 3600)
    check(st.rewards["u"] == before, "stk-jailed-no-rewards")
    end()


def test_premium_sampling():
    e = Engine({"BTC": {"max_leverage": 20, "sz_decimals": 2,
                        "impact_notional_usd": 5_000 * S}})
    e.set_oracle("BTC", 100 * S)
    e.deposit("m1", 50_000 * S)
    e.deposit("m2", 50_000 * S)
    e.place("m1", "BTC", True, 99 * S, 100 * S, "ALO")
    e.place("m2", "BTC", False, 101 * S, 100 * S, "ALO")
    from hl.types import Position
    e.ch.acc("L").positions["BTC"] = Position("BTC", 10 * S, 100 * S)
    e.ch.acc("Sh").positions["BTC"] = Position("BTC", -10 * S, 100 * S)
    e.deposit("L", 10_000 * S)
    e.deposit("Sh", 10_000 * S)
    e.block_ts = 100
    e.sample_premiums()
    e.block_ts = 105
    e.sample_premiums()
    check(len(e.premium_samples.get("BTC", [])) == 2, "ps-sampled")
    # with bid 99 / ask 101 around oracle 100: premium 0 -> F = interest 0.01%
    e.block_ts = 3700
    ev = e.settle_funding_if_due()
    check(len(ev) == 1 and ev[0]["f8"] == 10_000, "ps-rate")
    # the settle sampled at 3700 -> that sample belongs to the NEXT hour
    check(len(e.premium_samples.get("BTC", [])) == 1, "ps-carryover")
    end()




test_vault()
test_outcomes()
test_trailing()
test_scale()
test_hip1_extras()
test_dust()
test_reserves()
test_staking()
test_premium_sampling()
end()
