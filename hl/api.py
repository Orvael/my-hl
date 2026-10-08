# Created: WIB 2026-10-08 17:5x — API layer (my-hl, specs/12)
# Real surface shapes from the live info-endpoint/signing docs (fetched 10-08).
# Substitutions (documented): signature = stub envelope with nonce replay
# protection (real HL = EIP-712 eth keys); WS subscriptions = /events long-poll
# stream with the same payloads. The ENGINE stays untouched and deterministic;
# only the API layer uses wall time (rate limiting).
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .num import qdiv
from .config import SCALE

MAX_NONCES = 10_000


def fmt_px(px):
    return str(qdiv(px, 10 ** 6) / 10 ** 2) if px is not None else None


class ApiState:
    """Server state around one Engine. All /info reads + /exchange writes."""

    def __init__(self, engine, spot=None):
        self.engine = engine
        self.spot = spot
        self.lock = threading.RLock()
        self.used_nonces = set()
        self.api_wallets = {}       # api_addr -> master_addr
        self.builders = {}          # builder_addr -> earned usd
        self.subaccounts = {}       # master -> [sub names]
        self.events = []            # ring buffer (fills, funding, liq, ...)
        self.rate = {}              # user -> (window_ts, count)
        self.rate_limit = 100       # requests per window
        self.rate_window_s = 10

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
        if t == "l2Book":
            coin = req["coin"]
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
            return self._user_state(req["user"])
        if t == "openOrders":
            return self._open_orders(req["user"])
        if t == "userFills":
            return self._user_fills(req["user"])
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
            return self._dispatch(user, req.get("action", {}))

    def _dispatch(self, user, action):
        e = self.engine
        t = action.get("type")
        if t == "order":
            builder = action.get("builder")
            results = []
            for o in action.get("orders", []):
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
            ok = e.books[action["coin"]].cancel(action["oid"])
            return {"status": "ok" if ok else "err",
                    "response": "canceled" if ok else "unknown_oid"}
        if t == "approveApiWallet":
            self.api_wallets[action["api_wallet"]] = user
            return {"status": "ok", "response": "approved"}
        if t == "createSubaccount":
            sub = "%s/%s" % (user, action["name"])
            e.ch.acc(sub)
            self.subaccounts.setdefault(user, []).append(sub)
            return {"status": "ok", "response": sub}
        if t == "usdSend":
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


def make_server(api_state, host="127.0.0.1", port=0):
    """ThreadingHTTPServer with the real surface: POST /info, POST /exchange,
    GET /events?since=N, GET /health. Port 0 = ephemeral (for tests)."""

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

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._json(400, {"error": "bad_json"})
            if self.path == "/info":
                with api_state.lock:
                    return self._json(200, api_state.info(req))
            if self.path == "/exchange":
                return self._json(200, api_state.exchange(req))
            return self._json(404, {"error": "not_found"})

        def do_GET(self):
            if self.path == "/health":
                return self._json(200, {"status": "ok"})
            if self.path.startswith("/events"):
                since = 0
                if "since=" in self.path:
                    since = int(self.path.split("since=")[1].split("&")[0])
                with api_state.lock:
                    return self._json(200, {"events": api_state.events_since(since)})
            return self._json(404, {"error": "not_found"})

    srv = ThreadingHTTPServer((host, port), Handler)
    return srv
