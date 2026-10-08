# Created: WIB 2026-10-08 14:3x — clearinghouse (my-hl)
from .config import SCALE, TAKER_FEE, MAKER_FEE, INTEREST_8H, FUNDING_CLAMP, FUNDING_CAP
from .num import qdiv, notional, clamp, wavg
from .types import Account, Position

VAULT_FEES = "vault_fees"
VAULT_HLP = "vault_hlp"
VAULT_ADL = "vault_adl"
VAULTS = (VAULT_FEES, VAULT_HLP, VAULT_ADL)


class ClearingHouse:
    def __init__(self, assets):
        self.assets = assets  # coin -> AssetCfg-like dict
        self.accounts = {}

    def acc(self, user):
        if user not in self.accounts:
            self.accounts[user] = Account(user)
        return self.accounts[user]

    def fee_of(self, is_taker, sz_px_notional):
        rate = TAKER_FEE if is_taker else MAKER_FEE
        return qdiv(sz_px_notional * rate, SCALE)

    def apply_side(self, user, coin, is_buy, px, sz, fee):
        """Apply one fill to one account. Returns realized pnl (int, USD 1e8).
        Spec: opening = size-weighted entry; closing = entry frozen,
        realized = side*(px-entry)*sz; flip = close then open at px."""
        acc = self.acc(user)
        pos = acc.positions.get(coin)
        if pos is None:
            pos = Position(coin, 0, 0)
            acc.positions[coin] = pos
        inc = sz if is_buy else -sz
        szi0 = pos.szi
        if szi0 == 0 or (szi0 > 0) == (inc > 0):
            opening = True
        else:
            opening = False
        realized = 0
        if opening:
            if szi0 == 0:
                pos.entry_px = wavg(px, sz, px, sz)
            else:
                pos.entry_px = wavg(pos.entry_px, szi0 if szi0 > 0 else -szi0,
                                    px, sz)
            pos.szi = szi0 + inc
        else:
            side0 = 1 if szi0 > 0 else -1
            close_sz = min(-szi0 if szi0 < 0 else szi0, sz)
            realized = qdiv(side0 * (px - pos.entry_px) * close_sz, SCALE)
            acc.usd += realized
            if pos.is_isolated:
                ret = qdiv(pos.iso_margin * close_sz, -szi0 if szi0 < 0 else szi0)
                acc.usd += ret
                pos.iso_margin -= ret
            if sz > close_sz:
                pos.szi = inc + side0 * close_sz
                pos.entry_px = px
            else:
                pos.szi = szi0 + inc
        if pos.szi == 0:
            del acc.positions[coin]
        acc.usd -= fee
        return realized

    def total_notional(self, acc, marks):
        tot = 0
        for coin, pos in acc.positions.items():
            if pos.szi == 0:
                continue
            m = marks.get(coin)
            if m is None:
                continue
            tot += notional(m, pos.szi if pos.szi > 0 else -pos.szi)
        return tot

    def initial_req(self, acc, marks):
        tot = 0
        for coin, pos in acc.positions.items():
            if pos.szi == 0:
                continue
            m = marks.get(coin)
            if m is None:
                continue
            lev = self.assets[coin]["max_leverage"]
            tot += qdiv(notional(m, abs(pos.szi)), lev)
        return tot

    def tier_max_lev(self, coin, ntl):
        """specs/07: tier by notional position value; single-tier fallback."""
        cfg = self.assets[coin]
        tiers = cfg.get("margin_tiers")
        if not tiers:
            return cfg["max_leverage"]
        mx = tiers[0]["max_lev"]
        for t in tiers:
            if ntl >= t["lower_usd"]:
                mx = t["max_lev"]
            else:
                break
        return mx

    def maintenance_req_pos(self, coin, ntl):
        """specs/07: mm = sum over tier segments of covered_notional * rate_i.
        Equal by construction to notional*rate(top) - deduction recursion."""
        cfg = self.assets[coin]
        tiers = cfg.get("margin_tiers")
        if not tiers:
            rate = qdiv(SCALE, 2 * cfg["max_leverage"])
            return qdiv(ntl * rate, SCALE)
        tot = 0
        for i, t in enumerate(tiers):
            lower = t["lower_usd"]
            upper = tiers[i + 1]["lower_usd"] if i + 1 < len(tiers) else None
            covered = ntl - lower
            if upper is not None:
                covered = min(covered, upper - lower)
            if covered <= 0:
                break
            rate = qdiv(SCALE, 2 * t["max_lev"])
            tot += qdiv(covered * rate, SCALE)
        return tot

    def maintenance_req(self, acc, marks):
        tot = 0
        for coin, pos in acc.positions.items():
            if pos.szi == 0:
                continue
            m = marks.get(coin)
            if m is None:
                continue
            ntl = notional(m, abs(pos.szi))
            if pos.is_isolated:
                continue  # isolated liq is checked per position, not cross
            tot += self.maintenance_req_pos(coin, ntl)
        return tot

    def upnl_cross(self, acc, marks):
        total = 0
        for coin, pos in acc.positions.items():
            if pos.szi == 0 or pos.is_isolated:
                continue
            m = marks.get(coin)
            if m is None:
                continue
            side = 1 if pos.szi > 0 else -1
            total += qdiv(side * (m - pos.entry_px) * abs(pos.szi), SCALE)
        return total

    def account_value(self, acc, marks):
        """Cross account value: balance + cross-only pnl (isolated excluded)."""
        return acc.usd + self.upnl_cross(acc, marks)

    def iso_equity(self, acc, coin, marks):
        """specs/07: isolated liq inputs = iso bucket + that position's pnl only."""
        pos = acc.positions.get(coin)
        if pos is None or not pos.is_isolated:
            return None
        m = marks.get(coin)
        if m is None:
            return None
        side = 1 if pos.szi > 0 else -1
        pnl = qdiv(side * (m - pos.entry_px) * abs(pos.szi), SCALE)
        return pos.iso_margin + pnl

    def can_open(self, user, coin, px, sz, marks):
        acc = self.acc(user)
        lev = self.assets[coin]["max_leverage"]
        need = qdiv(notional(px, sz), lev)
        avail = self.account_value(acc, marks) - self.initial_req(acc, marks)
        return avail - need >= 0

    def can_withdraw(self, user, amt, marks):
        acc = self.acc(user)
        avail = self.account_value(acc, marks) - amt
        floor = max(self.initial_req(acc, marks), qdiv(self.total_notional(acc, marks), 10))
        return avail >= floor

    def funding_rate(self, impact_bid, impact_ask, oracle_px):
        """specs/01: F = P + clamp(interest - P, -0.0005, 0.0005), 1e8-scale ints.
        Returns 8h rate F; hourly payment uses F/8."""
        diff = max(impact_bid - oracle_px, 0) - max(oracle_px - impact_ask, 0)
        prem = qdiv(diff * SCALE, oracle_px)
        clamped = clamp(INTEREST_8H - prem, -FUNDING_CLAMP, FUNDING_CLAMP)
        F = prem + clamped
        return clamp(F, -FUNDING_CAP, FUNDING_CAP)

    def funding_pay(self, acc, coin, F8, oracle_px):
        """One account's hourly payment: -szi * oracle * F8/8 (long pays when F>0).
        Returns delta (signed USD 1e8). Zero-sum across accounts."""
        pos = acc.positions.get(coin)
        if pos is None or pos.szi == 0:
            return 0
        Fh = qdiv(F8, 8)
        return -qdiv(pos.szi * oracle_px * Fh, SCALE * SCALE)

    def funding_settle(self, coin, F8, oracle_px):
        """Apply hourly funding for one coin to all accounts. Returns total (must be 0)."""
        total = 0
        for user in sorted(self.accounts):
            if user.startswith("vault_"):
                continue
            acc = self.acc(user)
            delta = self.funding_pay(acc, coin, F8, oracle_px)
            acc.usd += delta
            total += delta
        return total

    def backstop_transfer(self, user, dex=None):
        """specs/03 + specs/10: cross backstop to the HLP vault; HIP-3 dex
        assets route to that dex's own backstop liquidator account."""
        a = self.acc(user)
        vault_name = "vault_hlp_dex_%s" % dex if dex else VAULT_HLP
        v = self.acc(vault_name)
        v.usd += a.usd
        for coin, pos in a.positions.items():
            if pos.szi == 0:
                continue
            vp = v.positions.get(coin)
            if vp is None or vp.szi == 0:
                v.positions[coin] = Position(coin, pos.szi, pos.entry_px)
            else:
                vp.entry_px = wavg(vp.entry_px, abs(vp.szi),
                                   pos.entry_px, abs(pos.szi))
                vp.szi += pos.szi
        a.usd = 0
        a.positions = {}

    def iso_backstop(self, user, coin):
        """specs/07: isolated backstop = that position + its bucket to vault."""
        a = self.acc(user)
        pos = a.positions.get(coin)
        if pos is None:
            return
        v = self.acc(VAULT_HLP)
        v.usd += pos.iso_margin
        vp = v.positions.get(coin)
        if vp is None or vp.szi == 0:
            v.positions[coin] = Position(coin, pos.szi, pos.entry_px)
        else:
            vp.entry_px = wavg(vp.entry_px, abs(vp.szi),
                               pos.entry_px, abs(pos.szi))
            vp.szi += pos.szi
        del a.positions[coin]

    def open_isolated(self, user, coin, amt):
        """Move amt from cross balance into the position's isolated bucket."""
        a = self.acc(user)
        pos = a.positions.get(coin)
        if pos is None:
            return False
        amt = min(amt, a.usd)
        if amt <= 0:
            return False
        pos.is_isolated = True
        pos.iso_margin += amt
        a.usd -= amt
        return True

    def adl_rank(self, acc, coin, mark):
        """specs/05 ADL index: (mark/entry) * (notional/account_value)."""
        pos = acc.positions.get(coin)
        if pos is None or pos.szi == 0:
            return 0
        eq = self.account_value(acc, {coin: mark})
        if eq <= 0:
            eq = 1
        n = notional(mark, abs(pos.szi))
        return qdiv(qdiv(mark * n, pos.entry_px), eq)

    def adl_close_pair(self, u_under, u_opp, coin, mark, close_sz):
        """Close close_sz against the underwater user at mark (no fees, v1).
        Zero-sum: underwater absorbs the loss, opposite books the gain."""
        pa = self.acc(u_under).positions[coin]
        pb = self.acc(u_opp).positions.get(coin)
        side_u = 1 if pa.szi > 0 else -1
        side_o = -side_u
        # underwater realizes its loss at mark
        ru = qdiv(side_u * (mark - pa.entry_px) * close_sz, SCALE)
        self.acc(u_under).usd += ru
        # opposite side closes at mark against underwater
        if pb is None:
            return False
        ro = qdiv(side_o * (mark - pb.entry_px) * close_sz, SCALE)
        self.acc(u_opp).usd += ro
        pa.szi -= side_u * close_sz
        pb.szi -= side_o * close_sz
        return True



