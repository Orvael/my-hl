# Created: WIB 2026-10-08 14:03
# WIB 2026-10-08 14:0x — arithmetic helpers, integer-only (Hyperliquid imitation my-hl)
from .config import SCALE

def qdiv(n, d):
    """Integer division truncated toward zero (deterministic, signed-safe)."""
    q_, r = divmod(n, d)
    if r != 0 and (n < 0) != (d < 0):
        q_ += 1
    return q_

def notional(px, sz):
    """px (1e8) scaled * sz (1e8) scaled -> USD in 1e8 units."""
    return qdiv(px * sz, SCALE)

def wavg(px_a, sz_a, px_b, sz_b):
    """Size-weighted average price, int scaled."""
    return qdiv(px_a * sz_a + px_b * sz_b, sz_a + sz_b)

def clamp(x, lo, hi):
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
