# Created: WIB 2026-10-08 14:1x — CLOB, price-time priority (my-hl)
from collections import deque
from .types import Order

STATUS_RESTED = "rested"
STATUS_FILLED = "filled"
STATUS_CANCELED = "canceled"


class OrderBook:
    __slots__ = ("coin", "bids", "asks", "orders", "last_trade")

    def __init__(self, coin):
        self.coin = coin
        self.bids = {}
        self.asks = {}
        self.orders = {}
        self.last_trade = None

    def best_bid(self):
        return max(self.bids) if self.bids else None

    def best_ask(self):
        return min(self.asks) if self.asks else None

    def mid(self):
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb + ba) // 2

    def _opp(self, is_buy):
        return self.asks if is_buy else self.bids

    def _own(self, is_buy):
        return self.bids if is_buy else self.asks

    def _sweep(self, taker, fills):
        """Match taker against resting orders. Returns status string.
        STP v1: sweep stops if the best maker is the same user (taker may
        partially fill or rest; documented simplification in specs/05)."""
        crossed = self._crossed(taker)
        if taker.tif == "ALO":
            return STATUS_CANCELED if crossed else STATUS_RESTED
        while taker.sz_rem > 0:
            opp = self._opp(taker.is_buy)
            if not opp:
                break
            px = min(opp) if taker.is_buy else max(opp)
            level = opp[px]
            maker = level[0]
            if maker.user == taker.user:
                break  # STP v1
            if not self._crossed(taker, px):
                break
            fill_sz = min(taker.sz_rem, maker.sz_rem)
            self.last_trade = maker.px
            fills.append({
                "maker_oid": maker.oid, "maker_user": maker.user,
                "px": maker.px, "sz": fill_sz,
            })
            taker.sz_rem -= fill_sz
            maker.sz_rem -= fill_sz
            if maker.sz_rem == 0:
                level.popleft()
                del self.orders[maker.oid]
                if not level:
                    del opp[px]
        if taker.sz_rem == 0:
            return STATUS_FILLED
        return STATUS_CANCELED if taker.tif == "IOC" else STATUS_RESTED

    def _crossed(self, taker, px=None):
        if px is None:
            px = self.best_ask() if taker.is_buy else self.best_bid()
        if px is None:
            return False
        return px <= taker.px if taker.is_buy else px >= taker.px

    def place(self, order):
        """Returns (status, fills). Handles ALO cross-cancel and IOC remainder."""
        fills = []
        if order.tif == "ALO" and self._crossed(order):
            return STATUS_CANCELED, fills
        status = self._sweep(order, fills)
        if status == STATUS_RESTED:
            self._rest(order)
        return status, fills

    def _rest(self, order):
        side = self._own(order.is_buy)
        if order.px not in side:
            side[order.px] = deque()
        side[order.px].append(order)
        self.orders[order.oid] = order

    def cancel(self, oid):
        o = self.orders.pop(oid, None)
        if o is None:
            return False
        side = self._own(o.is_buy)
        level = side.get(o.px)
        if level is not None:
            try:
                level.remove(o)
            except ValueError:
                pass
            if not level:
                del side[o.px]
        return True

    def side_sz(self, is_buy):
        side = self._own(is_buy)
        return sum(o.sz_rem for level in side.values() for o in level)




