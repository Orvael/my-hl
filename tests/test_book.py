# Created: WIB 2026-10-08 14:2x — CLOB tests (my-hl)
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hl.book import OrderBook
from hl.types import Order

S = 10**8
FAILS = []


def mk(oid, user, is_buy, px_usd, sz, tif='GTC'):
    return Order(oid, user, 'BTC', is_buy, px_usd * S, sz, sz, tif)


def check(cond, label):
    if not cond:
        FAILS.append(label)
    return cond


def end():
    if FAILS:
        print('FAILS:', FAILS)
        sys.exit(1)
    print('test_book OK')
    FAILS.clear()


def test_price_priority():
    ob = OrderBook('BTC')
    check(ob.place(mk(1, 'm1', True, 99, S))[0] == 'rested', 'pp-rest1')
    check(ob.place(mk(2, 'm2', True, 100, S))[0] == 'rested', 'pp-rest2')
    st, fills = ob.place(mk(3, 't1', False, 95, 2 * S, 'IOC'))
    check(st == 'filled', 'pp-status')
    check([f['px'] for f in fills] == [100 * S, 99 * S], 'pp-order')
    check([f['maker_user'] for f in fills] == ['m2', 'm1'], 'pp-users')


def test_fifo_and_tifs():
    ob = OrderBook('BTC')
    ob.place(mk(4, 'f1', True, 100, S))
    ob.place(mk(5, 'f2', True, 100, S))
    st, fills = ob.place(mk(6, 't2', False, 100, S, 'IOC'))
    check(st == 'filled', 'fifo-status')
    check(fills[0]['maker_oid'] == 4, 'fifo-order')
    st, _ = ob.place(mk(7, 't3', False, 100, S, 'ALO'))
    check(st == 'canceled', 'alo-cross')
    ob.place(mk(8, 'f3', True, 100, S))
    st, _ = ob.place(mk(9, 'x1', True, 100, S, 'ALO'))
    check(st == 'rested', 'alo-rest')
    st, fills = ob.place(mk(10, 't4', False, 100, 5 * S, 'IOC'))
    check(st == 'canceled', 'ioc-partial')
    check(len(fills) == 3, 'ioc-fills')
    check([f['maker_oid'] for f in fills] == [5, 8, 9], 'ioc-order')
    check(ob.side_sz(True) == 0, 'ioc-side-empty')
    end()


def test_stp_cancel_conserve():
    ob2 = OrderBook('BTC')
    ob2.place(mk(11, 'u', True, 100, 2 * S))
    st, fills = ob2.place(mk(12, 'u', False, 100, S, 'IOC'))
    check(st == 'canceled', 'stp-stop')
    check(fills == [], 'stp-nofill')
    ob3 = OrderBook('BTC')
    for i in range(6):
        ob3.place(mk(20 + i, 'm%d' % i, True, 99 + i, S))
    st, fills = ob3.place(mk(30, 't', False, 101, 4 * S, 'GTC'))
    check(st == 'filled', 'con-rest')
    check(sum(f['sz'] for f in fills) == 4 * S, 'con-sum')
    check(ob3.side_sz(True) == 2 * S, 'con-side')
    check(ob3.cancel(21), 'con-cancel')
    check(not ob3.cancel(999), 'con-cancel-miss')
    end()


