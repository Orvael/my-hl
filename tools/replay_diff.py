# Created: WIB 2026-10-08 19:1x — replay real HL data through our engine (my-hl)
# Streams recorder JSONL; feeds real l2 snapshots + trades through OUR OrderBook
# and measures how closely our matching reproduces reality.
# Memory: streaming, one file pass, no accumulation.
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hl.book import OrderBook          # noqa: E402
from hl.types import Order             # noqa: E402

S = 10 ** 8


def to_int(px_str):
    return int(round(float(px_str) * S))


def rebuild(ob, levels):
    """Reset our book from a real l2 snapshot: levels = [bids, asks]."""
    ob.bids.clear()
    ob.asks.clear()
    ob.orders.clear()
    from collections import deque
    for side_dict, rows, is_buy in ((ob.bids, levels[0], True),
                                    (ob.asks, levels[1], False)):
        for i, row in enumerate(rows):
            px = to_int(row["px"])
            dq = deque()
            o = Order(i + 1 if is_buy else 10_000 + i, "real", "BTC",
                      is_buy, px, S, int(round(float(row["sz"]) * S)), "ALO")
            o.sz_rem = int(round(float(row["sz"]) * S))
            dq.append(o)
            side_dict[px] = dq
            ob.orders[o.oid] = o


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "hldata_test"
    d = sys.argv[2] if len(sys.argv) > 2 else None
    files = []
    if d:
        files = sorted(os.path.join(d, f) for f in os.listdir(d)
                       if f.endswith(".jsonl"))
    else:
        files = [path]
    ob = OrderBook("BTC")
    trades_total = no_fill = exact = near = 0
    slip_bps_sum = 0.0
    slip_max = 0.0
    syncs = 0
    last_mid = None
    for path in files:
        with open(path) as fp:
            for line in fp:
                rec = json.loads(line)
                ch, data = rec["ch"], rec["d"]
                if ch == "l2Book":
                    rebuild(ob, data["levels"])
                    bb = max(ob.bids) if ob.bids else None
                    ba = min(ob.asks) if ob.asks else None
                    if bb and ba:
                        last_mid = (bb + ba) // 2
                    syncs += 1
                elif ch == "trades":
                    for t in data:
                        px = to_int(t["px"])
                        sz = int(round(float(t["sz"]) * S))
                        is_buy = last_mid is None or px >= last_mid
                        o = Order(999_999, "replay", "BTC", is_buy, px, sz, sz,
                                  "IOC")
                        st, fills = ob.place(o)
                        got = sum(f["sz"] for f in fills)
                        if not fills:
                            no_fill += 1
                            continue
                        wavg_px = sum(f["px"] * f["sz"]
                                      for f in fills) // max(got, 1)
                        slip = abs(wavg_px - px) / S / float(t["px"]) * 10_000
                        slip_bps_sum += slip
                        slip_max = max(slip_max, slip)
                        if slip < 0.5:
                            exact += 1
                        else:
                            near += 1
                        trades_total += 1
    print("snapshots synced : %d" % syncs)
    print("real trades      : %d (no-fill %d)" % (trades_total + no_fill,
                                                 no_fill))
    if trades_total:
        print("exact match <0.5bp: %d (%.1f%%)" % (
            exact, 100.0 * exact / trades_total))
        print("mean slip        : %.4f bps" % (slip_bps_sum / trades_total))
        print("max slip         : %.4f bps" % slip_max)
    print("engine state     : bids %d levels, asks %d levels" % (
        len(ob.bids), len(ob.asks)))


if __name__ == "__main__":
    main()
