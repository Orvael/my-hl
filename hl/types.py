# Created: WIB 2026-10-08 14:0x — core types (my-hl, Hyperliquid imitation)
from .config import SCALE  # noqa: F401

TIFS = ("GTC", "IOC", "ALO")


class Order:
    __slots__ = ("oid", "user", "coin", "is_buy", "px", "sz", "sz_rem",
                 "tif", "reduce_only", "ts")

    def __init__(self, oid, user, coin, is_buy, px, sz, sz_rem,
                 tif, reduce_only=False, ts=0):
        self.oid = oid
        self.user = user
        self.coin = coin
        self.is_buy = is_buy
        self.px = px
        self.sz = sz
        self.sz_rem = sz_rem
        self.tif = tif
        self.reduce_only = reduce_only
        self.ts = ts


class Position:
    __slots__ = ("coin", "szi", "entry_px", "iso_margin", "is_isolated")

    def __init__(self, coin, szi=0, entry_px=0, iso_margin=0, is_isolated=False):
        self.coin = coin
        self.szi = szi
        self.entry_px = entry_px
        self.iso_margin = iso_margin
        self.is_isolated = is_isolated


class Account:
    __slots__ = ("user", "usd", "positions")
    def __init__(self, user):
        self.user = user
        self.usd = 0
        self.positions = {}
