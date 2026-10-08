# Created: WIB 2026-10-08 16:5x — HIP-4 outcome markets (my-hl)
from .num import qdiv
from .config import SCALE


def interpolate(mark0, t0, mark1, t1, t_settle):
    """Contract specs (verified): target price = linear interpolation between
    the mark updates immediately before and after the settlement timestamp."""
    if t1 == t0:
        return mark1
    return mark0 + qdiv((t_settle - t0) * (mark1 - mark0), t1 - t0)


class OutcomeMarket:
    """Fully collateralized binary / multi-price outcome (specs: HIP-4,
    recurring outcomes). Series key = (class, underlying, period) is unique."""

    def __init__(self, series_key, underlying, expiry_ts, targets):
        self.key = series_key          # ('priceBinary'|'priceBucket', u, period)
        self.underlying = underlying
        self.expiry_ts = expiry_ts
        self.targets = targets         # [t] for binary, [p1, p2] for bucket
        self.balances = {}             # user -> {outcome_idx: shares}
        self.collateral = {}           # user -> usd locked
        self.marks = []                # (ts, mark) history for interpolation

    def on_mark(self, ts, mark):
        self.marks.append((ts, mark))

    def buy(self, user, outcome_idx, shares, px_num, px_den):
        """Buy shares at price px_num/px_den (fully collateralized, 0..1)."""
        if not (0 <= px_num <= px_den) or shares <= 0:
            return False
        # shares are 1e8-scaled (1 share = 1 USDC at settle); cost in 1e8 USD
        cost = qdiv(shares * px_num, px_den)
        self.balances.setdefault(user, {})
        self.balances[user][outcome_idx] = self.balances[user].get(outcome_idx, 0) + shares
        self.collateral[user] = self.collateral.get(user, 0) + cost
        return True

    def settle_binary(self, t0, mark0, t1, mark1):
        """YES (1) iff interpolated target price >= target. Pays 1 or 0."""
        px = interpolate(mark0, t0, mark1, t1, self.expiry_ts)
        yes = 1 if px >= self.targets[0] else 0
        return self._payout({1: yes, 0: 1 - yes})

    def settle_bucket(self, t0, mark0, t1, mark1):
        """3 buckets: < p1, [p1, p2), >= p2. Exactly one settles to 1."""
        px = interpolate(mark0, t0, mark1, t1, self.expiry_ts)
        p1, p2 = self.targets
        if px < p1:
            win = 0
        elif px < p2:
            win = 1
        else:
            win = 2
        return self._payout({i: 1 if i == win else 0 for i in range(3)})

    def _payout(self, outcome_values):
        payouts = {}
        for user, bals in self.balances.items():
            total = 0
            for idx, sh in bals.items():
                total += sh * outcome_values.get(idx, 0)
            payouts[user] = total  # shares pay 1 USDC each when winning
        return payouts
