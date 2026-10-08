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
            if sz > close_sz:
                pos.szi = inc + side0 * close_sz
                pos.entry_px = px
            else:
                pos.szi = szi0 + inc
        if pos.szi == 0:
            del acc.positions[coin]
        acc.usd -= fee
        return realized

    def upnl(self, acc, marks):
        total = 0
        for coin, pos in acc.positions.items():
            if pos.szi == 0:
                continue
            m = marks.get(coin)
            if m is None:
                continue
            side = 1 if pos.szi > 0 else -1
            total += qdiv(side * (m - pos.entry_px) * abs(pos.szi), SCALE)
        return total

    def account_value(self, acc, marks):
        return acc.usd + self.upnl(acc, marks)

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

    def maintenance_req(self, acc, marks):
        tot = 0
        for coin, pos in acc.positions.items():
            if pos.szi == 0:
                continue
            m = marks.get(coin)
            if m is None:
                continue
            lev = self.assets[coin]["max_leverage"]
            rate = qdiv(SCALE, 2 * lev)
            tot += qdiv(notional(m, abs(pos.szi)) * rate, SCALE)
        return tot

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
                continue
            acc = self.acc(user)
            delta = self.funding_pay(acc, coin, F8, oracle_px)
            acc.usd += delta
            total += delta
        return total

    def backstop_transfer(self, user):
        """specs/03: cross backstop = balance + all cross positions to vault_hlp.
        v1: positions transfer unpriced (docs silent on backstop fill pricing)."""
        a = self.acc(user)
        v = self.acc(VAULT_HLP)
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

