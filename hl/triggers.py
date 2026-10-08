# Created: WIB 2026-10-08 15:2x — trigger orders (my-hl)
from .num import qdiv
from .config import SCALE

# fire-side semantics: 'below' fires when mark <= trigger; 'above' when >=.
# TP/SL = take/stop for the close side of a position (specs/08).


class Trigger:
    __slots__ = ("user", "coin", "is_buy", "fire", "kind", "order_kind",
                 "trigger_px", "limit_px", "sz", "parent_oid")

    def __init__(self, user, coin, is_buy, fire, kind, order_kind,
                 trigger_px, limit_px=None, sz=None, parent_oid=None):
        self.user = user
        self.coin = coin
        self.is_buy = is_buy
        self.fire = fire  # 'below' | 'above'
        self.kind = kind  # 'tp' | 'sl' | 'stop' | 'take'
        self.order_kind = order_kind  # 'market' | 'limit'
        self.trigger_px = trigger_px
        self.limit_px = limit_px
        self.sz = sz  # None = whole position at fire time
        self.parent_oid = parent_oid


class Twap:
    __slots__ = ("user", "coin", "is_buy", "total_sz", "filled_sz",
                 "start_ts", "duration_s", "reduce_only")

    def __init__(self, user, coin, is_buy, total_sz, start_ts, duration_s,
                 reduce_only=False):
        self.user = user
        self.coin = coin
        self.is_buy = is_buy
        self.total_sz = total_sz
        self.filled_sz = 0
        self.start_ts = start_ts
        self.duration_s = duration_s
        self.reduce_only = reduce_only


class TrailingStop:
    """specs/08: market order when mark retraces from its best level since
    activation. Close-long (sell) trails the highest mark; close-short (buy)
    trails the lowest. Trigger never moves backward."""

    __slots__ = ("user", "coin", "is_buy", "distance", "is_pct", "best",
                 "reduce_only")

    def __init__(self, user, coin, is_buy, distance, is_pct=False,
                 reduce_only=True):
        self.user = user
        self.coin = coin
        self.is_buy = is_buy
        self.distance = distance
        self.is_pct = is_pct
        self.best = None    # highest mark (sell) or lowest mark (buy)
        self.reduce_only = reduce_only

    def on_mark(self, mark):
        """Returns trigger px when fired, else None."""
        if self.is_buy:
            if self.best is None or mark < self.best:
                self.best = mark
        else:
            if self.best is None or mark > self.best:
                self.best = mark
        if self.is_pct:
            thr = qdiv(self.best * (SCALE + self.distance if self.is_buy
                                    else SCALE - self.distance), SCALE)
        else:
            thr = self.best + self.distance if self.is_buy \
                else self.best - self.distance
        fired = mark >= thr if self.is_buy else mark <= thr
        return thr if fired else None


class TriggerStore:
    def __init__(self):
        self.triggers = []   # active + pending (parent_oid != None = pending)
        self.twaps = []
        self.trailing = []   # TrailingStop list

    def scale_orders(self, user, coin, is_buy, px_lo, px_hi, n, sz_each,
                     tif="ALO"):
        """specs/08 Scale: n limit orders across a price range."""
        out = []
        step = qdiv(px_hi - px_lo, n - 1) if n > 1 else 0
        for i in range(n):
            out.append((px_lo + i * step, sz_each))
        return out

    def add(self, t):
        self.triggers.append(t)

    def pending_children(self, parent_oid):
        return [t for t in self.triggers if t.parent_oid == parent_oid]

    def promote_children(self, parent_oid):
        for t in self.triggers:
            if t.parent_oid == parent_oid:
                t.parent_oid = None  # now active

    def cancel_children(self, parent_oid):
        self.triggers = [t for t in self.triggers if t.parent_oid != parent_oid]

    def remove(self, t):
        self.triggers.remove(t)
