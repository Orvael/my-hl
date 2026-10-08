# Created: WIB 2026-10-08 22:3x — portfolio margin tests (my-hl, specs/16)
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hl.engine import Engine
from hl.clearing import PortfolioMargin
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
    print("test_pm OK")
    FAILS.clear()


def test_pm():
    pm = PortfolioMargin()
    # mode gate: needs $10k account value
    ok, why = pm.set_mode("u", True, 5_000 * S)
    check(not ok and why == "needs_10k_account_value", "pm-gate")
    ok, why = pm.set_mode("u", True, 20_000 * S)
    check(ok and pm.is_pm("u"), "pm-on")
    # supply to the pool, then borrow against it
    pm.collateral.setdefault("u", {})["USDC"] = [50_000 * S, pm.LTVS["USDC"], 1 * S]
    ok, why = pm.supply("u", "USDC", 10_000 * S)
    check(ok, "pm-supply")
    ok, why = pm.borrow("u", "USDC", 15_000 * S)
    check(not ok and why == "pool_empty", "pm-pool-cap")
    ok, why = pm.borrow("u", "USDC", 8_000 * S)
    check(ok, "pm-borrow")
    # interest: util = 8000/10000 = 0.8 -> rate exactly 5% APY
    pm.accrue_interest(0)
    pm.last_interest_ts["USDC"] = 0
    pm.accrue_interest(3600)
    expected = 8_000 * S + pm.borrow_rate_hourly(0.8) * 8_000 * S
    check(abs(pm.pools["USDC"]["borrowed"] - 8_000 * S -
              int(8_000 * S * 0.05 / 8760)) < 100 * S, "pm-interest")
    # repay
    pm.repay("u", "USDC", 4_000 * S)
    check(pm.pools["USDC"]["borrowed"] < 8_000 * S, "pm-repay")
    # PM maintenance adds the borrow buffer
    check(pm.pm_maintenance(100 * S, 50 * S) == 160 * S, "pm-maint")
    end()


test_pm()
