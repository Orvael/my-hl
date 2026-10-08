# Created: WIB 2026-10-08 22:4x — engine benchmark (my-hl)
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hl.book import OrderBook
from hl.types import Order
from hl.engine import Engine
from hl.journal import state_hash
from hl.config import SCALE

S = SCALE
N = 4000
ob = OrderBook("BTC")
t0 = time.time()
for i in range(N):
    px = int((90 + (i % 2000) * 0.01) * S)
    ob.place(Order(i + 1, "u%d" % (i % 50), "BTC", i % 2 == 0, px, S, S, "GTC"))
t1 = time.time()
for i in range(N):
    ob.place(Order(N + i + 1, "taker", "BTC", i % 2 == 1,
                   int(105 * S), 5 * S, 5 * S, "IOC"))
t2 = time.time()
e = Engine({"BTC": {"max_leverage": 20, "sz_decimals": 2,
                    "impact_notional_usd": 6_000 * S}})
t3 = time.time()
for i in range(200):
    e.apply_block(i, [{"t": "oracle", "coin": "BTC", "px": (100 + i) * S},
                      {"t": "deposit", "user": "a", "usd": 100 * S}])
t4 = time.time()
h = state_hash(e)
t5 = time.time()
print("book place : %8.0f orders/sec" % (N / (t1 - t0)))
print("book match : %8.0f orders/sec" % (N / (t2 - t1)))
print("blocks     : %8.0f blocks/sec (200)" % (200 / (t4 - t3)))
print("state hash : %8.0f hashes/sec" % (1 / (t5 - t4)))
