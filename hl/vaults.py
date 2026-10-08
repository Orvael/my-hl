# Created: WIB 2026-10-08 16:5x — HLP-style vault (my-hl, ref section 7)
from .num import qdiv
from .config import SCALE

LOCKUP_S = 4 * 24 * 3600  # 4-day lock after latest deposit


class HlpVault:
    """Depositor shares accrue the vault's real-time pnl (MM + backstop).
    Equity = cash + positions pnl; share price = equity / total_shares."""

    def __init__(self, name="hlp"):
        self.name = name
        self.cash = 0
        self.positions = {}    # coin -> [szi, entry_px] (netted, like users)
        self.shares = {}       # user -> share count
        self.total_shares = 0
        self.last_deposit_ts = {}  # user -> latest deposit ts

    def equity(self, marks):
        eq = self.cash
        for coin, (szi, entry) in self.positions.items():
            if szi == 0:
                continue
            m = marks.get(coin)
            if m is None:
                continue
            side = 1 if szi > 0 else -1
            eq += qdiv(side * (m - entry) * abs(szi), SCALE)
        return eq

    def share_px(self, marks):
        if self.total_shares == 0:
            return SCALE  # 1.00
        return qdiv(self.equity(marks) * SCALE, self.total_shares)

    def deposit(self, user, usd, ts, marks):
        if usd <= 0:
            return False
        sp = self.share_px(marks)
        mint = qdiv(usd * SCALE, sp) if self.total_shares else usd
        self.shares[user] = self.shares.get(user, 0) + mint
        self.total_shares += mint
        self.cash += usd
        self.last_deposit_ts[user] = ts
        return True

    def can_withdraw(self, user, ts):
        return ts - self.last_deposit_ts.get(user, 0) >= LOCKUP_S

    def withdraw(self, user, frac_num, frac_den, ts, marks):
        """Redeem a fraction of the user's shares (after the lockup)."""
        if not self.can_withdraw(user, ts):
            return None
        sh = self.shares.get(user, 0)
        burn = qdiv(sh * frac_num, frac_den)
        if burn <= 0 or burn > sh:
            return None
        sp = self.share_px(marks)
        usd = qdiv(burn * sp, SCALE)
        self.shares[user] = sh - burn
        self.total_shares -= burn
        self.cash -= usd
        return usd

    def book_fill(self, coin, is_buy, px, sz, fee):
        """Vault as counterparty: update netted position + cash for a fee."""
        self.cash -= fee
        szi, entry = self.positions.get(coin, [0, 0])
        inc = sz if is_buy else -sz
        if szi == 0 or (szi > 0) == (inc > 0):
            if szi == 0:
                entry = px
            else:
                tot = abs(szi) + sz
                entry = qdiv(entry * abs(szi) + px * sz, tot)
            szi += inc
        else:
            side = 1 if szi > 0 else -1
            close_sz = min(abs(szi), sz)
            self.cash += qdiv(side * (px - entry) * close_sz, SCALE)
            if sz > close_sz:
                szi = inc + side * close_sz
                entry = px
            else:
                szi += inc
        self.positions[coin] = [szi, entry]

    def absorb_backstop(self, usd, coin, szi, entry_px):
        """Backstop takeover: position + margin move in at their values."""
        self.cash += usd
        cur = self.positions.get(coin, [0, 0])
        if cur[0] == 0:
            self.positions[coin] = [szi, entry_px]
        else:
            tot = abs(cur[0]) + abs(szi)
            cur[1] = qdiv(cur[1] * abs(cur[0]) + entry_px * abs(szi), tot)
            cur[0] += szi
            self.positions[coin] = cur
