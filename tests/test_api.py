# Created: WIB 2026-10-08 18:0x — API layer tests: real HTTP round-trips (my-hl)
import sys, os, json, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hl.engine import Engine
from hl.api import ApiState, make_server
from hl.config import SCALE

S = SCALE
FAILS = []


def check(cond, label):
    if not cond:
        FAILS.append(label)
    return cond


def post(url, obj):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def get(url):
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())


def main():
    e = Engine({"BTC": {"max_leverage": 20, "sz_decimals": 2,
                        "impact_notional_usd": 6_000 * S}})
    e.set_oracle("BTC", 100 * S)
    api = ApiState(e)
    srv = make_server(api, port=0)
    port = srv.server_address[1]
    import threading
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = "http://127.0.0.1:%d" % port

    check(get(base + "/health")["status"] == "ok", "api-health")
    meta = post(base + "/info", {"type": "meta"})
    check(meta["universe"][0]["name"] == "BTC", "api-meta")
    e.deposit("lp", 100_000 * S)
    e.deposit("trader1", 100_000 * S)
    e.place("lp", "BTC", True, 99 * S, 500 * S, "ALO")
    e.place("lp", "BTC", False, 101 * S, 500 * S, "ALO")
    mids = post(base + "/info", {"type": "allMids"})
    check(mids["BTC"] == "100.0", "api-mids")
    book = post(base + "/info", {"type": "l2Book", "coin": "BTC"})
    check(book["levels"][0][0][0] == "99.0", "api-l2-bid")
    check(book["levels"][1][0][0] == "101.0", "api-l2-ask")
    # place a real order over HTTP
    r = post(base + "/exchange", {
        "action": {"type": "order", "orders": [
            {"coin": "BTC", "is_buy": True, "limit_px": 99 * S, "sz": 10 * S,
             "tif": "GTC"}]},
        "nonce": 1, "signature": {"signer": "trader1"}})
    check(r["status"] == "ok", "api-order-ok")
    st = post(base + "/info", {"type": "userState", "user": "trader1"})
    check(st["assetPositions"] == [], "api-no-position-resting")
    oo = post(base + "/info", {"type": "openOrders", "user": "trader1"})
    check(len(oo) == 1 and oo[0]["side"] == "B", "api-open-orders")
    # nonce replay rejected
    r2 = post(base + "/exchange", {
        "action": {"type": "order", "orders": []}, "nonce": 1,
        "signature": {"signer": "trader1"}})
    check(r2["status"] == "err", "api-nonce-replay")
    # taker order over HTTP fills against the lp ask
    r3 = post(base + "/exchange", {
        "action": {"type": "order", "orders": [
            {"coin": "BTC", "is_buy": True, "limit_px": 101 * S, "sz": 10 * S,
             "tif": "IOC"}]},
        "nonce": 2, "signature": {"signer": "trader1"}})
    check(r3["status"] == "ok", "api-taker-ok")
    fills = post(base + "/info", {"type": "userFills", "user": "trader1"})
    check(len(fills) == 1 and fills[0]["px"] == "101.0", "api-user-fills")
    st = post(base + "/info", {"type": "userState", "user": "trader1"})
    check(st["assetPositions"][0]["position"]["szi"] == "10.0", "api-position")
    check(st["assetPositions"][0]["position"]["leverage"]["type"] == "cross",
          "api-lev-cross")
    # builder fee: order with builder gets the extra bps charged
    e.deposit("t2", 100_000 * S)
    r4 = post(base + "/exchange", {
        "action": {"type": "order",
                   "orders": [{"coin": "BTC", "is_buy": False,
                               "limit_px": 99 * S, "sz": 10 * S, "tif": "IOC"}],
                   "builder": {"b": "builderx", "f": 10}},  # 10 bps
        "nonce": 3, "signature": {"signer": "t2"}})
    check(r4["status"] == "ok", "api-builder-ok")
    check(api.engine.ch.acc("builderx").usd > 0, "api-builder-earned")
    # api wallet acts for its master
    post(base + "/exchange", {"action": {"type": "approveApiWallet",
                                         "api_wallet": "bot1"},
                              "nonce": 10, "signature": {"signer": "t2"}})
    r5 = post(base + "/exchange", {
        "action": {"type": "usdSend", "destination": "friend", "amount": 5 * S},
        "nonce": 11, "signature": {"signer": "bot1"}})
    check(r5["status"] == "ok", "api-wallet-acts")
    check(e.ch.acc("friend").usd == 5 * S, "api-usd-send")
    # subaccount
    r6 = post(base + "/exchange", {"action": {"type": "createSubaccount",
                                              "name": "sub1"},
                                   "nonce": 12,
                                   "signature": {"signer": "t2"}})
    check(r6["status"] == "ok" and r6["response"] == "t2/sub1", "api-sub")
    # events stream
    evs = get(base + "/events?since=0")["events"]
    check(any(x["t"] == "fill" for x in evs), "api-events")
    # rate limit: hammer past 100 in the window
    last = None
    for i in range(150):
        last = post(base + "/exchange", {"action": {"type": "order",
                                                    "orders": []},
                                         "nonce": 1000 + i,
                                         "signature": {"signer": "hammer"}})
    check(last["status"] == "err" and last["response"] == "rate_limit",
          "api-rate-limit")
    srv.shutdown()
    if FAILS:
        print("FAILS:", FAILS)
        sys.exit(1)
    print("test_api OK")


main()
