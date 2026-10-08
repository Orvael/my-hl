# SPEC — Liquidations (engine rule)

Source: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations.md
Source exact: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations.md
Fetched + verified WIB 2026-10-08 13:5x.

## Quoted rules
- Trigger: equity < maintenance requirement. Maintenance rate = half of initial at max leverage.
- "positions are first attempted to be entirely closed by sending market orders to the book.
  The orders are for the full size of the position... If the positions are entirely or partially
  closed such that the maintenance margin requirements are met, any remaining collateral remains
  with the trader."
- "If the account equity drops below 2/3 of the maintenance margin without successful liquidation
  through the book, a backstop liquidation happens through the liquidator vault."
- Cross backstop: all cross positions + cross margin transfer to the liquidator vault; a trader
  with no isolated positions ends at zero account equity.
- "During backstop liquidation, the maintenance margin is not returned to the user."
- Partial liquidations: >100k positions send 20% slices with 30s cooldown — NOT in v1.
- Liq price formula: liq_price = price - side * margin_available / position_size / (1 - l * side),
  l = 1/MAINTENANCE_LEVERAGE = 2 * max_leverage, margin_available = account_value - maint_req.

## Engine mapping (v1)
- End-of-block pass, deterministic order (users sorted, positions by notional desc).
- Book liq = full-size market order as taker; fees charged on fills.
- Recheck equity >= maint_req after each forced close; stop when met.
- Backstop if equity < qdiv(2*maint_req, 3): balance + all positions move to vault_hlp as-is.
  Backstop fill pricing NOT specified in docs — v1 transfers unpriced. Documented simplification.
- ADL after that when account value negative (see 05_mechanics).
