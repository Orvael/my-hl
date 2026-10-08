# Created: WIB 2026-10-08 15:5x — spot layer: HIP-1 tokens, spot books, HIP-2 (my-hl)
from .num import qdiv
from .book import OrderBook, STATUS_RESTED
from .types import Order
from .config import SCALE

SPOT_TAKER = 70_000  # 0.070% (specs/09, fees.md spot tier-0)
SPOT_MAKER = 40_000  # 0.040%
USDC_IDX = 0


class Token:
    __slots__ = ("idx", "name", "wei_decimals", "sz_decimals", "max_supply",
                 "supply", "deployer", "fee_share_pct")

    def __init__(self, idx, name, wei_decimals, sz_decimals, max_supply,
                 deployer, fee_share_pct=100):
        self.idx = idx
        self.name = name
        self.wei_decimals = wei_decimals
        self.sz_decimals = sz_decimals
        self.max_supply = max_supply
        self.supply = max_supply
        self.deployer = deployer
        self.fee_share_pct = fee_share_pct


class SpotStore:
    """HIP-1 tokens, USDC-quoted spot books, deployer fee share (specs/09)."""

    def __init__(self):
        self.tokens = {USDC_IDX: Token(USDC_IDX, "USDC", 8, 8,
                                       2**64, "system")}
        self.balances = {}
        self.pairs = {}      # (base_idx, quote_idx) -> {"book": OrderBook, ...}
        self.next_token = 1
        self.next_oid_spot = 1
        self.burned = {}     # token_idx -> wei burned via fees

    def bal(self, user, idx):
        return self.balances.get(user, {}).get(idx, 0)

    def _add(self, user, idx, wei):
        self.balances.setdefault(user, {})
        self.balances[user][idx] = self.bal(user, idx) + wei

    def deploy(self, name, wei_decimals, sz_decimals, max_supply, deployer,
               genesis, fee_share_pct=100):
        if not name or len(name) > 6:
            return None, "bad_name"
        if sz_decimals + 5 > wei_decimals:
            return None, "bad_decimals"
        idx = self.next_token
        self.next_token += 1
        t = Token(idx, name, wei_decimals, sz_decimals, max_supply, deployer,
                  fee_share_pct)
        self.tokens[idx] = t
        for user, wei in genesis.items():
            self._add(user, idx, wei)
        return idx, "ok"

    def send(self, src, dst, idx, wei):
        if wei < 0 or self.bal(src, idx) < wei:
            return False
        self._add(src, idx, -wei)
        self._add(dst, idx, wei)
        return True

    def usd_class_transfer(self, user, wei, to_perp):
        """specs/09: atomic USDC between spot wallet and perp margin."""
        if to_perp:
            if not self.send(user, "usd_class", USDC_IDX, wei):
                return False
        else:
            if not self.send("usd_class", user, USDC_IDX, wei):
                return False
        return True

    def place_spot(self, user, base_idx, is_buy, px, sz, tif="GTC"):
        """USDC-quoted spot book (specs/09). px = quote-wei per base-wei,
        sz = base wei. Hold model: debit at placement, refund on cancel."""
        pair = (base_idx, USDC_IDX)
        if pair not in self.pairs:
            self.pairs[pair] = {"book": OrderBook("%d/USDC" % base_idx),
                                "hl_last_ts": 0}
        book = self.pairs[pair]["book"]
        lot = 10 ** (self.tokens[base_idx].wei_decimals
                     - self.tokens[base_idx].sz_decimals)
        if sz % lot != 0:
            return None, "bad_sz"
        # px is 1e8-scaled USDC per base unit; sz in wei.
        cost = qdiv(px * sz, 10 ** self.tokens[base_idx].wei_decimals)
        if is_buy:
            if self.bal(user, USDC_IDX) < cost:
                return None, "no_balance"
            self._add(user, USDC_IDX, -cost)
        else:
            if self.bal(user, base_idx) < sz:
                return None, "no_balance"
            self._add(user, base_idx, -sz)
        oid = self.next_oid_spot
        self.next_oid_spot += 1
        o = Order(oid, user, str(pair), is_buy, px, sz, sz, tif)
        status, fills = book.place(o)
        self._settle_spot_fills(user, base_idx, is_buy, fills)
        if is_buy and fills:
            wd = 10 ** self.tokens[base_idx].wei_decimals
            improve = sum(qdiv((px - f["px"]) * f["sz"], wd) for f in fills)
            self._add(user, USDC_IDX, improve)
        if status == "canceled":
            filled_sz = sum(f["sz"] for f in fills)
            self._refund(user, base_idx, is_buy, px, sz - filled_sz)
        return status, fills

    def cancel_spot(self, user, base_idx, oid):
        pair = (base_idx, USDC_IDX)
        book = self.pairs[pair]["book"]
        o = book.orders.get(oid)
        if o is None or o.user != user:
            return False
        px, sz = o.px, o.sz_rem
        if book.cancel(oid):
            self._refund(user, base_idx, o.is_buy, px, sz)
            return True
        return False

    def _refund(self, user, base_idx, is_buy, px, sz):
        if sz <= 0:
            return
        if is_buy:
            self._add(user, USDC_IDX,
                      qdiv(px * sz, 10 ** self.tokens[base_idx].wei_decimals))
        else:
            self._add(user, base_idx, sz)

    def _settle_spot_fills(self, taker, base_idx, is_buy, fills):
        """Taker's payment was pre-debited (hold model): on buy it receives
        base; on sell it receives quote minus fee. Maker is base seller on a
        buy, base buyer on a sell."""
        dep = self.tokens[base_idx].deployer
        share = self.tokens[base_idx].fee_share_pct
        for f in fills:
            q = qdiv(f["px"] * f["sz"], 10 ** self.tokens[base_idx].wei_decimals)
            fee_t = q * SPOT_TAKER // SCALE
            fee_m = q * SPOT_MAKER // SCALE
            if is_buy:
                self._add(taker, base_idx, f["sz"])
                self._add(f["maker_user"], USDC_IDX, q - fee_m)
            else:
                self._add(taker, USDC_IDX, q - fee_t)
                self._add(f["maker_user"], base_idx, f["sz"])
            self._split_fee(fee_t + fee_m, dep, share)

    def _split_fee(self, fee, dep, share_pct):
        to_dep = fee * share_pct // 100
        self._add(dep, USDC_IDX, to_dep)
        self._add("vault_af", USDC_IDX, fee - to_dep)

    def add_hl(self, pair_key, start_px, n_orders, order_sz, n_seeded, ts):
        """specs/09 HIP-2: strategy account 'hip2_<idx>' quotes the range."""
        self.pairs[pair_key]["hl"] = {"start_px": start_px, "n_orders": n_orders,
                                      "order_sz": order_sz,
                                      "n_seeded": n_seeded, "last_ts": ts}

    def hl_pass(self, pair_key, ts):
        """specs/09: every >=3s, re-quote: asks from base balance (nFull +
        partial), n_seeded bids on the 0.3% ladder. ALO only."""
        p = self.pairs[pair_key]
        if "hl" not in p:
            return False
        if ts - p["hl"]["last_ts"] < 3:
            return False
        p["hl"]["last_ts"] = ts
        book = p["book"]
        base_idx = pair_key[0]
        h = p["hl"]
        huser = "hip2_%d" % base_idx
        for oid in [oid for oid, o in book.orders.items() if o.user == huser]:
            self.cancel_spot(huser, base_idx, oid)
        sz = h["order_sz"]
        px = h["start_px"]
        base_bal = self.bal(huser, base_idx)
        n_full = base_bal // sz
        for i in range(h["n_orders"]):
            ask_px = px * (1003 ** (i + 1)) // (1000 ** (i + 1))
            if i < n_full:
                self.place_spot(huser, base_idx, False, ask_px, sz, "ALO")
            elif i == n_full and base_bal % sz > 0:
                self.place_spot(huser, base_idx, False, ask_px, base_bal % sz, "ALO")
        for j in range(h["n_seeded"]):
            bid_px = px * (1000 ** (j + 1)) // (1003 ** (j + 1))
            self.place_spot(huser, base_idx, True, bid_px, sz, "ALO")
        return True
