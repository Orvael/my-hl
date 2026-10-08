<!-- Created: WIB 2026-10-08 16:08 -->
# SPEC — Trigger orders: TP/SL, stops, TWAP (engine rules)

Sources (fetched + verified WIB 2026-10-08):
- trading/take-profit-and-stop-loss-orders-tp-sl.md, trading/order-types.md

## TP/SL (quoted)
- Mark price triggers TP/SL.
- TP/SL market orders: "slippage tolerance of 10%".
- Limit TP/SL: explicit limit px controls slippage tolerance.
- Position TP/SL: size = entire position at trigger time (not fixed);
  explicit size = fixed-size.
- OCO (parent-order TP/SL): children have fixed size = parent size.
  - parent fully fills at placement -> children placed immediately.
  - parent unfilled -> children untriggered; cancel parent -> cancel children.
  - parent canceled after partial fill -> children fully canceled.
  - parent partially filled then canceled FOR INSUFFICIENT MARGIN ->
    children placed for the FULL parent size.

## Stop/Take orders (order-types.md)
- Stop Market: triggers when mark crosses trigger; for a buy trigger px above
  mid; sell trigger below mid. Take Market: opposite sides.
- Trailing stop: market order on mark retracing from best level since
  activation. Trigger never moves backward.
- TWAP: suborders at >=30s intervals; target = elapsed/total * total size;
  per-suborder 3% max slippage; catch-up suborders capped at 3x the normal
  suborder size; $100 min total. Randomize (±20%) NOT in v1 (no rand in engine).
- Chase: runs in the browser tab -> NOT engine scope. Documented, skipped.

## Engine mapping
- hl/triggers.py: TriggerStore keyed by user. Trigger fields: user, coin,
  is_buy (close side), trigger_px, kind ('tp'|'sl'|'stop'|'take'), order_kind
  ('market'|'limit'), limit_px (or market = ±10% from trigger), sz (None =
  whole position), reduce_only enforced.
- Parent orders carry optional tp/sl children specs (OCO); engine handles
  fill->place / cancel->cancel / insufficient-margin-cancel->place-full.
- TWAP: TwapOrder {user, coin, is_buy, total_sz, start_ts, duration_s,
  triggered_px?}; each block: target = qdiv(elapsed * total, duration);
  while filled < target: send suborder sz = min(3*base, target - filled)
  as IOC with px = mark*(1 ± 3%). base = qdiv(total, duration/30s).
- Trigger pass runs each block BEFORE liquidations, using mark prices.
