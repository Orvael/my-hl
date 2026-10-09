# Created: WIB 2026-10-08 17:5x — API layer (my-hl, specs/12)
# Real surface shapes from the live info-endpoint/signing docs (fetched 10-08).
# Substitutions (documented): signature = stub envelope with nonce replay
# protection (real HL = EIP-712 eth keys); WS subscriptions = /events long-poll
# stream with the same payloads. The ENGINE stays untouched and deterministic;
# only the API layer uses wall time (rate limiting).
import json
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .num import qdiv
from .config import SCALE


def _ws_frame_bytes(op, payload):
    data = payload if isinstance(payload, bytes) else payload.encode()
    ln = len(data)
    if ln < 126:
        return bytes([op, ln]) + data
    if ln < 65536:
        return bytes([op, 126]) + ln.to_bytes(2, "big") + data
    return bytes([op, 127]) + ln.to_bytes(8, "big") + data

MAX_NONCES = 10_000


def _ws_frame(text):
    """Server->client text frame (unmasked, RFC6455)."""
    data = text.encode()
    ln = len(data)
    if ln < 126:
        return bytes([0x81, ln]) + data
    if ln < 65536:
        return bytes([0x81, 126]) + ln.to_bytes(2, "big") + data
    return bytes([0x81, 127]) + ln.to_bytes(8, "big") + data


def fmt_px(px):
    return str(qdiv(px, 10 ** 6) / 10 ** 2) if px is not None else None


