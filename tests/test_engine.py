# Created: WIB 2026-10-08 14:5x — end-to-end engine tests (my-hl)
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hl.engine import Engine
from hl.journal import state_hash
from hl.config import SCALE

S = SCALE
FAILS = []


def check(cond, label):
    if not cond:
        FAILS.append(label)
    return cond


def mk_assets():
    return {"BTC": {"max_leverage": 20, "sz_decimals": 2,
                    "impact_notional_usd": 200_000 * S}}


def conservation(e, label, expected):
    tot = 0
    for u, a in e.ch.accounts.items():
        tot += a.usd
    check(tot == expected, "conservation:" + label)


def end():
    if FAILS:
        print("FAILS:", FAILS)
        sys.exit(1)
    print("test_engine OK")
    FAILS.clear()


def test_trade_flow():
    e = Engine(mk_assets())
    e.set_oracle("BTC", 100 * S)
    e.deposit("maker", 10_000 * S)
    e.deposit("taker", 10_000 * S)
    e.deposit("vault_fees", 0)
    st, fills = e.place("maker", "BTC", True, 100 * S, S, "ALO")
    check(st == "rested" and not fills, "tf-maker-rest")
    st, fills = e.place("taker", "BTC", False, 99 * S, S, "IOC")
    check(st == "filled" and len(fills) == 1, "tf-taker-fill")
    m = e.ch.acc("maker")
    t = e.ch.acc("taker")
    check(m.positions["BTC"].szi == S, "tf-maker-pos")
    check(t.positions["BTC"].szi == -S, "tf-taker-pos")
    notl = 100 * S  # USD 1e8: 1 unit at 100 USD
    check(m.usd == 10_000 * S - 1_500_000, "tf-maker-fee")
    check(t.usd == 10_000 * S - 4_500_000, "tf-taker-fee")
    check(e.ch.acc("vault_fees").usd == 6_000_000, "tf-fees-vault")
    # maker re-prices bid to 110 (ALO rests), taker sells again -> entry wavg 105
    st, fills = e.place("maker", "BTC", True, 110 * S, S, "ALO")
    check(st == "rested", "tf-maker-reprice")
    st, fills = e.place("taker", "BTC", False, 110 * S, S, "IOC")
    check(st == "filled" and len(fills) == 1, "tf-second-fill")
    m2 = e.ch.acc("maker")
    t2 = e.ch.acc("taker")
    check(m2.positions["BTC"].entry_px == 105 * S, "tf-wavg-entry")
    check(m2.positions["BTC"].szi == 2 * S, "tf-maker-sz")
    check(t2.positions["BTC"].entry_px == 105 * S, "tf-short-wavg")
    conservation(e, "trade_flow", 20_000 * S)
    # margin reject: 60 units at 5000 -> need 15000 > ~9990 available
    st, _ = e.place("taker", "BTC", True, 5_000 * S, 60 * S)
    check(st is None, "tf-margin-reject")
    end()


def _short_at(engine, user, px_usd, sz, bid_user):
    engine.place(bid_user, "BTC", True, px_usd * S, sz, "ALO")
    engine.place(user, "BTC", False, px_usd * S, sz, "IOC")


def test_liq_keeps_remainder():
    e = Engine(mk_assets())
    e.set_oracle("BTC", 100 * S)
    for u in ("alice", "carol"):
        e.deposit(u, 600 * S)
    e.deposit("vault_fees", 0)
    _short_at(e, "alice", 100, 100 * S, "carol")
    a = e.ch.acc("alice")
    check(a.positions["BTC"].szi == -100 * S, "lk-open")
    # top carol up so her free margin can back the extra 100-unit ask at 20x
    e.deposit("carol", 1_000 * S)
    # carol asks at 104 so the forced close fills; price up -> alice liquidatable
    stc, reason = e.place("carol", "BTC", False, 104 * S, 100 * S, "ALO")
    check(stc == "rested", "lk-ask-rest:%s" % (reason,))
    e.set_oracle("BTC", 104 * S)
    e.liq_pass(dict(e.oracles))
    # equity ~195.5 < maint 260 (and > 2/3 of it) -> forced buy @104, keeps remainder
    check(185 * S < a.usd < 205 * S, "lk-keeps-remainder")
    check(not a.positions, "lk-flat")
    conservation(e, "liq_keep", 2_200 * S)
    end()


