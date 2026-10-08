<!-- Created: WIB 2026-10-08 14:01 -->
# SPEC — Entry price & PnL (engine rules)

Source: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/entry-price-and-pnl.md
Fetched + verified WIB 2026-10-08 13:5x.

## Quoted rules
- "entry price, unrealized pnl, and closed pnl are purely frontend components... The fundamental
  accounting is based on margin checks (balance for spot) and trades."
- Opening trade = the absolute value of the position increases.
- Opening: entry updated to size-weighted average of current entry and trade price.
- Closing: entry unchanged.
- upnl = side * (mark - entry) * position_size, side = 1 long / -1 short.
- Closed pnl = fee + side * (mark - entry) * position_size (closing trades only).

## Engine mapping (v1)
- Close fill: realized = qdiv(side*(px_fill - entry)*sz, SCALE) to balance, fee subtracted.
- Entry frozen on close; weighted-average on open; flip = close at old entry, remainder opens at fill px.
- Balance conservation is the binding invariant; entry/upnl are derived views for reports.