class ApiState:
    """Server state around one Engine. All /info reads + /exchange writes."""

    def __init__(self, engine, spot=None, staking=None, vault=None,
                 vault_factory=None):
        self.engine = engine
        self.spot = spot
        self.staking = staking
        self.vault = vault
        self.vault_factory = vault_factory
        self.referrals_ui = {}   # user -> {code, referred: [], earned}
        self.lock = threading.RLock()
        self.used_nonces = set()
        self.api_wallets = {}       # api_addr -> master_addr
        self.builders = {}          # builder_addr -> earned usd
        self.subaccounts = {}       # master -> [sub names]
        self.events = []            # ring buffer (fills, funding, liq, ...)
        self.rate = {}              # user -> (window_ts, count)
        self.rate_limit = 100       # requests per window
        self.rate_window_s = 10
        self.multisig = {}          # user -> {authorized, threshold}
        self.action_log = []        # (ts, action) for block production
        self.faucet_given = set()

    # ---- /info ----

    def info(self, req):
        t = req.get("type")
        if t == "meta":
            uni = []
            for coin in sorted(self.engine.assets):
                cfg = self.engine.assets[coin]
                uni.append({"name": coin,
                            "szDecimals": cfg["sz_decimals"],
                            "maxLeverage": cfg["max_leverage"]})
            return {"universe": uni}
        if t == "allMids":
            out = {}
            for coin in sorted(self.engine.assets):
                b = self.engine.books[coin]
                px = b.mid()
                if px is None:
                    px = b.last_trade  # docs: last trade fallback
                if px is None:
                    px = self.engine.mark(coin)
                if px is not None:
                    out[coin] = fmt_px(px)
            return out
        if t == "assetCtxs":
            # per-market context: mark, oracle, live premium funding,
            # open interest (sum |szi|), recent notional volume
            out = {}
            for coin in sorted(self.engine.assets):
                b = self.engine.books[coin]
                mark = self.engine.mark(coin)
                oracle = self.engine.oracles.get(coin)
                px = b.mid() or b.last_trade or mark
                oi = 0
                for acc in self.engine.ch.accounts.values():
                    p = acc.positions.get(coin)
                    if p is not None and p.szi:
                        oi += abs(p.szi)
                vlm = 0
                fund = None
                for ev in self.events:
                    if ev.get("coin") != coin:
                        continue
                    if ev.get("t") == "fill":
                        vlm += ev["px"] * ev["sz"]
                    elif ev.get("t") == "funding" and fund is None:
                        fund = ev.get("f8")
                if fund is None and oracle and px:
                    fund = (px - oracle) * 8 * SCALE // oracle
                out[coin] = {
                    "markPx": fmt_px(mark),
                    "oraclePx": fmt_px(oracle),
                    "funding8h": fmt_px(fund or 0),
                    "openInterest": fmt_px(oi),
                    "dayNtlVlm": str(round(vlm / 1e16, 2)),
                }
            return out
        if t == "l2Book":
            coin = req.get("coin")
            b = self.engine.books.get(coin)
            if b is None:
                return None
            def lvl(side, rev):
                out = []
                for px in sorted(side, reverse=rev):
                    tot = sum(o.sz_rem for o in side[px])
                    out.append([fmt_px(px), str(qdiv(tot, 10 ** 6) / 10 ** 2),
                                len(side[px])])
                return out
            return {"coin": coin,
                    "levels": [lvl(b.bids, True), lvl(b.asks, False)]}
        if t == "userState":
            return self._user_state(req.get("user"))
        if t == "openOrders":
            return self._open_orders(req.get("user"))
        if t == "userFills":
            return self._user_fills(req.get("user"))
        if t == "leaderboard":
            rows = []
            for u, a in self.engine.ch.accounts.items():
                if u.startswith("vault_") or u == "lp_bot" or u == "sim_bot":
                    continue
                marks = {c: self.engine.mark(c) for c in self.engine.assets}
                av = self.engine.ch.account_value(a, marks)
                rows.append({"user": u,
                             "accountValue": fmt_px(av),
                             "volume": fmt_px(self.engine.volume.get(u, 0))})
            rows.sort(key=lambda r: -float(r["accountValue"]))
            return [{"rank": i + 1, **r} for i, r in enumerate(rows[:50])]
        if t == "staking":
            st = self.staking
            if st is None:
                return {"validators": []}
            out = []
            for name, v in st.validators.items():
                out.append({"validator": name,
                            "totalStake": fmt_px(st.total_stake(name)),
                            "commission": "%d%%" % v["commission_pct"],
                            "jailed": v["jailed"]})
            out.sort(key=lambda r: -float(r["totalStake"].replace(",", "")))
            u = req.get("user")
            return {"validators": out,
                    "delegations": st.delegations.get(u, {}),
                    "rewards": fmt_px(st.rewards.get(u, 0))}
        if t == "vault":
            v = self.vault
            if v is None:
                return None
            marks = {c: self.engine.mark(c) for c in self.engine.assets}
            u = req.get("user")
            return {"name": v.name, "equity": fmt_px(v.equity(marks)),
                    "sharePx": fmt_px(v.share_px(marks)),
                    "totalShares": fmt_px(v.total_shares),
                    "yourShares": fmt_px(v.shares.get(u, 0)),
                    "lockedUntil": v.last_deposit_ts.get(u, 0) + 4 * 86400}
        if t == "vaults":
            out = []
            vf = self.vault_factory
            marks = {c: self.engine.mark(c) for c in self.engine.assets}
            if self.vault is not None:
                out.append({"name": self.vault.name, "leader": "HLP",
                            "equity": fmt_px(self.vault.equity(marks)),
                            "sharePx": fmt_px(self.vault.share_px(marks)),
                            "leaderFraction": "0%"})
            if vf is not None:
                for name in sorted(vf.vaults):
                    v = vf.vaults[name]
                    eq = v.equity(marks)
                    shp = v.share_px(marks)
                    out.append({"name": name, "leader": vf.leaders[name],
                                "equity": fmt_px(eq), "sharePx": fmt_px(shp),
                                "leaderFraction": "0%"})
            return out
        if t == "portfolio":
            pm = self.engine.pm
            u = req.get("user")
            c = pm.collateral.get(u, {})
            b = pm.borrows.get(u, {})
            return {"mode": pm.modes.get(u, "standard"),
                    "collateral": {k: [v[0] / 1e8, v[2] / 1e8, v[1] / 1e6]
                                   for k, v in c.items()},
                    "borrows": {k: v / 1e8 for k, v in b.items()},
                    "pools": {k: [p["supplied"] / 1e8, p["borrowed"] / 1e8]
                              for k, p in pm.pools.items()}}
        if t == "referral":
            u = req.get("user")
            r = self.engine
            code = u[:8] if u else ""
            return {"code": code,
                    "referred": sorted(r.referrals.get(x, x)
                                       for x in r.referrals
                                       if r.referrals.get(x) == code),
                    "earned": fmt_px(r.referral_rewards.get(u, 0)),
                    "canRefer": r.volume.get(u, 0) >= 10_000 * 10 ** 8}
        if t == "spotMeta":
            if self.spot is None:
                return {"tokens": [], "pairs": []}
            toks = [{"idx": i, "name": tk.name,
                     "szDecimals": tk.sz_decimals}
                    for i, tk in sorted(self.spot.tokens.items())]
            pairs = [{"pair": "%s/USDC" % self.spot.tokens[k[0]].name,
                      "base": k[0]} for k in sorted(self.spot.pairs)]
            return {"tokens": toks, "pairs": pairs}
        if t == "spotBook":
            if self.spot is None:
                return None
            key = (int(req["base"]), 0)
            p = self.spot.pairs.get(key)
            if p is None:
                return None
            b = p["book"]

            def lv(side, rev):
                out = []
                for px in sorted(side, reverse=rev)[:10]:
                    tot = sum(o.sz_rem for o in side[px])
                    out.append([fmt_px(px),
                                tot / 10 ** self.spot.tokens[key[0]].wei_decimals])
                return out
            return {"bids": lv(b.bids, True), "asks": lv(b.asks, False)}
        if t == "spotBalances":
            if self.spot is None:
                return {}
            u = req.get("user")
            out = {}
            for idx, tk in self.spot.tokens.items():
                w = self.spot.bal(u, idx)
                if w:
                    out[tk.name] = w / 10 ** tk.wei_decimals
            return out
        return None

    def _user_state(self, user):
        e = self.engine
        marks = {c: e.mark(c) for c in e.assets}
        acc = e.ch.accounts.get(user)
        if acc is None:
            return {"marginSummary": {"accountValue": "0.0",
                                      "totalNtlPos": "0.0"},
                    "assetPositions": []}
        av = e.ch.account_value(acc, marks)
        ntl = e.ch.total_notional(acc, marks)
        withdrawable = max(av - e.ch.initial_req(acc, marks), 0)
        positions = []
        for coin in sorted(acc.positions):
            p = acc.positions[coin]
            if p.szi == 0:
                continue
            side = 1 if p.szi > 0 else -1
            m = marks.get(coin)
            upnl = qdiv(side * (m - p.entry_px) * abs(p.szi), SCALE) if m else 0
            positions.append({
                "position": {
                    "coin": coin, "szi": fmt_px(p.szi),
                    "entryPx": fmt_px(p.entry_px),
                    "leverage": {"leverage": p.lev or
                                 e.assets[coin]["max_leverage"],
                                 "type": "isolated" if p.is_isolated
                                 else "cross"},
                    "unrealizedPnl": fmt_px(upnl),
                    "isolatedMargin": fmt_px(p.iso_margin) if p.is_isolated
                    else None,
                },
                "type": "oneWay"})
        return {"marginSummary": {"accountValue": fmt_px(av),
                                  "totalNtlPos": fmt_px(ntl),
                                  "totalRawUsd": fmt_px(acc.usd)},
                "withdrawable": fmt_px(withdrawable),
                "assetPositions": positions}

    def _open_orders(self, user):
        out = []
        for coin in sorted(self.engine.books):
            b = self.engine.books[coin]
            for oid, o in sorted(b.orders.items()):
                if o.user != user:
                    continue
                out.append({"coin": coin, "oid": oid,
                            "limitPx": fmt_px(o.px), "sz": fmt_px(o.sz_rem),
                            "side": "B" if o.is_buy else "A",
                            "timestamp": o.ts})
        return out

    def _user_fills(self, user):
        out = []
        for ev in self.events:
            if ev.get("t") != "fill":
                continue
            for role in ("taker", "maker"):
                if ev.get(role) != user:
                    continue
                out.append({
                    "coin": ev["coin"], "px": fmt_px(ev["px"]),
                    "sz": fmt_px(ev["sz"]),
                    "side": "B" if ev["is_buy"] else "A",
                    "dir": ev.get("dir", "Open Long"),
                    "closedPnl": fmt_px(ev.get("closed_pnl", 0)),
                    "fee": fmt_px(ev.get(role + "_fee", 0)),
                    "tid": ev["tid"], "time": ev["ts"],
                    "hash": ev.get("hash", "0x0")})
        return out[-2000:]

    # ---- /exchange ----

    def record_event(self, ev):
        ev["tid"] = len(self.events) + 1
        self.events.append(ev)
        if len(self.events) > 5_000:
            del self.events[:1_000]

    def rate_check(self, user):
        import time
        now = int(time.time())
        win, cnt = self.rate.get(user, (0, 0))
        if now - win >= self.rate_window_s:
            self.rate[user] = (now, 1)
            return True
        if cnt >= self.rate_limit:
            return False
        self.rate[user] = (win, cnt + 1)
        return True

    def exchange(self, req):
        nonce = req.get("nonce")
        if nonce is None or nonce in self.used_nonces:
            return {"status": "err", "response": "nonce_replayed_or_missing"}
        signer = req.get("signature", {}).get("signer")
        if signer is None:
            return {"status": "err", "response": "no_signer"}
        user = self.api_wallets.get(signer, signer)  # API wallet -> master
        with self.lock:
            self.used_nonces.add(nonce)
            if len(self.used_nonces) > MAX_NONCES:
                self.used_nonces.clear()
            if not self.rate_check(user):
                return {"status": "err", "response": "rate_limit"}
            action = req.get("action", {})
            eng = []
            result = self._dispatch(user, action,
                                    leader=req.get("signature", {}).get("signer"),
                                    eng=eng)
            if eng:
                self.action_log.append((int(time.time()), eng))
            return result

    def _dispatch(self, user, action, leader=None, eng=None):
        try:
            return self._dispatch_inner(user, action, leader, eng)
        except Exception:
            return {"status": "err", "response": "bad_request"}

    def _dispatch_inner(self, user, action, leader=None, eng=None):
        e = self.engine
        t = action.get("type")
        if t == "order":
            builder = action.get("builder")
            results = []
            for o in action.get("orders", []):
                if int(o.get("limit_px", 0)) <= 0 or int(o.get("sz", 0)) <= 0:
                    return {"status": "err", "response": "px_sz_must_be_positive"}
                if eng is not None:
                    eng.append({"t": "place", "user": user, "coin": o["coin"],
                                "is_buy": o["is_buy"], "px": int(o["limit_px"]),
                                "sz": int(o["sz"]), "tif": o.get("tif", "GTC"),
                                "reduce_only": o.get("reduce_only", False)})
                if builder:
                    e.builder = (builder["b"], int(builder["f"]))
                else:
                    e.builder = None
                st, fills = e.place(user, o["coin"], o["is_buy"],
                                    int(o["limit_px"]), int(o["sz"]),
                                    o.get("tif", "GTC"),
                                    o.get("reduce_only", False))
                if st is None:
                    results.append({"status": "rejected",
                                    "error": fills if isinstance(fills, str) else "x"})
                    continue
                for f in fills:
                    self.record_event({"t": "fill", "coin": o["coin"],
                                       "px": f["px"], "sz": f["sz"],
                                       "is_buy": o["is_buy"],
                                       "taker": user, "maker": f["maker_user"],
                                       "ts": e.block_ts,
                                       "hash": "0x%d" % f["maker_oid"]})
                results.append({"status": st or "rejected",
                                "oid": e.next_oid - 1 if st else None})
            return {"status": "ok", "response": {"statuses": results}}
        if t == "cancel":
        # ownership: a user may only cancel their own resting order
            if action.get("coin") not in e.books:
                return {"status": "err", "response": "no_coin"}
            o = e.books.get(action["coin"], None) and \
                e.books[action["coin"]].orders.get(action["oid"])
            if o is not None and o.user != user:
                return {"status": "err", "response": "not_your_order"}
            ok = e.books[action["coin"]].cancel(action["oid"])
            if ok and eng is not None:
                eng.append({"t": "cancel", "coin": action["coin"],
                            "oid": action["oid"]})
            return {"status": "ok" if ok else "err",
                    "response": "canceled" if ok else "unknown_oid"}
        if t == "convertToMultiSigUser":
            if len(action.get("authorized", [])) > 10:
                return {"status": "err", "response": "max_10_authorized"}
            self.multisig[user] = {"authorized": action["authorized"],
                                   "threshold": action["threshold"]}
            return {"status": "ok", "response": "converted"}
        if t == "multiSig":
            ms = self.multisig.get(action.get("target", user))
            if ms is None:
                return {"status": "err", "response": "not_multisig"}
            leader = leader or user
            if leader not in ms["authorized"]:
                return {"status": "err", "response": "leader_not_authorized"}
            sigs = set(action.get("signatures", []))
            if len(sigs & set(ms["authorized"])) < ms["threshold"]:
                return {"status": "err", "response": "below_threshold"}
            return self._dispatch(action["target"], action.get("inner_action", {}),
                                  leader=leader)
        if t == "faucet":
            if eng is not None:
                eng.append({"t": "deposit", "user": user,
                            "usd": 10_000 * 10 ** 8})
            """Testnet money: once per account (specs/14)."""
            if not hasattr(self, "faucet_given"):
                self.faucet_given = set()
            if user in self.faucet_given:
                return {"status": "err", "response": "already_fauceted"}
            amt = 10_000 * 10 ** 8
            self.engine.deposit(user, amt)
            if self.spot is not None:
                self.spot._add(user, 0, 10_000 * 10 ** 8)
            self.faucet_given.add(user)
            return {"status": "ok", "response": "fauceted_10000_usdc"}
        if t == "delegate" and self.staking is not None:
            if int(action.get("amount", 0)) <= 0:
                return {"status": "err", "response": "amount_must_be_positive"}
            ok = self.staking.delegate(user, action["validator"],
                                       int(action["amount"]))
            return {"status": "ok" if ok else "err",
                    "response": "delegated" if ok else "no_validator"}
        if t == "createVault":
            vf = self.vault_factory
            if vf is None:
                return {"status": "err", "response": "no_factory"}
            amt = int(action.get("usd", 0))
            ok, why = vf.create(user, action["name"], usd=min(amt,
                                self.engine.ch.acc(user).usd),
                                ts=int(time.time()),
                                marks={c: self.engine.mark(c)
                                       for c in self.engine.assets})
            if ok and amt > 0:
                self.engine.ch.acc(user).usd -= min(amt,
                                                    self.engine.ch.acc(user).usd)
            return {"status": "ok" if ok else "err", "response": why}
        if t == "vaultDepositNamed":
            vf = self.vault_factory
            v = vf.get(action["name"]) if vf else None
            if v is None:
                return {"status": "err", "response": "no_vault"}
            marks = {c: self.engine.mark(c) for c in self.engine.assets}
            amt = int(action["amount"])
            if self.engine.ch.acc(user).usd < amt:
                return {"status": "err", "response": "insufficient"}
            self.engine.ch.acc(user).usd -= amt
            v.deposit(user, amt, int(time.time()), marks)
            return {"status": "ok", "response": "deposited"}
        if t == "vaultWithdrawNamed":
            vf = self.vault_factory
            v = vf.get(action["name"]) if vf else None
            if v is None:
                return {"status": "err", "response": "no_vault"}
            marks = {c: self.engine.mark(c) for c in self.engine.assets}
            got = v.withdraw(user, int(action.get("num", 1)),
                             int(action.get("den", 1)),
                             int(time.time()), marks)
            if got is None:
                return {"status": "err", "response": "locked_or_empty"}
            self.engine.ch.acc(user).usd += got
            return {"status": "ok", "response": fmt_px(got)}
        if t == "vaultDeposit" and self.vault is not None:
            marks = {c: self.engine.mark(c) for c in self.engine.assets}
            amt = int(action["amount"])
            acc = self.engine.ch.acc(user)
            if acc.usd < amt:
                return {"status": "err", "response": "insufficient"}
            ok = self.vault.deposit(user, amt, int(time.time()), marks)
            return {"status": "ok" if ok else "err", "response": "deposited"}
        if t == "vaultWithdraw" and self.vault is not None:
            marks = {c: self.engine.mark(c) for c in self.engine.assets}
            got = self.vault.withdraw(user, int(action.get("num", 1)),
                                      int(action.get("den", 1)),
                                      int(time.time()), marks)
            if got is None:
                return {"status": "err", "response": "locked_or_empty"}
            self.engine.ch.acc(user).usd += got
            return {"status": "ok", "response": fmt_px(got)}
        if t == "setReferral":
            ok, why = self.engine.refer(action["code_owner"], user)
            return {"status": "ok" if ok else "err", "response": why}
        if t == "spotOrder" and self.spot is not None:
            if int(action.get("px", 0)) <= 0 or int(action.get("sz", 0)) <= 0:
                return {"status": "err", "response": "px_sz_must_be_positive"}
            st, fills = self.spot.place_spot(
                user, int(action["base"]), bool(action["is_buy"]),
                int(action["px"]), int(action["sz"]),
                action.get("tif", "GTC"))
            if st is None:
                return {"status": "err", "response": str(fills)}
            for f in fills:
                self.record_event({"t": "spot_fill", "base": action["base"],
                                   "px": f["px"], "sz": f["sz"],
                                   "is_buy": bool(action["is_buy"]),
                                   "taker": user, "maker": f["maker_user"],
                                   "ts": self.engine.block_ts})
            return {"status": "ok", "response": {"status": st}}
        if t == "faucetSpot" and self.spot is not None:
            if user in self.faucet_given:
                return {"status": "err", "response": "already_fauceted"}
            self.spot._add(user, 0, 10_000 * 10 ** 8)
            self.faucet_given.add(user)
            return {"status": "ok", "response": "spot_usdc_10000"}
        if t == "pmMode":
            if eng is not None:
                eng.append({"t": "pmMode", "user": user,
                            "on": bool(action.get("on"))})
            acc = self.engine.ch.acc(user)
            marks = {c: self.engine.mark(c) for c in self.engine.assets}
            av = self.engine.ch.account_value(acc, marks)
            ok, why = self.engine.pm.set_mode(user, bool(action.get("on")),
                                              av)
            return {"status": "ok" if ok else "err", "response": why}
        if t == "pmSupply":
            if int(action.get("amount", 0)) <= 0:
                return {"status": "err", "response": "amount_must_be_positive"}
            if eng is not None:
                eng.append({"t": "pmSupply", "user": user,
                            "asset": action["asset"],
                            "amount": int(action["amount"])})
            ok, why = self.engine.pm.supply(user, action["asset"],
                                            int(action["amount"]))
            return {"status": "ok" if ok else "err", "response": why}
        if t == "pmBorrow":
            if int(action.get("amount", 0)) <= 0:
                return {"status": "err", "response": "amount_must_be_positive"}
            if eng is not None:
                eng.append({"t": "pmBorrow", "user": user,
                            "asset": action["asset"],
                            "amount": int(action["amount"])})
            ok, why = self.engine.pm.borrow(user, action["asset"],
                                            int(action["amount"]))
            return {"status": "ok" if ok else "err", "response": why}
        if t == "pmRepay":
            if eng is not None:
                eng.append({"t": "pmRepay", "user": user,
                            "asset": action["asset"],
                            "amount": int(action["amount"])})
            ok, why = self.engine.pm.repay(user, action["asset"],
                                           int(action["amount"]))
            return {"status": "ok" if ok else "err", "response": why}
        if t == "approveApiWallet":
            self.api_wallets[action["api_wallet"]] = user
            return {"status": "ok", "response": "approved"}
        if t == "createSubaccount":
            sub = "%s/%s" % (user, action["name"])
            e.ch.acc(sub)
            self.subaccounts.setdefault(user, []).append(sub)
            return {"status": "ok", "response": sub}
        if t == "usdSend":
            if int(action.get("amount", 0)) <= 0:
                return {"status": "err", "response": "amount_must_be_positive"}
            if eng is not None:
                eng.append({"t": "usdSend", "user": user,
                            "destination": action["destination"],
                            "amount": int(action["amount"])})
            dst, amt = action["destination"], int(action["amount"])
            src = e.ch.accounts.get(user)
            if src is None or src.usd < amt:
                return {"status": "err", "response": "insufficient"}
            src.usd -= amt
            e.ch.acc(dst).usd += amt
            return {"status": "ok", "response": "sent"}
        if t == "usdClassTransfer" and self.spot is not None:
            ok = self.spot.usd_class_transfer(user, int(action["amount"]),
                                              action.get("toPerp", True))
            return {"status": "ok" if ok else "err",
                    "response": "transferred" if ok else "no_balance"}
        return {"status": "err", "response": "unknown_action"}

    # ---- events stream (WS-substitute) + HTTP server ----

    def events_since(self, since):
        return [ev for ev in self.events if ev["tid"] > since]