def test_backstop():
    e = Engine(mk_assets())
    e.set_oracle("BTC", 100 * S)
    e.deposit("alice", 600 * S)
    e.deposit("carol", 600 * S)
    e.deposit("vault_fees", 0)
    e.deposit("vault_hlp", 0)
    _short_at(e, "alice", 100, 100 * S, "carol")
    # NO asks resting: forced close cannot fill -> equity < 2/3 maint -> backstop
    e.set_oracle("BTC", 105 * S)
    ev = e.liq_pass(dict(e.oracles))
    a = e.ch.acc("alice")
    v = e.ch.acc("vault_hlp")
    check(a.usd == 0 and not a.positions, "bs-zeroed")
    check(v.usd > 0, "bs-vault-usd")
    check(v.positions["BTC"].szi == -100 * S, "bs-vault-pos")
    check(any(x["t"] == "backstop" for x in ev), "bs-event")
    conservation(e, "backstop", 1_200 * S)
    end()


def test_adl():
    from hl.types import Position
    e = Engine(mk_assets())
    e.set_oracle("BTC", 105 * S)
    e.deposit("under", 100 * S)
    e.deposit("opp", 10 * S)
    # manufacture negative equity directly (cross backstop prevents it in v1)
    e.ch.acc("under").usd = -400 * S
    e.ch.acc("under").positions["BTC"] = Position("BTC", -50 * S, 100 * S)
    e.ch.acc("opp").positions["BTC"] = Position("BTC", 50 * S, 100 * S)
    ev = e.adl_pass("under", dict(e.oracles))
    u = e.ch.acc("under")
    o = e.ch.acc("opp")
    check(any(x["t"] == "adl" for x in ev), "adl-event")
    check(u.positions["BTC"].szi == 0, "adl-under-flat")
    check(o.positions["BTC"].szi == 0, "adl-opp-flat")
    check(u.usd + o.usd == -400 * S + 10 * S, "adl-zero-sum")
    end()


def _journal(tmp):
    import hl.journal as J
    p = os.path.join(tmp, "blocks.jsonl")
    fp = open(p, "w")
    J.append_block(fp, 0, [
        {"t": "oracle", "coin": "BTC", "px": 100 * S},
        {"t": "deposit", "user": "m", "usd": 5_000 * S},
        {"t": "deposit", "user": "t", "usd": 5_000 * S},
        {"t": "deposit", "user": "vault_fees", "usd": 0},
    ])
    J.append_block(fp, 60, [
        {"t": "place", "user": "m", "coin": "BTC", "is_buy": True,
         "px": 100 * S, "sz": 10 * S, "tif": "ALO"},
        {"t": "place", "user": "t", "coin": "BTC", "is_buy": False,
         "px": 100 * S, "sz": 10 * S, "tif": "IOC"},
    ])
    J.append_block(fp, 3601, [
        {"t": "oracle", "coin": "BTC", "px": 101 * S},
    ])
    fp.close()
    return p


def test_journal_determinism():
    import tempfile
    import hl.journal as J
    tmp = tempfile.mkdtemp()
    p = _journal(tmp)
    e1, h1 = J.replay(p)
    e2, h2 = J.replay(p)
    check(h1 == h2, "jr-same-hash")
    check(h1 == J.state_hash(e1), "jr-hash-of-engine")
    # funding fired exactly once at the hour boundary
    check(e1.last_funding_ts == 3600, "jr-funding-ts")
    check(e1.ch.acc("t").positions["BTC"].szi == -10 * S, "jr-pos")
    conservation(e1, "journal", 10_000 * S)
    end()


def test_funding_boundary_once():
    from hl.types import Position
    assets = {"BTC": {"max_leverage": 20, "sz_decimals": 2,
                      "impact_notional_usd": 5_000 * S}}
    e = Engine(assets)
    e.set_oracle("BTC", 100 * S)
    for u in ("m1", "m2", "L", "Sh"):
        e.deposit(u, 20_000 * S)
    # seed the book so impact prices exist: bid 99 / ask 101, deep enough for 5k
    e.place("m1", "BTC", True, 99 * S, 100 * S, "ALO")
    e.place("m2", "BTC", False, 101 * S, 100 * S, "ALO")
    # positions for L and Sh directly (margin formality irrelevant here)
    e.ch.acc("L").positions["BTC"] = Position("BTC", 10 * S, 100 * S)
    e.ch.acc("Sh").positions["BTC"] = Position("BTC", -10 * S, 100 * S)
    e.block_ts = 3600
    ev1 = e.settle_funding_if_due()
    check(len(ev1) == 1, "fb-fired")
    check(ev1[0]["f8"] == 10_000, "fb-rate-0.01pct")
    usd_L = e.ch.acc("L").usd
    e.block_ts = 3660
    ev2 = e.settle_funding_if_due()
    check(len(ev2) == 0, "fb-not-twice")
    e.block_ts = 7200
    ev3 = e.settle_funding_if_due()
    check(len(ev3) == 1, "fb-2nd-hour-fires")
    check(e.ch.acc("L").usd != usd_L, "fb-paid-2nd-hour")
    end()
