# Created: WIB 2026-10-08 14:4x — engine: deterministic state machine (my-hl)
from .config import (SCALE, TAKER_FEE, MAKER_FEE, INTEREST_8H,
                     FUNDING_CLAMP, FUNDING_CAP)
from .num import qdiv, notional, clamp
from .book import OrderBook, STATUS_FILLED, STATUS_RESTED, STATUS_CANCELED
from .clearing import ClearingHouse, VAULT_FEES, VAULT_HLP, VAULT_ADL
from .oracle import MarkEngine
from .triggers import TriggerStore, Trigger, Twap
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
        self.ext = {}
        self.mark_engine = MarkEngine()
        self.marks = {}
        self.trig = TriggerStore()
        self.partial_liq_threshold = 100_000 * SCALE
        self.last_partial_ts = {}
        self.premium_samples = {}
        self.premium_samples_ts = {}
        self.builder = None  # (builder_addr, fee_bps) set per API order
        self.volume = {}          # user -> cumulative traded USD (14d in prod)
        self.spot_volume = {}     # kept by the spot layer, 2x weight (fees.md)
        self.referrals = {}       # referred_user -> referrer
        self.referral_rewards = {}  # referrer -> usd accrued
        self.points = {}          # user -> points (airdrop distribution)
        self.delisted = set()

    def mark(self, coin):
        """specs/06 robust mark; falls back to oracle if never computed."""
        m = self.marks.get(coin)
        if m is not None:
            return m
        return self.oracles.get(coin)

    def update_marks(self):
        """specs/06: refresh robust marks from book + externals (end of block)."""
        for coin in sorted(self.assets):
            oracle = self.oracles.get(coin)
            if oracle is None:
                continue
            b = self.books[coin]
            ext = self.ext.get(coin)
            m = self.mark_engine.update(
                coin, oracle, b.mid(), b.best_bid(), b.best_ask(),
                b.last_trade, ext, self.block_ts)
            if m is not None:
                self.marks[coin] = m

    def execute_market(self, user, coin, is_buy, sz, px_bound, tif="IOC"):
        """Aggressive order -> book sweep -> clearing, fees on every fill."""
        oid = self.next_oid
        self.next_oid += 1
        o = Order(oid, user, coin, is_buy, px_bound, sz, sz, tif)
        status, fills = self.books[coin].place(o)
        self._settle_fills(user, coin, is_buy, fills)
        return status, fills

    FEE_TAKER_TIERS = [45_000, 40_000, 35_000, 30_000, 28_000, 26_000, 24_000]
    FEE_MAKER_TIERS = [15_000, 12_000, 8_000, 4_000, 0, 0, 0]
    TIER_BOUNDS = [5_000_000, 25_000_000, 100_000_000, 500_000_000,
                   2_000_000_000, 7_000_000_000]

    def fee_tier(self, user):
        """specs/13: 14d weighted volume = perps + 2x spot (fees.md)."""
        v = self.volume.get(user, 0) + 2 * self.spot_volume.get(user, 0)
        tier = 0
        for i, b in enumerate(self.TIER_BOUNDS):
            if v > b * SCALE:
                tier = i + 1
            else:
                break
        return tier

    def taker_fee_for(self, user):
        rate = self.FEE_TAKER_TIERS[self.fee_tier(user)]
        if user in self.referrals and self.volume.get(user, 0) < 25_000_000 * SCALE:
            rate = rate * 96 // 100  # 4% referral discount, first $25M
        return rate

    def refer(self, referrer, new_user):
        """specs/13: code requires $10k volume; referred gets 4% discount,
        referrer earns 10% of their fees."""
        if self.volume.get(referrer, 0) < 10_000 * SCALE:
            return False, "code_needs_10k_volume"
        self.referrals[new_user] = referrer
        return True, "ok"

    def delist(self, coin, settle_px):
        """specs/13: settle to the 1h time-weighted spot oracle (caller
        supplies the TWAP px), cancel all orders, no new orders accepted."""
        for oid in list(self.books[coin].orders):
            self.books[coin].cancel(oid)
        settled = 0
        for user in sorted(self.ch.accounts):
            pos = self.ch.acc(user).positions.get(coin)
            if pos is None or pos.szi == 0:
                continue
            side = 1 if pos.szi > 0 else -1
            realized = qdiv(side * (settle_px - pos.entry_px) * abs(pos.szi),
                            SCALE)
            self.ch.acc(user).usd += realized
            del self.ch.acc(user).positions[coin]
            settled += 1
        self.delisted.add(coin)
        return settled

    def _settle_fills(self, user, coin, is_buy, fills):
        for f in fills:
            notl = f["px"] * f["sz"] // SCALE
            self.volume[user] = self.volume.get(user, 0) + notl
            maker = f["maker_user"]
            self.volume[maker] = self.volume.get(maker, 0) + notl
            fee_t = qdiv(notl * self.taker_fee_for(user), SCALE)
            fee_m = qdiv(notl * self.FEE_MAKER_TIERS[
                self.fee_tier(maker)], SCALE)
            if self.builder:
                fee_b = qdiv(notl * self.builder[1], 10_000)
                fee_t += fee_b
                self.ch.acc(self.builder[0]).usd += fee_b
            self.ch.acc(user).usd -= fee_t
            self.ch.acc(VAULT_FEES).usd += fee_t
            self.ch.acc(maker).usd -= fee_m
            self.ch.acc(VAULT_FEES).usd += fee_m
            if user in self.referrals and fee_t:
                ref = self.referrals[user]
                share = qdiv(fee_t, 10)
                self.referral_rewards[ref] = self.referral_rewards.get(ref, 0) \
                    + share
                self.ch.acc(ref).usd += share
                self.ch.acc(VAULT_FEES).usd -= share
            self.ch.apply_side(user, coin, is_buy, f["px"], f["sz"], 0)
            self.ch.apply_side(maker, coin, not is_buy, f["px"], f["sz"], 0)
            if f["maker_oid"] not in self.books[coin].orders:
                self.trig.promote_children(f["maker_oid"])

    def order_value_cap(self, coin, tif):
        """specs/07 caps: $30M max_lev>=25; $5M [20,25); $2M [10,20); else $500k.
        Limit orders (GTC/ALO) get 10x the market cap."""
        mx = self.assets[coin]["max_leverage"]
        if mx >= 25:
            cap = 30_000_000 * SCALE
        elif mx >= 20:
            cap = 5_000_000 * SCALE
        elif mx >= 10:
            cap = 2_000_000 * SCALE
        else:
            cap = 500_000 * SCALE
        if tif in ("GTC", "ALO"):
            cap *= 10
        return cap

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

    def place(self, user, coin, is_buy, px, sz, tif="GTC", reduce_only=False,
              tp=None, sl=None):
        """tp/sl: OCO child specs {kind, trigger_px, limit_px?} (specs/08)."""
        if coin not in self.assets:
            return None, "no_coin"
        if coin in self.delisted:
            return None, "delisted"
        if tif not in TIFS:
            return None, "bad_tif"
        if not self.px_valid(coin, px):
            return None, "bad_px"
        if not self.sz_valid(coin, sz):
            return None, "bad_sz"
        ntl_cap = self.order_value_cap(coin, tif)
        if notional(px, sz) > ntl_cap:
            return None, "too_large"
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
        if tp or sl:
            if status == "filled":
                self._place_children(user, coin, is_buy, sz, tp, sl)
            elif status == "rested":
                self._register_pending(user, coin, is_buy, sz, oid, tp, sl)
        return status, fills

    def _child_fire(self, close_is_buy, kind):
        if kind == "tp":
            return "below" if close_is_buy else "above"
        return "above" if close_is_buy else "below"

    def _child_trigger(self, user, coin, close_is_buy, kind, spec, sz, parent_oid):
        fire = self._child_fire(close_is_buy, kind)
        return Trigger(user, coin, close_is_buy, fire, kind,
                       spec.get("order_kind", "market"),
                       spec["trigger_px"], spec.get("limit_px"), sz,
                       parent_oid=parent_oid)

    def _register_pending(self, user, coin, is_buy, sz, oid, tp, sl):
        for spec, kind in ((tp, "tp"), (sl, "sl")):
            if spec:
                self.trig.add(self._child_trigger(user, coin, not is_buy,
                                                  kind, spec, sz, oid))

    def _place_children(self, user, coin, is_buy, sz, tp, sl):
        self._register_pending(user, coin, is_buy, sz, None, tp, sl)

    def trigger_pass(self, marks):
        """specs/08: fire triggers on mark, deterministic (registration order)."""
        events = []
        for t in list(self.trig.triggers):
            if t.parent_oid is not None:
                continue  # pending children wait for their parent fill
            mark = marks.get(t.coin)
            if mark is None:
                continue
            hit = (mark <= t.trigger_px) if t.fire == "below" else (mark >= t.trigger_px)
            if not hit:
                continue
            pos = self.ch.acc(t.user).positions.get(t.coin)
            sz = t.sz if t.sz is not None else (abs(pos.szi) if pos else 0)
            self.trig.remove(t)
            if sz == 0:
                events.append({"t": "trigger_skip", "user": t.user, "kind": t.kind})
                continue
            if t.order_kind == "market":
                lim = qdiv(t.trigger_px * (110 if t.is_buy else 90), 100)
            else:
                lim = t.limit_px or t.trigger_px
            st, fills = self.place(t.user, t.coin, t.is_buy, lim, sz, "IOC",
                                   True)
            events.append({"t": "trigger_fire", "kind": t.kind, "user": t.user,
                           "fills": len(fills)})
        return events

    def twap_pass(self, marks):
        """specs/08: target = elapsed/total * size; catch-up suborders <= 3x base;
        per-suborder px bound = mark +/- 3%."""
        events = []
        for tw in list(self.trig.twaps):
            elapsed = self.block_ts - tw.start_ts
            if elapsed <= 0:
                continue
            target = qdiv(elapsed * tw.total_sz, tw.duration_s)
            base = qdiv(tw.total_sz * 30, tw.duration_s)
            if base == 0:
                base = tw.total_sz
            guard = 0
            while tw.filled_sz < target and guard < 50:
                guard += 1
                need = target - tw.filled_sz
                sz = min(3 * base, need)
                mark = marks.get(tw.coin)
                if mark is None:
                    break
                lim = qdiv(mark * (103 if tw.is_buy else 97), 100)
                st, fills = self.place(tw.user, tw.coin, tw.is_buy, lim, sz,
                                       "IOC", tw.reduce_only)
                got = sum(f["sz"] for f in fills)
                tw.filled_sz += got
                if got == 0:
                    break
            if tw.filled_sz >= tw.total_sz or elapsed >= tw.duration_s:
                self.trig.twaps.remove(tw)
                events.append({"t": "twap_done", "user": tw.user,
                               "filled": tw.filled_sz})
        return events

    def place_trigger(self, user, coin, is_buy, fire, kind, order_kind,
                      trigger_px, limit_px=None, sz=None, parent_oid=None):
        """specs/08: stop-sell fires 'below' (trigger < mid at placement),
        take-sell 'above' (trigger > mid); mirrored for buys."""
        mid = self.books[coin].mid()
        if mid is None:
            mid = self.oracles.get(coin)
        if mid is None:
            return "no_mid"
        if mid is None:
            return "no_mid"
        if fire == "below" and trigger_px >= mid:
            return "bad_trigger"
        if fire == "above" and trigger_px <= mid:
            return "bad_trigger"
        self.trig.add(Trigger(user, coin, is_buy, fire, kind, order_kind,
                              trigger_px, limit_px, sz, parent_oid))
        return "ok"

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

    def sample_premiums(self):
        """specs/01 (upgrade): premium sampled every 5s of block time,
        averaged over the hour for the settle."""
        for coin in sorted(self.assets):
            oracle = self.oracles.get(coin)
            if oracle is None:
                continue
            last = self.premium_samples_ts.get(coin, 0)
            if self.block_ts - last < 5:
                continue
            self.premium_samples_ts[coin] = self.block_ts
            ib = self.impact_bid(coin, oracle)
            ia = self.impact_ask(coin, oracle)
            if ib is None or ia is None:
                continue
            diff = max(ib - oracle, 0) - max(oracle - ia, 0)
            prem = qdiv(diff * SCALE, oracle)
            self.premium_samples.setdefault(coin, []).append(
                (self.block_ts, prem))

    def settle_funding_if_due(self):
        """specs/01: F = avg(premium samples over the interval)
        + clamp(interest - avg, +/-0.0005); 4%/h cap; paid at F/8."""
        events = []
        self.sample_premiums()
        if self.block_ts - self.last_funding_ts < 3600:
            return events
        hours = qdiv(self.block_ts - self.last_funding_ts, 3600)
        self.last_funding_ts += hours * 3600
        for coin in sorted(self.assets):
            oracle = self.oracles.get(coin)
            if oracle is None:
                continue
            samples = [p for (t, p) in self.premium_samples.get(coin, [])
                       if t <= self.last_funding_ts]
            if not samples:
                continue
            self.premium_samples[coin] = [
                (t, p) for (t, p) in self.premium_samples.get(coin, [])
                if t > self.last_funding_ts]
            prem_avg = qdiv(sum(samples), len(samples))
            clamped = clamp(INTEREST_8H - prem_avg, -FUNDING_CLAMP, FUNDING_CLAMP)
            F8 = clamp(prem_avg + clamped, -FUNDING_CAP, FUNDING_CAP)
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
            # isolated positions: own bucket, own notional (specs/07)
            for coin in sorted(acc.positions):
                pos = acc.positions[coin]
                if not pos.is_isolated or pos.szi == 0:
                    continue
                m = marks_live.get(coin)
                if m is None:
                    continue
                ntl = notional(m, abs(pos.szi))
                eq_iso = self.ch.iso_equity(acc, coin, marks_live)
                mreq_iso = self.ch.maintenance_req_pos(coin, ntl)
                if eq_iso is None or eq_iso >= mreq_iso:
                    continue
                if eq_iso < qdiv(2 * mreq_iso, 3):
                    self.ch.iso_backstop(user, coin)
                    events.append({"t": "backstop_iso", "user": user, "coin": coin})
                    continue
                is_buy = pos.szi < 0
                px_bound = SCALE * SCALE if is_buy else 1
                self.execute_market(user, coin, is_buy, abs(pos.szi), px_bound, "IOC")
            for _ in range(50):
                eq = self.ch.account_value(acc, marks_live)
                mreq = self.ch.maintenance_req(acc, marks_live)
                if eq >= mreq or not acc.positions:
                    break
                if eq < qdiv(2 * mreq, 3):
                    big = max((c for c, p in acc.positions.items()
                               if not p.is_isolated),
                              key=lambda c: notional(marks_live.get(c, 0),
                                                     abs(acc.positions[c].szi)),
                              default=None)
                    dex = big.split(":")[0] if big and ":" in big else None
                    self.ch.backstop_transfer(user, dex=dex)
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
                ntl = notional(marks_live.get(coin, 0), sz)
                in_cooldown = self.block_ts - self.last_partial_ts.get(user, 0) <= 30
                if ntl > self.partial_liq_threshold and not in_cooldown:
                    sz = qdiv(sz, 5)
                    self.last_partial_ts[user] = self.block_ts
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

    def run_passes(self, ts):
        """specs/14: the end-of-block passes standalone, so a live API engine
        mirrors apply_block's semantics exactly (marks, triggers, funding, liq)."""
        self.block_ts = ts
        self.update_marks()
        marks = {c: self.mark(c) for c in self.assets}
        events = []
        events.extend(self.twap_pass(marks))
        events.extend(self.trigger_pass(marks))
        events.extend(self.settle_funding_if_due())
        events.extend(self.liq_pass(marks))
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
            elif t == "points":
                self.points[a["user"]] = self.points.get(a["user"], 0) + a["pts"]
            elif t == "refer":
                ok, why = self.refer(a["referrer"], a["new_user"])
                if not ok:
                    events.append({"t": "reject", "op": "refer", "why": why})
            elif t == "ext_px":
                self.ext.setdefault(a["coin"], {})[a["src"]] = a["px"]
            elif t == "place_trigger":
                r = self.place_trigger(a["user"], a["coin"], a["is_buy"],
                                       a["fire"], a["kind"], a.get("order_kind", "market"),
                                       a["trigger_px"], a.get("limit_px"),
                                       a.get("sz"), a.get("parent_oid"))
                if r != "ok":
                    events.append({"t": "reject", "op": "place_trigger", "why": r})
            elif t == "twap":
                self.trig.twaps.append(Twap(a["user"], a["coin"], a["is_buy"],
                                            a["sz"], self.block_ts,
                                            a["duration_s"],
                                            a.get("reduce_only", False)))
            elif t == "place":
                st, fills = self.place(a["user"], a["coin"], a["is_buy"],
                                       a["px"], a["sz"], a.get("tif", "GTC"),
                                       a.get("reduce_only", False))
                if st is None:
                    events.append({"t": "reject", "op": "place", "why": fills})
                elif fills:
                    events.extend(dict(f, t="fill", taker=a["user"]) for f in fills)
                elif st == "canceled":
                    events.append({"t": "cancel", "oid": a.get("oid"), "user": a["user"]})
            elif t == "cancel":
                if self.books[a["coin"]].cancel(a["oid"]):
                    self.trig.cancel_children(a["oid"])
            else:
                events.append({"t": "reject", "op": str(t)})
        self.update_marks()
        marks = {c: self.mark(c) for c in self.assets}
        events.extend(self.twap_pass(marks))
        events.extend(self.trigger_pass(marks))
        events.extend(self.settle_funding_if_due())
        events.extend(self.liq_pass(marks))
        return events