class PortfolioMargin:
    """specs/16: portfolio margin mode (trading__portfolio-margin.md).
    Spot collateral valued with LTV (BTC 0.5, HYPE 0.65 per docs); borrow
    interest at 0.05 + 4.75*max(0, util-0.8) APY, indexed hourly; liquidation
    when the whole portfolio breaches its maintenance requirement."""

    LTVS = {"BTC": 500_000, "HYPE": 650_000, "USDC": 1_000_000}  # 1e6 scale

    def __init__(self):
        self.modes = {}          # user -> "portfolio"
        self.collateral = {}     # user -> {asset: [qty_1e8, ltv, mark]}
        self.borrows = {}        # user -> {asset: owed_1e8}
        self.pools = {}          # asset -> {"supplied": x, "borrowed": y}
        self.last_interest_ts = {}

    def set_mode(self, user, on, account_value_usd):
        """Docs gate: account value >$10k (or >$5M volume)."""
        if on and account_value_usd < 10_000 * SCALE:
            return False, "needs_10k_account_value"
        if on:
            self.modes[user] = "portfolio"
        else:
            self.modes.pop(user, None)
        return True, "ok"

    def is_pm(self, user):
        return self.modes.get(user) == "portfolio"

    def supply(self, user, asset, amount):
        """Provide borrowable liquidity to the pool, earns interest."""
        c = self.collateral.setdefault(user, {})
        c[asset] = c.get(asset, [0, self.LTVS.get(asset, 0), 0])
        moved = min(amount, c[asset][0])
        if moved <= 0:
            return False, "no_balance"
        c[asset][0] -= moved
        p = self.pools.setdefault(asset, {"supplied": 0, "borrowed": 0})
        p["supplied"] += moved
        return True, "ok"

    def borrow(self, user, asset, amount):
        p = self.pools.setdefault(asset, {"supplied": 0, "borrowed": 0})
        if p["supplied"] - p["borrowed"] < amount:
            return False, "pool_empty"
        self.borrows.setdefault(user, {})
        self.borrows[user][asset] = self.borrows[user].get(asset, 0) + amount
        p["borrowed"] += amount
        return True, "ok"

    def repay(self, user, asset, amount):
        owed = self.borrows.get(user, {}).get(asset, 0)
        pay = min(amount, owed)
        self.borrows[user][asset] = owed - pay
        p = self.pools.setdefault(asset, {"supplied": 0, "borrowed": 0})
        p["borrowed"] -= pay
        return True, "ok"

    def borrow_rate_hourly(self, util):
        """specs/16: 0.05 + 4.75*max(0, util-0.8) APY -> per-hour fraction."""
        rate_apy = 0.05 + 4.75 * max(0.0, util - 0.8)
        return rate_apy / 8760

    def accrue_interest(self, ts):
        """specs/16: hourly index at the docs' utilization rate formula."""
        for asset, p in self.pools.items():
            last = self.last_interest_ts.get(asset, ts)
            dt = max(ts - last, 0)
            self.last_interest_ts[asset] = ts
            if dt < 3600 or p["borrowed"] <= 0:
                continue
            util = p["borrowed"] / max(p["supplied"], 1)
            rate_apy = 0.05 + 4.75 * max(0.0, util - 0.8)
            hourly = qdiv(p["borrowed"] * int(rate_apy * 1e6), 1_000_000 * 8760)
            p["borrowed"] += hourly

    def pm_values(self, user, spot_val, borrowed_val):
        """specs/16: PM account value includes LTV-weighted spot collateral
        minus borrows."""
        c = self.collateral.get(user, {})
        ltv_val = 0
        for asset, (qty, ltv, mark) in c.items():
            ltv_val += qdiv(qty * mark // SCALE * ltv, SCALE)
        return spot_val - borrowed_val, ltv_val, borrowed_val

    def pm_maintenance(self, perp_maint, borrowed_val):
        """v1 substitution: borrowed value carries a 1.2x maintenance buffer
        (docs define a full portfolio margin ratio; simplified, documented)."""
        return perp_maint + qdiv(borrowed_val * 12, 10)
