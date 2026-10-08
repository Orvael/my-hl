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


class TriggerStore:
    def __init__(self):
        self.triggers = []   # active + pending (parent_oid != None = pending)
        self.twaps = []

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
