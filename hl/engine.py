# Created: WIB 2026-10-08 14:4x — engine: deterministic state machine (my-hl)
from .config import SCALE, TAKER_FEE, MAKER_FEE
from .num import qdiv, notional
from .book import OrderBook, STATUS_FILLED, STATUS_RESTED, STATUS_CANCELED
from .clearing import ClearingHouse, VAULT_FEES, VAULT_HLP, VAULT_ADL
from .types import Order, TIFS

LIQ = "LIQ"  # pseudo-user for forced closes


class Engine:
    def __init__(self, assets, vault_names=(VAULT_FEES, VAULT_HLP, VAULT_ADL)):
        self.assets = assets
        self.books = {c: OrderBook(c) for c in assets}
        self.ch = ClearingHouse(assets)
        self.next_oid = 1
        self.block_ts = 0
        self.last_funding_ts = 0
        self.vault_names = vault_names
        self.oracles = {}

    def mark(self, coin):
        """v1 simplification: mark = oracle (robust index is v2)."""
        return self.oracles.get(coin)

    def execute_market(self, user, coin, is_buy, sz, px_bound, tif="IOC"):
        """Aggressive order -> book sweep -> clearing, fees on every fill."""
        oid = self.next_oid
        self.next_oid += 1
        o = Order(oid, user, coin, is_buy, px_bound, sz, sz, tif)
        status, fills = self.books[coin].place(o)
        self._settle_fills(user, coin, is_buy, fills)
        return status, fills

    def _settle_fills(self, user, coin, is_buy, fills):
        for f in fills:
            notl = f["px"] * f["sz"] // SCALE
            fee_t = qdiv(notl * TAKER_FEE, SCALE)
            fee_m = qdiv(notl * MAKER_FEE, SCALE)
            self.ch.acc(user).usd -= fee_t
            self.ch.acc(VAULT_FEES).usd += fee_t
            maker = f["maker_user"]
            self.ch.acc(maker).usd -= fee_m
            self.ch.acc(VAULT_FEES).usd += fee_m
            self.ch.apply_side(user, coin, is_buy, f["px"], f["sz"], 0)
            self.ch.apply_side(maker, coin, not is_buy, f["px"], f["sz"], 0)

    def px_valid(self, coin, px):
        """specs/05: <=5 sig figs AND <= (6 - szDecimals) decimals; integers always OK."""
        cfg = self.assets[coin]
        sd = cfg["sz_decimals"]
        unit = 10 ** (8 - (6 - sd))
        if px % unit != 0:
            return False
        frac = px % SCALE
        if frac == 0:
            return True  # integer price: sig-fig rule waived
        s = str(px).rstrip("0")
        return len(s) <= 5

    def sz_valid(self, coin, sz):
        sd = self.assets[coin]["sz_decimals"]
        return sz % (10 ** (8 - sd)) == 0

    def place(self, user, coin, is_buy, px, sz, tif="GTC", reduce_only=False):
        if coin not in self.assets:
            return None, "no_coin"
        if tif not in TIFS:
            return None, "bad_tif"
        if not self.px_valid(coin, px):
            return None, "bad_px"
        if not self.sz_valid(coin, sz):
            return None, "bad_sz"
        marks = dict(self.oracles)
        if reduce_only:
            pos = self.ch.acc(user).positions.get(coin)
            cap = abs(pos.szi) if pos else 0
            sz = min(sz, cap)
            if sz == 0:
                return None, "no_position"
        else:
            if not self.ch.can_open(user, coin, px, sz, marks):
                return None, "no_margin"
        oid = self.next_oid
        self.next_oid += 1
        o = Order(oid, user, coin, is_buy, px, sz, sz, tif, reduce_only)
        status, fills = self.books[coin].place(o)
        self._settle_fills(user, coin, is_buy, fills)
        return status, fills

    def deposit(self, user, usd):
        self.ch.acc(user).usd += usd

    def withdraw(self, user, usd, marks=None):
        marks = marks if marks is not None else dict(self.oracles)
        if not self.ch.can_withdraw(user, usd, marks):
            return False
        self.ch.acc(user).usd -= usd
        return True

    def set_oracle(self, coin, px):
        self.oracles[coin] = px

    def impact_bid(self, coin, oracle_px):
        """specs/01: avg execution px to trade impact_notional on the bid side."""
        cfg = self.assets[coin]
        need = cfg["impact_notional_usd"]
        filled = 0
        acc_px_sz = 0
        for px in sorted(self.books[coin].bids, reverse=True):
            for o in self.books[coin].bids[px]:
                take = min(o.sz_rem, need - filled)
                acc_px_sz += px * take
                filled += take
                if filled >= need:
                    break
            if filled >= need:
                break
        if filled == 0:
            return None
        return qdiv(acc_px_sz, filled)

    def impact_ask(self, coin, oracle_px):
        cfg = self.assets[coin]
        need = cfg["impact_notional_usd"]
        filled = 0
        acc_px_sz = 0
        for px in sorted(self.books[coin].asks):
            for o in self.books[coin].asks[px]:
                take = min(o.sz_rem, need - filled)
                acc_px_sz += px * take
                filled += take
                if filled >= need:
                    break
            if filled >= need:
                break
        if filled == 0:
            return None
        return qdiv(acc_px_sz, filled)

    def settle_funding_if_due(self):
        """Hourly boundary crossed since last settle -> compute F from our book
        and settle (v1: one sample per boundary, see specs/01 simplification)."""
        events = []
        if self.block_ts - self.last_funding_ts < 3600:
            return events
        hours = qdiv(self.block_ts - self.last_funding_ts, 3600)
        self.last_funding_ts += hours * 3600
        for coin in sorted(self.assets):
            oracle = self.oracles.get(coin)
            ib = self.impact_bid(coin, oracle)
            ia = self.impact_ask(coin, oracle)
            if oracle is None or ib is None or ia is None:
                continue
            F8 = self.ch.funding_rate(ib, ia, oracle)
            tot = self.ch.funding_settle(coin, F8, oracle)
            events.append({"t": "funding", "coin": coin, "f8": F8, "total": tot})
        return events

    def liq_pass(self, marks):
        """specs/03: book-first full-size closes; backstop below 2/3 maint."""
        events = []
        marks_live = dict(marks)
        for user in sorted(self.ch.accounts):
            if user.startswith("vault_") or user == LIQ:
                continue
            acc = self.ch.acc(user)
            if not acc.positions:
                continue
            for _ in range(50):
                eq = self.ch.account_value(acc, marks_live)
                mreq = self.ch.maintenance_req(acc, marks_live)
                if eq >= mreq or not acc.positions:
                    break
                if eq < qdiv(2 * mreq, 3):
                    self.ch.backstop_transfer(user)
                    events.append({"t": "backstop", "user": user})
                    break
                coin = max(acc.positions,
                           key=lambda c: notional(marks_live.get(c, 0), abs(acc.positions[c].szi)))
                pos = acc.positions[coin]
                if pos.szi == 0:
                    del acc.positions[coin]
                    continue
                is_buy = pos.szi < 0
                sz = abs(pos.szi)
                px_bound = SCALE * SCALE if is_buy else 1
                status, fills = self.execute_market(user, coin, is_buy, sz,
                                                    px_bound, "IOC")
                if not fills:
                    break
            eq_after = self.ch.account_value(acc, marks_live)
            mreq_after = self.ch.maintenance_req(acc, marks_live)
            if eq_after < 0:
                events.extend(self.adl_pass(user, marks_live))
        return events

    def adl_pass(self, u_under, marks):
        """specs/05: close profitable opposite-side users at mark against the
        underwater account until its value is >= 0 (no fees on ADL, v1)."""
        events = []
        acc = self.ch.acc(u_under)
        for coin in sorted(acc.positions):
            pos = acc.positions[coin]
            if pos.szi == 0:
                continue
            mark = marks.get(coin)
            if mark is None:
                continue
            side_u = 1 if pos.szi > 0 else -1
            opps = []
            for u2 in sorted(self.ch.accounts):
                if u2 == u_under or u2.startswith("vault_"):
                    continue
                p2 = self.ch.acc(u2).positions.get(coin)
                if p2 is None or p2.szi == 0:
                    continue
                if (p2.szi > 0) == (side_u > 0):
                    continue  # same side
                rank = self.ch.adl_rank(self.ch.acc(u2), coin, mark)
                opps.append((rank, u2))
            opps.sort(key=lambda x: (-x[0], x[1]))
            for _rank, u2 in opps:
                if self.ch.account_value(acc, marks) >= 0:
                    break
                p2 = self.ch.acc(u2).positions.get(coin)
                if p2 is None or p2.szi == 0:
                    continue
                close_sz = min(abs(acc.positions[coin].szi), abs(p2.szi))
                self.ch.adl_close_pair(u_under, u2, coin, mark, close_sz)
                events.append({"t": "adl", "under": u_under, "opp": u2,
                               "coin": coin, "sz": close_sz})
        return events

    def apply_block(self, ts, actions):
        """One deterministic block: user actions in order, then end-of-block
        passes (funding boundary -> liquidations -> ADL). Returns events."""
        self.block_ts = ts
        events = []
        for a in actions:
            t = a.get("t")
            if t == "deposit":
                self.deposit(a["user"], a["usd"])
            elif t == "withdraw":
                if not self.withdraw(a["user"], a["usd"]):
                    events.append({"t": "reject", "op": "withdraw", "user": a["user"]})
            elif t == "oracle":
                self.set_oracle(a["coin"], a["px"])
            elif t == "place":
                st, fills = self.place(a["user"], a["coin"], a["is_buy"],
                                       a["px"], a["sz"], a.get("tif", "GTC"),
                                       a.get("reduce_only", False))
                if fills:
                    events.extend(dict(f, t="fill", taker=a["user"]) for f in fills)
                elif st == "canceled":
                    events.append({"t": "cancel", "oid": a.get("oid"), "user": a["user"]})
            elif t == "cancel":
                self.books[a["coin"]].cancel(a["oid"])
            else:
                events.append({"t": "reject", "op": str(t)})
        events.extend(self.settle_funding_if_due())
        marks = dict(self.oracles)
        events.extend(self.liq_pass(marks))
        return events