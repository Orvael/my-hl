# Created: WIB 2026-10-08 15:1x — oracle aggregation + robust mark (my-hl)
from .num import qdiv

SRC_WEIGHTS = [("binance", 3), ("okx", 2), ("bybit", 2), ("kraken", 1),
               ("kucoin", 1), ("gate", 1), ("mexc", 1), ("hl_spot", 1)]
CEX_PERP_WEIGHTS = [("binance", 3), ("okx", 2), ("bybit", 2), ("gate", 1),
                    ("mexc", 1)]

HL_SPOT_EXCLUDED = "hl_spot"   # BTC-style: HL spot out, externals in
EXT_EXCLUDED = "external"      # HYPE-style: externals out, HL spot in


def weighted_median(items):
    """items = [(px:int, weight:int)] -> lower median at ties. Deterministic."""
    if not items:
        return None
    items = sorted(items)
    total = sum(w for _, w in items)
    acc = 0
    for px, w in items:
        acc += w
        if acc * 2 >= total:
            return px
    return items[-1][0]


def median3(*vals):
    xs = sorted(x for x in vals if x is not None)
    return xs[len(xs) // 2] if xs else None


class OracleEngine:
    """Per-validator weighted median of source prices; stake-weighted median
    across validators (specs/06)."""

    def __init__(self):
        self.validators = {}       # name -> stake
        self.sources = {}          # (coin, validator) -> {src: px}
        self.val_px = {}           # (coin, validator) -> px
        self.oracle = {}           # coin -> px

    def set_validator(self, name, stake):
        self.validators[name] = stake

    def on_src(self, coin, validator, src, px):
        k = (coin, validator)
        self.sources.setdefault(k, {})[src] = px
        items = []
        for src_name, w in SRC_WEIGHTS:
            if src_name in self.sources[k]:
                items.append((self.sources[k][src_name], w))
        px_med = weighted_median(items)
        self.val_px[k] = px_med
        self._refold(coin)

    def _refold(self, coin):
        items = [(p, self.validators.get(v, 0))
                 for (c, v), p in self.val_px.items() if c == coin]
        if items:
            self.oracle[coin] = weighted_median(items)

    def oracle_px(self, coin):
        return self.oracle.get(coin)


class MarkEngine:
    """specs/06: median of (oracle+EMA150(mid-oracle)), median(bid,ask,last),
    weighted median of external perp mids; 2-of-3 rule adds EMA30 of input 2."""

    def __init__(self):
        self.ema150 = {}
        self.ema30 = {}
        self.last_ts = {}
        self.marks = {}

    def update(self, coin, oracle_px, mid, bb, ba, last_trade, ext_mids, ts):
        """ext_mids = [(src, px)] with CEX_PERP_WEIGHTS. Returns mark px."""
        def ema_step(cur, sample, t, period):
            t = min(t, 10 * period)
            if cur is None:
                return sample
            return cur + qdiv((sample - cur) * t, period)

        t = ts - self.last_ts.get(coin, ts)
        t = t if t > 0 else 0
        i1 = None
        if oracle_px is not None and mid is not None:
            self.ema150[coin] = ema_step(self.ema150.get(coin),
                                         mid - oracle_px, t, 150)
            i1 = oracle_px + self.ema150[coin]
        book_prices = [p for p in (bb, ba, last_trade) if p is not None]
        i2 = sorted(book_prices)[len(book_prices) // 2] if book_prices else None
        i3 = None
        if ext_mids:
            d = dict(ext_mids)
            pairs = [(d[s], w) for s, w in CEX_PERP_WEIGHTS if s in d]
            i3 = weighted_median(pairs)
        inputs = [x for x in (i1, i2, i3) if x is not None]
        if len(inputs) == 2 and i2 is not None:
            self.ema30[coin] = ema_step(self.ema30.get(coin), i2, t, 30)
            inputs.append(self.ema30[coin])
        if not inputs:
            return None
        self.last_ts[coin] = ts
        inputs.sort()
        mark = inputs[len(inputs) // 2]
        self.marks[coin] = mark
        return mark
