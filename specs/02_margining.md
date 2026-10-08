# SPEC — Margining (engine rule)

Source: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining.md
Fetched + verified verbatim WIB 2026-10-08 13:5x.

## Quoted rules
- Cross margin default; isolated supported; some assets strict-isolated (not v1).
- "The margin required to open a position is `position_size * mark_price / leverage`."
- Leverage = any integer 1..max_leverage. Checked ON OPENING only.
- "Unrealized pnl for cross margin positions will automatically be available as initial margin."
- Liq trigger (cross): "account value (including unrealized pnl) is less than the
  maintenance margin [rate] times the total open notional position".
- "The maintenance margin is currently set to half of the initial margin at max leverage."
  -> maint_rate = 0.5 / max_leverage  (1.25% at 40x, 16.7% at 3x).
- Isolated: same trigger with isolated margin + isolated notional only.
- Withdraw/transfer rule: `transfer_margin_required = max(initial_margin_required, 0.1 * total_position_value)`.

## Engine mapping (v1)
- account_value = usd_balance + sum side*(mark-entry)*szi (marks = oracle input, v1 simplification).
- initial_req = sum notional/max_leverage; available = account_value - initial_req.
- Open check: available - new_notional/max_leverage >= 0, else reject action.
- Withdraw check: remaining >= max(initial_req, qdiv(total_notional, 10)).
- maint_rate per asset = qdiv(SCALE, 2*max_leverage).
- Isolated: NOT in v1 (cross only). Marked as v2.