def make_server(api_state, chain=None, host="127.0.0.1", port=0):
    """ThreadingHTTPServer with the real surface: GET / (trading UI),
    POST /info, POST /exchange, GET /events?since=N, GET /chain/latest,
    GET /block/{h}, GET /health. Port 0 = ephemeral (for tests)."""
    import os
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "static")
    static_path = os.path.join(static_dir, "index.html")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _resp(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _html(self, code, body):
            self.send_response(code)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _ws_upgrade(self):
            """specs/15: the real typed subscription protocol.
            Client: {"method":"subscribe","subscription":{...}} (masked frames).
            Server pushes per-sub payloads (unmasked) every tick."""
            import base64 as b64
            import hashlib
            key = self.headers.get("Sec-WebSocket-Key")
            if not key:
                return self._json(400, {"error": "no_ws_key"})
            accept = b64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")
                             .encode()).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()

            class Frame:
                pass

            def read_client_frame():
                hdr = b""
                while len(hdr) < 2:
                    c = self.rfile.read(2 - len(hdr))
                    if not c:
                        raise ConnectionError("closed")
                    hdr += c
                op = hdr[0] & 0x0F
                masked = hdr[1] & 0x80
                ln = hdr[1] & 0x7F
                if ln == 126:
                    ext = self.rfile.read(2)
                    ln = int.from_bytes(ext, "big")
                elif ln == 127:
                    ext = self.rfile.read(8)
                    ln = int.from_bytes(ext, "big")
                mask = self.rfile.read(4) if masked else None
                payload = self.rfile.read(ln) if ln else b""
                if mask:
                    payload = bytes(b ^ mask[i % 4]
                                    for i, b in enumerate(payload))
                return op, payload

            subs = []

            def snapshot(sub):
                e = api_state.engine
                st = sub["type"]
                if st == "allMids":
                    out = {}
                    for coin in sorted(e.assets):
                        b2 = e.books[coin]
                        px = b2.mid() or b2.last_trade or e.mark(coin)
                        if px is not None:
                            out[coin] = fmt_px(px)
                    return {"channel": "allMids", "data": out}
                if st == "l2Book":
                    b2 = e.books.get(sub.get("coin", "BTC"))
                    if b2 is None:
                        return None
                    lvls = [
                        [[fmt_px(px), str(sum(o.sz_rem for o in b2.bids[px])
                                          / 1e8), len(b2.bids[px])]
                          for px in sorted(b2.bids, reverse=True)[:20]],
                        [[fmt_px(px), str(sum(o.sz_rem for o in b2.asks[px])
                                          / 1e8), len(b2.asks[px])]
                          for px in sorted(b2.asks)[:20]]]
                    return {"channel": "l2Book", "coin": sub.get("coin"),
                            "data": {"coin": sub.get("coin"),
                                     "time": int(time.time() * 1000),
                                     "levels": lvls}}
                if st == "trades":
                    t = [{"coin": ev["coin"], "px": fmt_px(ev["px"]),
                          "sz": str(ev["sz"] / 1e8), "side": "B" if ev["is_buy"] else "A",
                          "time": ev["ts"]}
                         for ev in api_state.events[-30:] if ev.get("t") == "fill"]
                    return {"channel": "trades", "data": t}
                if st == "candle":
                    t = [{"coin": ev["coin"], "px": ev["px"], "sz": ev["sz"],
                          "ts": ev["ts"]} for ev in api_state.events[-60:]
                         if ev.get("t") == "fill" and ev["coin"] == sub.get("coin")]
                    out = {}
                    for x in t:
                        m = x["ts"] * 1000 // 60000 * 60000
                        c = out.setdefault(m, [x["px"], x["px"], x["px"], x["px"]])
                        c[1] = max(c[1], x["px"]); c[2] = min(c[2], x["px"])
                        c[3] = x["px"]
                    return {"channel": "candle", "data": [
                        {"t": m, "o": c[0], "h": c[1], "l": c[2], "c": c[3]}
                        for m, c in sorted(out.items())]}
                if st in ("userEvents", "orderUpdates", "userFills"):
                    u = sub.get("user")
                    evs = [dict(ev) for ev in api_state.events
                           if ev.get("t") == "fill"
                           and (ev.get("taker") == u or ev.get("maker") == u)]
                    return {"channel": st, "data": evs[-20:]}
                if st == "clearinghouseState":
                    s = api_state.info({"type": "userState",
                                        "user": sub.get("user")})
                    return {"channel": "clearinghouseState", "data": s}
                return None

            try:
                while True:
                    op, payload = read_client_frame()
                    if op == 8:
                        return
                    if op == 9:  # ping -> pong
                        self.wfile.write(_ws_frame_bytes(0x8A, payload))
                        continue
                    if op != 1:
                        continue
                    try:
                        msg = json.loads(payload)
                    except Exception:
                        continue
                    method = msg.get("method")
                    sub = msg.get("subscription", {})
                    if method == "subscribe":
                        subs.append(sub)
                        self.wfile.write(_ws_frame_bytes(
                            0x81, json.dumps({"channel": "subscriptionResult",
                                              "sub": sub})))
                    elif method == "unsubscribe":
                        subs = [s for s in subs
                                if s.get("type") != sub.get("type")]
                    with api_state.lock:
                        for sub in subs:
                            snap = snapshot(sub)
                            if snap is not None:
                                self.wfile.write(_ws_frame_bytes(
                                    0x81, json.dumps(snap, separators=(",", ":"))))
                    time.sleep(0.3)
            except Exception:
                return

        def do_GET(self):
            if self.path == "/ws":
                self._ws_upgrade()
                return
            if self.path == "/static/lwc.js":
                with open(static_dir + "/lwc.js", "rb") as fp:
                    return self._resp(200, fp.read(), "application/javascript")
            if self.path == "/" or self.path.startswith("/index"):
                with open(static_path, "rb") as fp:
                    return self._html(200, fp.read())
            if self.path == "/health":
                return self._json(200, {"status": "ok"})
            if self.path.startswith("/candles"):
                coin = "BTC"
                if "coin=" in self.path:
                    coin = self.path.split("coin=")[1].split("&")[0]
                return self._json(200,
                                  {"candles": getattr(api_state, "candles", {})
                                   .get(coin, [])})
            if self.path == "/chain/latest":
                if chain is None:
                    return self._json(404, {"error": "no_chain"})
                return self._json(200, {"header": chain.head().header})
            if self.path.startswith("/block/"):
                if chain is None:
                    return self._json(404, {"error": "no_chain"})
                try:
                    h = int(self.path.split("/block/")[1].split("?")[0])
                except ValueError:
                    return self._json(400, {"error": "bad_block"})
                return self._json(200, chain.to_json(h))
            if self.path.startswith("/events"):
                since = 0
                if "since=" in self.path:
                    since = int(self.path.split("since=")[1].split("&")[0])
                with api_state.lock:
                    return self._json(200,
                                      {"events": api_state.events_since(since)})
            return self._json(404, {"error": "not_found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._json(400, {"error": "bad_json"})
            if self.path == "/info":
                try:
                    with api_state.lock:
                        return self._json(200, api_state.info(req))
                except Exception:
                    return self._json(400, {"error": "bad_request"})
            if self.path == "/exchange":
                try:
                    return self._json(200, api_state.exchange(req))
                except Exception:
                    return self._json(400, {"error": "bad_request"})
            return self._json(404, {"error": "not_found"})

    srv = ThreadingHTTPServer((host, port), Handler)
    return srv
